import uuid

import psycopg
from psycopg.rows import dict_row

from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import MIGRATIONS_DIR, ensure_schema, get_connection
from tests.conftest import cleanup_rows

MIGRATION_017 = MIGRATIONS_DIR / "017_api_keys.sql"


def _isolated_conn() -> psycopg.Connection:
    conn = psycopg.connect(settings.db_url, row_factory=dict_row)
    conn.execute("SET lock_timeout = '5s'")
    return conn


def test_api_keys_table_has_expected_columns():
    ensure_schema()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'api_keys'
        """
    ).fetchall()
    columns = {row["column_name"]: row for row in rows}

    assert columns["id"]["data_type"] == "uuid"
    assert columns["user_id"]["data_type"] == "uuid"
    assert columns["user_id"]["is_nullable"] == "NO"
    assert columns["key_hash"]["data_type"] == "text"
    assert columns["key_hash"]["is_nullable"] == "NO"
    assert columns["created_at"]["data_type"] == "timestamp with time zone"


def test_api_keys_user_id_has_cascade_delete():
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT rc.delete_rule
        FROM information_schema.referential_constraints rc
        JOIN information_schema.table_constraints tc
            ON tc.constraint_name = rc.constraint_name
        WHERE tc.table_name = 'api_keys'
        """
    ).fetchone()

    assert row is not None
    assert row["delete_rule"] == "CASCADE"


def test_api_keys_cascade_deletes_when_user_deleted():
    ensure_schema()
    email = f"test-mig017-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)

    conn = get_connection()
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (user["id"],)
        ).fetchone()["c"]
        assert before == 1

        conn.execute("DELETE FROM users WHERE id = %s", (user["id"],))
        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (user["id"],)
        ).fetchone()["c"]
        assert after == 0
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (email,))


def test_users_api_key_hash_column_no_longer_exists():
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'api_key_hash'
        """
    ).fetchone()

    assert row is None


def test_migration_017_is_idempotent():
    ensure_schema()
    sql = MIGRATION_017.read_text()

    conn = get_connection()
    conn.execute(sql)
    conn.commit()


def test_migration_017_lowercases_legacy_mixed_case_emails():
    """Rows written before login normalized case would otherwise be orphaned:
    a Google login for the same address lowercases it, misses the legacy row,
    and creates a second user whose workspaces are unreachable."""
    ensure_schema()
    sql = MIGRATION_017.read_text()

    conn = _isolated_conn()
    try:
        mixed = f"Mig017-{uuid.uuid4()}@Example.COM"
        user = conn.execute(
            "INSERT INTO users (email) VALUES (%s) RETURNING id", (mixed,)
        ).fetchone()

        conn.execute(sql)

        row = conn.execute(
            "SELECT email FROM users WHERE id = %s", (user["id"],)
        ).fetchone()
        assert row["email"] == mixed.lower()
    finally:
        conn.rollback()
        conn.close()
