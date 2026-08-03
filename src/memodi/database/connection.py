import contextlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from memodi.config import settings

_conn: psycopg.Connection | None = None
_schema_ensured = False

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def get_connection() -> psycopg.Connection:
    """The shared connection, reconnecting when the held one is dead.

    search_path is pinned to public instead of PostgreSQL's default
    `"$user", public`: the AGE graph is named 'memodi' and so is the DB
    role, so create_graph's schema is exactly what `"$user"` resolves to.
    Left unpinned, every unqualified statement — ensure_schema's own
    CREATE TABLE included — would build and read shadow copies of the
    app's tables inside the graph's schema.
    """
    global _conn
    if _conn is not None and not _conn.closed:
        try:
            _conn.execute("SELECT 1")
            return _conn
        except Exception:
            with contextlib.suppress(Exception):
                _conn.close()
            _conn = None
    _conn = psycopg.connect(
        settings.db_url,
        row_factory=dict_row,
        options="-c idle_in_transaction_session_timeout=30s -c search_path=public",
    )
    return _conn


def rollback() -> None:
    """Best-effort end of the shared connection's open transaction.

    Callers that swallow a database error use this so the next statement
    does not run inside an aborted transaction; callers that finish a
    read-only lookup use it so the connection is not handed back idle in
    transaction, which the server kills after 30s.
    """
    with contextlib.suppress(Exception):
        if _conn is not None and not _conn.closed:
            _conn.rollback()


def close_connection() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
        _conn = None


def run_migration(path: str) -> None:
    sql = Path(path).read_text()
    conn = get_connection()
    try:
        conn.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration '{Path(path).name}' failed: {e}") from e


def ensure_schema() -> None:
    global _schema_ensured
    if _schema_ensured:
        return

    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    conn.commit()

    applied = {
        row["name"] for row in conn.execute("SELECT name FROM _migrations").fetchall()
    }

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for migration_path in migration_files:
        name = migration_path.name
        if name not in applied:
            run_migration(str(migration_path))
            conn.execute("INSERT INTO _migrations (name) VALUES (%s)", (name,))
            conn.commit()

    _schema_ensured = True


def health_check() -> dict:
    try:
        conn = get_connection()
        row = conn.execute("SELECT version()").fetchone()
        version = row["version"] if row else "unknown"

        extensions = []
        for ext in conn.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'age')"
        ).fetchall():
            extensions.append(ext["extname"])

        return {
            "status": "healthy",
            "postgresql": version,
            "extensions": extensions,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }
