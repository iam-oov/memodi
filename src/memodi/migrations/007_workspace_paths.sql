CREATE TABLE IF NOT EXISTS workspace_paths (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID REFERENCES workspaces(id) NOT NULL,
    path TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ws_paths_workspace ON workspace_paths(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ws_paths_path ON workspace_paths(path);
