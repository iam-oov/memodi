-- occurred_at: logical event time, distinct from created_at (insert time).
-- Enables historical bulk imports that preserve the real timeline
-- (e.g. migrating notes from legacy .md files without losing order).
-- When NULL, ordering falls back to created_at.
ALTER TABLE observations ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_obs_effective_time
    ON observations (project_id, (COALESCE(occurred_at, created_at)) DESC)
    WHERE deleted_at IS NULL;
