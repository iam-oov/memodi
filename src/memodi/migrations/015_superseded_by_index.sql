-- get_observation resolves the reverse pointer (supersedes) by filtering
-- observations on superseded_by. Unindexed that is a Seq Scan over rows
-- carrying an inline 384d embedding, on every successful audit read.
-- Partial, like idx_obs_deleted: only superseded rows can ever match.
CREATE INDEX IF NOT EXISTS idx_obs_superseded_by ON observations(superseded_by)
    WHERE superseded_by IS NOT NULL;
