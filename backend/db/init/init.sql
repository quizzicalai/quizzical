-- =============================================================================
-- Database Initialization Script for Quizzical AI
-- =============================================================================
-- This script is executed automatically by the Postgres Docker container on its
-- first run. It sets up the necessary extensions and tables for the application.
-- Using 'IF NOT EXISTS' ensures that this script is idempotent and can be
-- safely run multiple times without causing errors.
-- =============================================================================

-- Step 1: Enable the pgvector extension
-- This is crucial for storing and querying vector embeddings, which are used
-- for the Retrieval-Augmented Generation (RAG) feature.
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================

-- Step 2: Create the 'session_history' table
-- This table stores the conversation history for each quiz session, along with
-- the vector embedding of the quiz synopsis for similarity searches.
CREATE TABLE IF NOT EXISTS session_history (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    interaction_index INTEGER NOT NULL,
    human_message TEXT,
    ai_message TEXT,
    -- The vector embedding's dimension (384) must match the embedding model's output.
    synopsis_embedding VECTOR(384), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Step 3: Create an index for efficient vector similarity searches
-- An IVFFlat index is a good balance between speed and accuracy for vector queries.
-- Using 'vector_cosine_ops' aligns with the distance metric specified in the app config.
CREATE INDEX IF NOT EXISTS session_history_synopsis_embedding_idx 
ON session_history 
USING ivfflat (synopsis_embedding vector_cosine_ops) WITH (lists = 100);

-- =============================================================================

-- Step 4: Create the 'characters' table
-- This table stores the AI-generated characters that are part of a quiz.
CREATE TABLE IF NOT EXISTS characters (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    short_description TEXT,
    profile_text TEXT,
    image_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================

-- Step 5: Create the 'feedback' table
-- This table collects user feedback (ratings and comments) for each quiz session.
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    rating VARCHAR(10) NOT NULL, -- Storing as VARCHAR, e.g., 'up' or 'down'
    comment TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
