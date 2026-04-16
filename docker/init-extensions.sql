CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;

-- Production-only (Hetzner): preload AGE for non-superuser connections.
-- Run manually on the server, NOT here — it can crash Docker Postgres:
--   ALTER DATABASE memodi SET session_preload_libraries = 'age';
-- In Docker dev the POSTGRES_USER is superuser, so LOAD 'age' works
-- directly and session_preload_libraries is unnecessary.

-- Grant AGE catalog access to the application user.
-- Without these grants, non-superuser connections can't see ag_catalog
-- tables (ag_graph, ag_label, etc.) even with the correct search_path.
-- In local Docker dev POSTGRES_USER is already superuser, so these are
-- redundant but harmless.
GRANT USAGE ON SCHEMA ag_catalog TO memodi;
GRANT ALL ON ALL TABLES IN SCHEMA ag_catalog TO memodi;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ag_catalog TO memodi;

-- Load AGE into the current session (initdb) and set search path.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
