ALTER TABLE observations ADD COLUMN IF NOT EXISTS plan_md TEXT;

CREATE INDEX IF NOT EXISTS idx_obs_plan_purge
    ON observations (resolved_at)
    WHERE plan_md IS NOT NULL;
