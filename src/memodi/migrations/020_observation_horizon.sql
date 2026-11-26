ALTER TABLE observations ADD COLUMN IF NOT EXISTS horizon TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

UPDATE observations
SET horizon = CASE type
    WHEN 'preference' THEN 'long'
    WHEN 'pattern' THEN 'long'
    WHEN 'decision' THEN 'medium'
    WHEN 'architecture' THEN 'medium'
    ELSE 'short'
END
WHERE horizon IS NULL;

ALTER TABLE observations ALTER COLUMN horizon SET DEFAULT 'medium';
ALTER TABLE observations ALTER COLUMN horizon SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_obs_horizon
    ON observations (project_id, horizon)
    WHERE deleted_at IS NULL AND superseded_by IS NULL;
