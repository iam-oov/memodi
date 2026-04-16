CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;

-- Preload AGE for every new connection to this database.
-- Required in production where the application user is not a superuser
-- (LOAD 'age' without this setting requires superuser privileges).
-- In local Docker dev the POSTGRES_USER is superuser, so this is just
-- a convenience that lets the app skip the explicit LOAD.
ALTER DATABASE memodi SET session_preload_libraries = 'age';

-- Load AGE into the current session (initdb) and set search path.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
