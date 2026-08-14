import uuid

from memodi.database import auth_repository, repository
from memodi.database.connection import MIGRATIONS_DIR, ensure_schema, get_connection
from tests.conftest import cleanup_rows

MIGRATION_018 = MIGRATIONS_DIR / "018_per_owner_paths.sql"

OLD_INDEX_NAME = "idx_workspace_paths_machine_path"
NEW_INDEX_NAME = "idx_workspace_paths_owner_machine_path"


def test_workspace_paths_owner_user_id_column_exists_not_null_with_fk():
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'workspace_paths' AND column_name = 'owner_user_id'
        """
    ).fetchone()

    assert row is not None
    assert row["data_type"] == "uuid"
    assert row["is_nullable"] == "NO"

    fk = conn.execute(
        """
        SELECT ccu.table_name AS foreign_table
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
        JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'workspace_paths'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'owner_user_id'
        """
    ).fetchone()

    assert fk is not None
    assert fk["foreign_table"] == "users"


def test_old_machine_path_index_is_gone():
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'workspace_paths' AND indexname = %s
        """,
        (OLD_INDEX_NAME,),
    ).fetchone()

    assert row is None


def test_new_owner_machine_path_unique_index_exists():
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'workspace_paths' AND indexname = %s
        """,
        (NEW_INDEX_NAME,),
    ).fetchone()

    assert row is not None, f"missing index {NEW_INDEX_NAME} on workspace_paths"
    assert "UNIQUE" in row["indexdef"]
    assert "owner_user_id" in row["indexdef"]
    assert "machine" in row["indexdef"]
    assert "path" in row["indexdef"]


def test_backfill_sets_owner_user_id_from_workspace():
    ensure_schema()
    conn = get_connection()

    email = f"test-mig018-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)
    machine = f"test-mig018-machine-{uuid.uuid4()}"
    path = f"/tmp/test-mig018-{uuid.uuid4()}"
    workspace_name = f"test-mig018-ws-{uuid.uuid4()}"
    workspace = repository.get_or_create_workspace(
        workspace_name, owner_user_id=user["id"]
    )

    try:
        conn.execute(
            "ALTER TABLE workspace_paths ALTER COLUMN owner_user_id DROP NOT NULL"
        )
        conn.execute(
            "INSERT INTO workspace_paths (workspace_id, machine, path, owner_user_id) "
            "VALUES (%s, %s, %s, NULL)",
            (workspace["id"], machine, path),
        )
        conn.commit()

        sql = MIGRATION_018.read_text()
        conn.execute(sql)
        conn.commit()

        row = conn.execute(
            "SELECT owner_user_id FROM workspace_paths WHERE workspace_id = %s",
            (workspace["id"],),
        ).fetchone()

        assert row is not None
        assert str(row["owner_user_id"]) == str(user["id"])
    finally:
        cleanup_rows(
            "DELETE FROM workspace_paths WHERE workspace_id = %s", (workspace["id"],)
        )
        cleanup_rows("DELETE FROM workspaces WHERE id = %s", (workspace["id"],))
        cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))


def test_migration_018_is_idempotent():
    ensure_schema()
    conn = get_connection()

    count_before = conn.execute(
        "SELECT COUNT(*) AS c FROM workspace_paths"
    ).fetchone()["c"]

    sql = MIGRATION_018.read_text()
    conn.execute(sql)
    conn.commit()

    column = conn.execute(
        """
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = 'workspace_paths' AND column_name = 'owner_user_id'
        """
    ).fetchone()
    assert column["is_nullable"] == "NO"

    new_index = conn.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'workspace_paths' AND indexname = %s
        """,
        (NEW_INDEX_NAME,),
    ).fetchone()
    assert new_index is not None

    old_index = conn.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'workspace_paths' AND indexname = %s
        """,
        (OLD_INDEX_NAME,),
    ).fetchone()
    assert old_index is None

    count_after = conn.execute(
        "SELECT COUNT(*) AS c FROM workspace_paths"
    ).fetchone()["c"]
    assert count_after == count_before
