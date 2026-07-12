DELETE FROM workspace_paths;

ALTER TABLE workspace_paths ADD COLUMN IF NOT EXISTS machine TEXT DEFAULT 'legacy';
ALTER TABLE workspace_paths ALTER COLUMN machine SET NOT NULL;

ALTER TABLE workspace_paths DROP CONSTRAINT IF EXISTS workspace_paths_path_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_paths_machine_path ON workspace_paths(machine, path);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
