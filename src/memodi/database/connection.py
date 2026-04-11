from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from memodi.config import settings

_conn: psycopg.Connection | None = None

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "docker" / "migrations"


def get_connection() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(settings.db_url, row_factory=dict_row)
    return _conn


def close_connection() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
        _conn = None


def run_migration(path: str) -> None:
    sql = Path(path).read_text()
    conn = get_connection()
    conn.execute(sql)
    conn.commit()


def ensure_schema() -> None:
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
