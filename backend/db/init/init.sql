-- =============================================================================
-- Database Initialization Script for Quizzical AI (Local)
-- =============================================================================
-- Idempotent: safe to run multiple times.
-- Creates extensions, types, tables, indexes, and lightweight triggers.
-- Schema aligns with the app’s ORM and persistence plan:
--   - session_history: one row per quiz session (final profile, transcript, feedback,
--     and a JSONB snapshot of the selected character set for LLM judging)
--   - characters: canonical characters (unique by name)
--   - character_session_map: M:N between sessions and characters
--   - session_questions: exactly one row per session; baseline/adaptive stored as JSON blobs
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";     -- uuid_generate_v4()

-- ---------------------------------------------------------------------------
-- Types
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_sentiment_enum') THEN
    CREATE TYPE user_sentiment_enum AS ENUM ('POSITIVE','NEGATIVE','NONE');
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

-- Characters: long-lived profiles, unique by name. Optional quality fields.
CREATE TABLE IF NOT EXISTS characters (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name                TEXT NOT NULL UNIQUE CHECK (name <> ''),
  short_description   TEXT NOT NULL CHECK (short_description <> ''),
  profile_text        TEXT NOT NULL CHECK (profile_text <> ''),
  profile_picture     BYTEA NULL,
  judge_quality_score SMALLINT NULL CHECK (judge_quality_score BETWEEN 1 AND 10),
  judge_feedback      TEXT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Session history: one row per quiz session. Final profile + transcript + feedback live here.
-- Includes character_set JSONB snapshot (array of character objects) for LLM judge on the set.
CREATE TABLE IF NOT EXISTS session_history (
  session_id          UUID PRIMARY KEY,
  category            TEXT NOT NULL CHECK (category <> ''),

  -- Structured synopsis object (title/summary). Required even if minimal.
  category_synopsis   JSONB NOT NULL,

  -- Optional vector embedding (dimension must match the embedder; default 384).
  synopsis_embedding  VECTOR(384) NULL,

  -- Optional planning/analysis artifacts
  agent_plan          JSONB NULL,

  -- Transcript of messages (array). Required even if empty: use [].
  session_transcript  JSONB NOT NULL,

  -- Snapshot of the character set chosen by the agent (array of objects).
  -- Example element: {"name":"...", "short_description":"...", "profile_text":"...", "image_url":null}
  character_set       JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- Final result (profile) once quiz completes; may be NULL until completion.
  final_result        JSONB NULL,

  -- Judge/evaluation (optional)
  judge_plan_score    SMALLINT NULL CHECK (judge_plan_score BETWEEN 1 AND 10),
  judge_plan_feedback TEXT NULL,

  -- User feedback (lives with the final profile)
  user_sentiment      user_sentiment_enum NULL,
  user_feedback_text  TEXT NULL,

  -- Completion flags
  is_completed        BOOLEAN NOT NULL DEFAULT FALSE,
  completed_at        TIMESTAMPTZ NULL,

  -- Full Q/A history on completion (array of {question_index, question_text, answer_text, option_index})
  qa_history          JSONB NULL,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- In case the table existed from a previous init without character_set, add it.
ALTER TABLE session_history
  ADD COLUMN IF NOT EXISTS character_set JSONB NOT NULL DEFAULT '[]'::jsonb;

-- M:N mapping between sessions and characters used in that session.
CREATE TABLE IF NOT EXISTS character_session_map (
  character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  session_id   UUID NOT NULL REFERENCES session_history(session_id) ON DELETE CASCADE,
  PRIMARY KEY (character_id, session_id)
);

-- Exactly one row per session; store baseline and adaptive as single JSON blobs.
CREATE TABLE IF NOT EXISTS session_questions (
  session_id         UUID PRIMARY KEY REFERENCES session_history(session_id) ON DELETE CASCADE,
  baseline_questions JSONB NULL,  -- e.g. {"kind":"baseline","count":N,"questions":[...]}
  adaptive_questions JSONB NULL,  -- e.g. {"kind":"adaptive","count":M,"questions":[...]}
  properties         JSONB NULL,  -- counts, timings, misc
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Vector ANN index for synopsis embedding (cosine). Only effective for non-null rows.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_session_synopsis_embedding_cosine_ivf'
  ) THEN
    CREATE INDEX idx_session_synopsis_embedding_cosine_ivf
      ON session_history USING ivfflat (synopsis_embedding vector_cosine_ops)
      WITH (lists = 100);
  END IF;
END $$;

-- Helpful JSONB GIN indexes on questions blobs
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_session_questions_baseline_gin'
  ) THEN
    CREATE INDEX idx_session_questions_baseline_gin
      ON session_questions
      USING GIN ((baseline_questions) jsonb_path_ops);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_session_questions_adaptive_gin'
  ) THEN
    CREATE INDEX idx_session_questions_adaptive_gin
      ON session_questions
      USING GIN ((adaptive_questions) jsonb_path_ops);
  END IF;
END $$;

-- GIN index on the character_set snapshot for ad-hoc querying
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_session_history_character_set_gin'
  ) THEN
    CREATE INDEX idx_session_history_character_set_gin
      ON session_history
      USING GIN ((character_set) jsonb_path_ops);
  END IF;
END $$;

-- Category lookup can be handy
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_session_history_category'
  ) THEN
    CREATE INDEX idx_session_history_category ON session_history (category);
  END IF;
END $$;

-- Name lookup (unique already exists; add a plain index if the planner prefers)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_characters_name'
  ) THEN
    CREATE INDEX idx_characters_name ON characters (name);
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Triggers to auto-update last_updated_at on UPDATE
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_characters_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_characters_set_updated_at
      BEFORE UPDATE ON characters
      FOR EACH ROW
      EXECUTE FUNCTION set_last_updated_at();
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_session_history_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_session_history_set_updated_at
      BEFORE UPDATE ON session_history
      FOR EACH ROW
      EXECUTE FUNCTION set_last_updated_at();
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_session_questions_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_session_questions_set_updated_at
      BEFORE UPDATE ON session_questions
      FOR EACH ROW
      EXECUTE FUNCTION set_last_updated_at();
  END IF;
END $$;

-- =============================================================================
-- End of init
-- =============================================================================
