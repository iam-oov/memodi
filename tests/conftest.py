import os
import uuid

# The repository import chain instantiates Settings(), which requires DB env
# vars. Set dummies before import so no-DB collection works (mirrors
# tests/test_defer_loading.py). setdefault preserves real values when present.
os.environ.setdefault("MEMODI_DB_USER", "test")
os.environ.setdefault("MEMODI_DB_PASSWORD", "test")

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection


def cleanup_rows(delete_sql: str, params: tuple) -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(delete_sql, params)
    conn.commit()


def _path(registered_workspace: dict, project_name: str) -> str:
    return f"{registered_workspace['root']}/{project_name}"


@pytest.fixture
def registered_workspace():
    ensure_schema()

    email = f"test-registered-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)
    machine = f"test-machine-{uuid.uuid4()}"
    root = f"/tmp/test-registered-{uuid.uuid4()}"
    workspace_name = f"test-registered-ws-{uuid.uuid4()}"

    workspace = repository.workspace_start(user["id"], machine, root, workspace_name)

    yield {
        "user_id": user["id"],
        "machine": machine,
        "root": root,
        "workspace": workspace,
        "api_key": user["api_key"],
    }

    cleanup_rows(
        """
        DELETE FROM workflow_transitions WHERE workflow_id IN (
            SELECT wf.id FROM workflows wf
            JOIN projects p ON p.id = wf.project_id
            WHERE p.workspace_id = %s
        )
        """,
        (workspace["id"],),
    )
    cleanup_rows(
        """
        DELETE FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (workspace["id"],),
    )
    cleanup_rows(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (workspace["id"],),
    )
    cleanup_rows(
        """
        DELETE FROM sessions
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (workspace["id"],),
    )
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (workspace["id"],))
    cleanup_rows(
        "DELETE FROM workspace_paths WHERE workspace_id = %s", (workspace["id"],)
    )
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (workspace["id"],))
    cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))
