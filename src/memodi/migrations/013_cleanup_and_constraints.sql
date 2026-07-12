-- Destructive cleanup: retire pre-ownership era test data, then lock
-- strict workspace-scoped resolution in as a database-level invariant.
-- Approved: no pg_dump needed for this dataset.

DELETE FROM workflow_transitions
WHERE workflow_id IN (
    SELECT id FROM workflows
    WHERE project_id IN (SELECT id FROM projects WHERE workspace_id IS NULL)
);

DELETE FROM workflows
WHERE project_id IN (SELECT id FROM projects WHERE workspace_id IS NULL);

DELETE FROM observations
WHERE project_id IN (SELECT id FROM projects WHERE workspace_id IS NULL);

-- Detach any surviving observation that still points at a doomed session so
-- the DELETE below cannot raise a foreign-key violation.
UPDATE observations SET session_id = NULL
WHERE session_id IN (
    SELECT id FROM sessions
    WHERE project_id IN (SELECT id FROM projects WHERE workspace_id IS NULL)
);

DELETE FROM sessions
WHERE project_id IN (SELECT id FROM projects WHERE workspace_id IS NULL);

DELETE FROM projects WHERE workspace_id IS NULL;

DELETE FROM workflow_transitions
WHERE workflow_id IN (
    SELECT wf.id FROM workflows wf
    JOIN projects p ON p.id = wf.project_id
    JOIN workspaces w ON w.id = p.workspace_id
    WHERE w.owner_user_id IS NULL
);

DELETE FROM workflows
WHERE project_id IN (
    SELECT p.id FROM projects p
    JOIN workspaces w ON w.id = p.workspace_id
    WHERE w.owner_user_id IS NULL
);

DELETE FROM observations
WHERE project_id IN (
    SELECT p.id FROM projects p
    JOIN workspaces w ON w.id = p.workspace_id
    WHERE w.owner_user_id IS NULL
);

-- Detach surviving observations from any owner-less-workspace session or
-- orphaned (project-less) session before the sweep below.
UPDATE observations SET session_id = NULL
WHERE session_id IN (
    SELECT id FROM sessions
    WHERE project_id IN (
        SELECT p.id FROM projects p
        JOIN workspaces w ON w.id = p.workspace_id
        WHERE w.owner_user_id IS NULL
    )
    OR project_id IS NULL
);

DELETE FROM sessions
WHERE project_id IN (
    SELECT p.id FROM projects p
    JOIN workspaces w ON w.id = p.workspace_id
    WHERE w.owner_user_id IS NULL
)
OR project_id IS NULL;

DELETE FROM projects
WHERE workspace_id IN (SELECT id FROM workspaces WHERE owner_user_id IS NULL);

DELETE FROM workspace_paths
WHERE workspace_id IN (SELECT id FROM workspaces WHERE owner_user_id IS NULL);

DELETE FROM workspaces WHERE owner_user_id IS NULL;

DELETE FROM workspace_paths WHERE machine = 'legacy';

ALTER TABLE workspace_paths ALTER COLUMN machine DROP DEFAULT;

DROP INDEX IF EXISTS idx_projects_name_no_workspace;

ALTER TABLE projects ALTER COLUMN workspace_id SET NOT NULL;

ALTER TABLE workspaces ALTER COLUMN owner_user_id SET NOT NULL;
