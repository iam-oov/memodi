ALTER TABLE observations ADD COLUMN IF NOT EXISTS embedding vector(384);
CREATE INDEX IF NOT EXISTS idx_obs_embedding ON observations USING hnsw (embedding vector_cosine_ops);
