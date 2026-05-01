-- backend/db/init/init.sql

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

-- ---------------------------------------------------------------------------
-- Forward-only additive migration: characters.image_url (§7.8)
-- Stores the FAL-generated portrait URL. Nullable; never overwritten if set.
-- ---------------------------------------------------------------------------
ALTER TABLE characters ADD COLUMN IF NOT EXISTS image_url TEXT NULL;

-- =============================================================================
-- §21 — Pre-Computed Topic Knowledge Packs (Phase 1: schema only)
-- =============================================================================
--
-- Forward-only, idempotent additions. ORM mirror lives in
-- backend/app/models/db.py. Application code does NOT yet read or write these
-- tables; that arrives in Phase 2 behind precompute.enabled=false.
--
-- All blocks below use IF NOT EXISTS (or DO $$ ... IF NOT EXISTS guards) so
-- this file remains safe to re-run on an existing database.

-- Required for accent-folded alias matching (§21.3 topic_aliases.alias_normalized).
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------------------
-- New tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS media_assets (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  content_hash      TEXT NOT NULL UNIQUE,
  prompt_hash       TEXT NOT NULL,
  storage_provider  TEXT NOT NULL DEFAULT 'fal',
  storage_uri       TEXT NOT NULL,
  bytes_blob        BYTEA NULL,
  prompt_payload    JSONB NOT NULL,
  evaluator_score   SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  flag_count        SMALLINT NOT NULL DEFAULT 0,
  expires_at        TIMESTAMPTZ NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topics (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug              TEXT NOT NULL UNIQUE,
  display_name      TEXT NOT NULL CHECK (display_name <> ''),
  embedding         VECTOR(384) NULL,
  popularity_rank   SMALLINT NULL,
  current_pack_id   UUID NULL,
  flag_count        SMALLINT NOT NULL DEFAULT 0,
  policy_status     TEXT NOT NULL DEFAULT 'allowed',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topic_aliases (
  alias_normalized  TEXT NOT NULL,
  topic_id          UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  display_alias     TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (alias_normalized, topic_id)
);

CREATE TABLE IF NOT EXISTS synopses (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id          UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  content_hash      TEXT NOT NULL UNIQUE,
  body              JSONB NOT NULL,
  image_asset_id    UUID NULL REFERENCES media_assets(id) ON DELETE SET NULL,
  evaluator_score   SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  evaluator_notes   TEXT NULL,
  flag_count        SMALLINT NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS character_sets (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  composition_hash  TEXT NOT NULL UNIQUE,
  composition       JSONB NOT NULL,
  evaluator_score   SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS baseline_question_sets (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  composition_hash  TEXT NOT NULL UNIQUE,
  composition       JSONB NOT NULL,
  evaluator_score   SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS questions (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  text_hash         TEXT NOT NULL UNIQUE,
  text              TEXT NOT NULL,
  options           JSONB NOT NULL,
  kind              TEXT NOT NULL,
  image_asset_id    UUID NULL REFERENCES media_assets(id) ON DELETE SET NULL,
  evaluator_score   SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  requires_factual_check BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topic_packs (
  id                        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id                  UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  version                   SMALLINT NOT NULL,
  status                    TEXT NOT NULL,
  synopsis_id               UUID NOT NULL REFERENCES synopses(id) ON DELETE RESTRICT,
  character_set_id          UUID NOT NULL REFERENCES character_sets(id) ON DELETE RESTRICT,
  baseline_question_set_id  UUID NOT NULL REFERENCES baseline_question_sets(id) ON DELETE RESTRICT,
  evaluator_score           SMALLINT NULL CHECK (evaluator_score BETWEEN 1 AND 10),
  evaluator_report          JSONB NULL,
  model_provenance          JSONB NOT NULL,
  cost_cents                SMALLINT NOT NULL DEFAULT 0,
  built_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at              TIMESTAMPTZ NULL,
  retired_at                TIMESTAMPTZ NULL,
  built_in_env              TEXT NOT NULL,
  CONSTRAINT uq_topic_packs_topic_version UNIQUE (topic_id, version)
);

-- topics.current_pack_id FK is added separately so the table can be created
-- before topic_packs exists (chicken-and-egg between the two tables).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_topics_current_pack_id'
  ) THEN
    ALTER TABLE topics
      ADD CONSTRAINT fk_topics_current_pack_id
      FOREIGN KEY (current_pack_id) REFERENCES topic_packs(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS content_flags (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  target_kind       TEXT NOT NULL,
  target_id         TEXT NOT NULL,
  reason_code       TEXT NOT NULL,
  reason_text       TEXT NULL,
  client_ip_hash    TEXT NOT NULL,
  resolved_at       TIMESTAMPTZ NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS precompute_jobs (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id          UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  status            TEXT NOT NULL DEFAULT 'queued',
  attempt           SMALLINT NOT NULL DEFAULT 0,
  tier              TEXT NULL,
  cost_cents        SMALLINT NOT NULL DEFAULT 0,
  evaluator_history JSONB NULL,
  error_text        TEXT NULL,
  delayed_until     TIMESTAMPTZ NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evaluator_training_examples (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  artefact_kind     TEXT NOT NULL,
  artefact_payload  JSONB NOT NULL,
  operator_score    SMALLINT NOT NULL CHECK (operator_score BETWEEN 1 AND 10),
  operator_notes    TEXT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embeddings_cache (
  text_hash         TEXT PRIMARY KEY,
  model             TEXT NOT NULL,
  dim               SMALLINT NOT NULL,
  embedding         VECTOR(384) NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  actor_id          TEXT NOT NULL,
  action            TEXT NOT NULL,
  target_kind       TEXT NOT NULL,
  target_id         TEXT NOT NULL,
  before_hash       TEXT NULL,
  after_hash        TEXT NULL,
  extra             JSONB NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Additive precompute columns on existing characters table (§21.3)
-- ---------------------------------------------------------------------------
ALTER TABLE characters ADD COLUMN IF NOT EXISTS canonical_key TEXT NULL;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS embedding VECTOR(384) NULL;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS evaluator_score SMALLINT NULL
  CHECK (evaluator_score IS NULL OR (evaluator_score BETWEEN 1 AND 10));
ALTER TABLE characters ADD COLUMN IF NOT EXISTS flag_count SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS image_asset_id UUID NULL
  REFERENCES media_assets(id) ON DELETE SET NULL;
ALTER TABLE characters ADD COLUMN IF NOT EXISTS policy_status TEXT NOT NULL DEFAULT 'allowed';

-- §21 Phase 12 — defer blob upload when source not yet reachable.
ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS pending_rehost BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill canonical_key for legacy rows (idempotent; no-op once populated).
UPDATE characters
   SET canonical_key = lower(unaccent(name))
 WHERE canonical_key IS NULL;

-- ---------------------------------------------------------------------------
-- Indexes (idempotent)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_topics_embedding_cosine_ivf') THEN
    CREATE INDEX idx_topics_embedding_cosine_ivf
      ON topics USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_topics_popularity_rank') THEN
    CREATE INDEX idx_topics_popularity_rank
      ON topics (popularity_rank DESC NULLS LAST);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_topic_packs_topic_status') THEN
    CREATE INDEX idx_topic_packs_topic_status ON topic_packs (topic_id, status);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_topic_packs_status_published_at') THEN
    CREATE INDEX idx_topic_packs_status_published_at
      ON topic_packs (status, published_at DESC);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_synopses_topic_id') THEN
    CREATE INDEX idx_synopses_topic_id ON synopses (topic_id);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_media_assets_prompt_hash') THEN
    CREATE INDEX idx_media_assets_prompt_hash ON media_assets (prompt_hash);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_media_assets_pending_rehost') THEN
    CREATE INDEX idx_media_assets_pending_rehost ON media_assets (pending_rehost) WHERE pending_rehost;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_content_flags_client_ip_hash') THEN
    CREATE INDEX idx_content_flags_client_ip_hash ON content_flags (client_ip_hash);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_content_flags_target') THEN
    CREATE INDEX idx_content_flags_target ON content_flags (target_kind, target_id);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_precompute_jobs_topic_id') THEN
    CREATE INDEX idx_precompute_jobs_topic_id ON precompute_jobs (topic_id);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_characters_canonical_key') THEN
    CREATE INDEX idx_characters_canonical_key ON characters (canonical_key);
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Triggers for new tables that have last_updated_at columns
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_topics_set_updated_at') THEN
    CREATE TRIGGER trg_topics_set_updated_at
      BEFORE UPDATE ON topics FOR EACH ROW
      EXECUTE FUNCTION set_last_updated_at();
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_precompute_jobs_set_updated_at') THEN
    CREATE TRIGGER trg_precompute_jobs_set_updated_at
      BEFORE UPDATE ON precompute_jobs FOR EACH ROW
      EXECUTE FUNCTION set_last_updated_at();
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- audit_log: append-only at the database layer.
-- The application role must not be able to UPDATE or DELETE rows. We REVOKE
-- those rights from PUBLIC; the deploy script grants only INSERT + SELECT to
-- the application role. (No-op if the role grants haven't run yet.)
-- ---------------------------------------------------------------------------
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- §21 Phase 4 — `mv_topic_pack_resolved` materialised view (`AC-PRECOMP-PERF-1`).
-- Hot-path resolver collapses the topic→pack→synopsis/charset/qset JOIN into a
-- single MV row. Refreshed CONCURRENTLY from `publish()` after the atomic swap
-- so reads never block. SQLite test bench skips the MV (uses plain JOIN).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_matviews WHERE matviewname = 'mv_topic_pack_resolved'
  ) THEN
    EXECUTE $mv$
      CREATE MATERIALIZED VIEW mv_topic_pack_resolved AS
      SELECT
        t.id                            AS topic_id,
        t.slug                          AS slug,
        t.display_name                  AS display_name,
        tp.id                           AS pack_id,
        tp.version                      AS version,
        tp.synopsis_id                  AS synopsis_id,
        tp.character_set_id             AS character_set_id,
        tp.baseline_question_set_id     AS baseline_question_set_id,
        tp.evaluator_score              AS evaluator_score,
        tp.published_at                 AS published_at
      FROM topics t
      JOIN topic_packs tp ON tp.id = t.current_pack_id
      WHERE tp.status = 'published'
    $mv$;
    -- Required for CONCURRENT refresh.
    CREATE UNIQUE INDEX uq_mv_topic_pack_resolved_topic_id
      ON mv_topic_pack_resolved (topic_id);
  END IF;
END $$;

-- =============================================================================
-- End of §21 schema additions
-- =============================================================================
