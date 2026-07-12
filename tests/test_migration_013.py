import hashlib
import uuid

import psycopg
from psycopg.rows import dict_row

from memodi.config import settings
from memodi.database.connection import MIGRATIONS_DIR, ensure_schema

MIGRATION_013 = MIGRATIONS_DIR / "013_cleanup_and_constraints.sql"


def _isolated_conn() -> psycopg.Connection:
    conn = psycopg.connect(settings.db_url, row_factory=dict_row)
    conn.execute("SET lock_timeout = '5s'")
    return conn


def test_migration_013_fk_safe_with_cross_project_session_reference():
    """A surviving observation that points at a doomed (workspace-less)
    project's session must not make the sessions DELETE raise a
    foreign-key violation."""
    ensure_schema()
    sql = MIGRATION_013.read_text()

    conn = _isolated_conn()
    try:
        # Recreate the pre-013 state the migration is meant to clean up
        # (the committed NOT NULL constraints otherwise forbid it).
        conn.execute("ALTER TABLE projects ALTER COLUMN workspace_id DROP NOT NULL")
        conn.execute("ALTER TABLE workspaces ALTER COLUMN owner_user_id DROP NOT NULL")

        email = f"mig013-{uuid.uuid4()}@example.com"
        api_key_hash = hashlib.sha256(email.encode()).hexdigest()
        user = conn.execute(
            "INSERT INTO users (email, api_key_hash) VALUES (%s, %s) RETURNING id",
            (email, api_key_hash),
        ).fetchone()
        ws_ok = conn.execute(
            "INSERT INTO workspaces (name, owner_user_id) VALUES (%s, %s) RETURNING id",
            (f"mig013-ws-{uuid.uuid4()}", user["id"]),
        ).fetchone()
        proj_ok = conn.execute(
            "INSERT INTO projects (name, workspace_id) VALUES (%s, %s) RETURNING id",
            (f"mig013-ok-{uuid.uuid4()}", ws_ok["id"]),
        ).fetchone()
        proj_doomed = conn.execute(
            "INSERT INTO projects (name, workspace_id) VALUES (%s, NULL) RETURNING id",
            (f"mig013-doomed-{uuid.uuid4()}",),
        ).fetchone()
        sess_doomed = conn.execute(
            "INSERT INTO sessions (project_id) VALUES (%s) RETURNING id",
            (proj_doomed["id"],),
        ).fetchone()
        obs_ok = conn.execute(
            """
            INSERT INTO observations (project_id, session_id, type, title, content)
            VALUES (%s, %s, 'decision', 'survivor', 'points at doomed session')
            RETURNING id
            """,
            (proj_ok["id"], sess_doomed["id"]),
        ).fetchone()

        # Before the fix this raises ForeignKeyViolation on the sessions DELETE.
        conn.execute(sql)

        assert (
            conn.execute(
                "SELECT id FROM projects WHERE id = %s", (proj_doomed["id"],)
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT id FROM sessions WHERE id = %s", (sess_doomed["id"],)
            ).fetchone()
            is None
        )
        survivor = conn.execute(
            "SELECT session_id FROM observations WHERE id = %s", (obs_ok["id"],)
        ).fetchone()
        assert survivor is not None
        assert survivor["session_id"] is None
    finally:
        conn.rollback()
        conn.close()


def test_migration_013_sweeps_orphan_project_null_sessions():
    """Sessions with project_id IS NULL are legacy orphans and must be
    swept by the cleanup, not left behind."""
    ensure_schema()
    sql = MIGRATION_013.read_text()

    conn = _isolated_conn()
    try:
        orphan = conn.execute(
            "INSERT INTO sessions (project_id) VALUES (NULL) RETURNING id"
        ).fetchone()

        conn.execute(sql)

        assert (
            conn.execute(
                "SELECT id FROM sessions WHERE id = %s", (orphan["id"],)
            ).fetchone()
            is None
        )
    finally:
        conn.rollback()
        conn.close()
