CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);

ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_name_key;
ALTER TABLE projects ADD CONSTRAINT projects_name_workspace_unique UNIQUE (name, workspace_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name_no_workspace ON projects(name) WHERE workspace_id IS NULL;
