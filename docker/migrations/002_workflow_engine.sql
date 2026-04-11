CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) NOT NULL,
    name TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'plan',
    objective TEXT,
    acceptance_criteria JSONB DEFAULT '[]',
    tasks JSONB DEFAULT '[]',
    result JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS workflow_transitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) NOT NULL,
    from_phase TEXT NOT NULL,
    to_phase TEXT NOT NULL,
    notes TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflows_project ON workflows(project_id);
CREATE INDEX IF NOT EXISTS idx_workflows_phase ON workflows(phase);
CREATE INDEX IF NOT EXISTS idx_workflow_transitions_workflow ON workflow_transitions(workflow_id);
