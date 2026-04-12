ALTER TABLE observations ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS duplicate_count INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_obs_content_hash
    ON observations(project_id, content_hash)
    WHERE content_hash IS NOT NULL AND deleted_at IS NULL;
