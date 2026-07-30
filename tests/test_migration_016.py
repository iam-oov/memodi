from memodi.database.connection import MIGRATIONS_DIR, ensure_schema, get_connection

MIGRATION_016 = MIGRATIONS_DIR / "016_session_client_id.sql"


def test_sessions_client_session_id_column_exists():
    """The /hooks/session-close route matches a session to close by the
    Claude Code session id, not by 'the workspace's active session' —
    that requires this column to persist it."""
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = 'sessions' AND column_name = 'client_session_id'
        """
    ).fetchone()

    assert row is not None, "missing column client_session_id on sessions"
    assert row["data_type"] == "text"


def test_migration_016_is_idempotent():
    ensure_schema()
    sql = MIGRATION_016.read_text()

    conn = get_connection()
    conn.execute(sql)
    conn.commit()
