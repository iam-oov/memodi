CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;

-- Load AGE into the current session (initdb) and set search path.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- === Production-only setup (Hetzner, non-superuser app user) ===
-- These are NOT run here because they can crash Docker Postgres or
-- are irrelevant when POSTGRES_USER is already superuser.
-- Run manually on the production server as postgres superuser:
--
--   ALTER DATABASE memodi SET session_preload_libraries = 'age';
--   GRANT USAGE ON SCHEMA ag_catalog TO memodi;
--   GRANT ALL ON ALL TABLES IN SCHEMA ag_catalog TO memodi;
--   GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ag_catalog TO memodi;
