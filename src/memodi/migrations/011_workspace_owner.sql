ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id);

ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS workspaces_name_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_owner_name ON workspaces (owner_user_id, name) NULLS NOT DISTINCT;
