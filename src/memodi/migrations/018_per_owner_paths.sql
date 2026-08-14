ALTER TABLE workspace_paths ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id);

UPDATE workspace_paths wp
SET owner_user_id = w.owner_user_id
FROM workspaces w
WHERE w.id = wp.workspace_id AND wp.owner_user_id IS NULL;

ALTER TABLE workspace_paths ALTER COLUMN owner_user_id SET NOT NULL;

DROP INDEX IF EXISTS idx_workspace_paths_machine_path;

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_paths_owner_machine_path
    ON workspace_paths(owner_user_id, machine, path);
