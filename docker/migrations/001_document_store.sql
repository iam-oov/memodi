CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ DEFAULT NULL,
    summary TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    project_id UUID REFERENCES projects(id) NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    topic_key TEXT DEFAULT NULL,
    metadata JSONB DEFAULT '{}',
    revision_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ DEFAULT NULL,
    search_vector TSVECTOR
);

CREATE INDEX IF NOT EXISTS idx_obs_project ON observations(project_id);
CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_obs_topic ON observations(project_id, topic_key)
    WHERE topic_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_deleted ON observations(deleted_at)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_obs_search ON observations USING GIN(search_vector);

CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', NEW.title || ' ' || NEW.content);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS observations_search_vector_update ON observations;
CREATE TRIGGER observations_search_vector_update
    BEFORE INSERT OR UPDATE ON observations
    FOR EACH ROW
    EXECUTE FUNCTION update_search_vector();
