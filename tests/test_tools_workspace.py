import json
import uuid

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    delete_workspace,
    list_workspaces,
    rename_workspace,
    workspace_start,
)
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def owner():
    email = f"test-ws-owner-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)
    yield user
    cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))


@pytest.fixture
def machine():
    return f"test-machine-{uuid.uuid4()}"


@pytest.fixture
def ws_name():
    return f"test-ws-{uuid.uuid4()}"


def _cleanup_workspace(ws_name: str) -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM workspace_paths
        WHERE workspace_id IN (SELECT id FROM workspaces WHERE name = %s)
        """,
        (ws_name,),
    )
    conn.execute(
        """
        DELETE FROM projects
        WHERE workspace_id IN (SELECT id FROM workspaces WHERE name = %s)
        """,
        (ws_name,),
    )
    conn.execute("DELETE FROM workspaces WHERE name = %s", (ws_name,))
    conn.commit()


# --- workspace_start ---


def test_workspace_start_creates_workspace(owner, machine, ws_name):
    path = f"/tmp/test-ws-start-{uuid.uuid4()}"
    try:
        result = json.loads(
            workspace_start(
                path=path, workspace=ws_name, user_id=owner["id"], machine=machine
            )
        )
        assert result["name"] == ws_name
        assert result["owner_user_id"] == str(owner["id"])
    finally:
        _cleanup_workspace(ws_name)


def test_workspace_start_reuses_by_name(owner, machine, ws_name):
    path_a = f"/tmp/test-ws-start-a-{uuid.uuid4()}"
    path_b = f"/tmp/test-ws-start-b-{uuid.uuid4()}"
    try:
        first = json.loads(
            workspace_start(
                path=path_a, workspace=ws_name, user_id=owner["id"], machine=machine
            )
        )
        second = json.loads(
            workspace_start(
                path=path_b, workspace=ws_name, user_id=owner["id"], machine=machine
            )
        )
        assert first["id"] == second["id"]
    finally:
        _cleanup_workspace(ws_name)


def test_workspace_start_duplicate_path_returns_validation_error(
    owner, machine, ws_name
):
    path = f"/tmp/test-ws-start-dup-{uuid.uuid4()}"
    other_name = f"test-ws-other-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine=machine
        )
        result = json.loads(
            workspace_start(
                path=path, workspace=other_name, user_id=owner["id"], machine=machine
            )
        )
        assert result["type"] == "validation"
        assert ws_name in result["error"]
    finally:
        _cleanup_workspace(ws_name)
        _cleanup_workspace(other_name)


def test_workspace_start_rejects_legacy_machine(owner, ws_name):
    path = f"/tmp/test-ws-legacy-{uuid.uuid4()}"
    result = json.loads(
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine="legacy"
        )
    )
    assert result["type"] == "validation"
    assert "legacy" in result["error"]


# --- list_workspaces ---


def test_list_workspaces_scoped_to_owner(owner, machine, ws_name):
    path = f"/tmp/test-ws-list-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine=machine
        )

        workspaces = json.loads(list_workspaces(owner["id"]))
        names = [w["name"] for w in workspaces]
        assert ws_name in names
    finally:
        _cleanup_workspace(ws_name)


def test_list_workspaces_reports_project_count(owner, machine, ws_name):
    path = f"/tmp/test-ws-count-{uuid.uuid4()}"
    try:
        ws = json.loads(
            workspace_start(
                path=path, workspace=ws_name, user_id=owner["id"], machine=machine
            )
        )
        repository.get_or_create_project(
            f"test-count-proj-{uuid.uuid4()}", workspace_id=ws["id"]
        )

        workspaces = json.loads(list_workspaces(owner["id"]))
        target = next(w for w in workspaces if w["name"] == ws_name)
        assert target["project_count"] >= 1
    finally:
        _cleanup_workspace(ws_name)


def test_list_workspaces_excludes_other_owners(owner, machine, ws_name):
    other_email = f"test-ws-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(other_email)
    path = f"/tmp/test-ws-other-owner-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=other["id"], machine=machine
        )

        workspaces = json.loads(list_workspaces(owner["id"]))
        names = [w["name"] for w in workspaces]
        assert ws_name not in names
    finally:
        _cleanup_workspace(ws_name)
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


# --- delete_workspace ---


def test_delete_workspace_removes_it(owner, machine, ws_name):
    path = f"/tmp/test-ws-delete-{uuid.uuid4()}"
    workspace_start(path=path, workspace=ws_name, user_id=owner["id"], machine=machine)

    result = json.loads(delete_workspace(workspace=ws_name, user_id=owner["id"]))
    assert result["deleted"] is True

    workspaces = json.loads(list_workspaces(owner["id"]))
    ws_names = [w["name"] for w in workspaces]
    assert ws_name not in ws_names


def test_delete_workspace_not_found(owner):
    result = json.loads(
        delete_workspace(workspace="nonexistent-ws-12345", user_id=owner["id"])
    )
    assert result["deleted"] is False
    assert "error" in result


def test_delete_workspace_rejects_other_owner(owner, machine, ws_name):
    other_email = f"test-ws-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(other_email)
    path = f"/tmp/test-ws-owned-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine=machine
        )

        result = json.loads(delete_workspace(workspace=ws_name, user_id=other["id"]))
        assert result["deleted"] is False

        workspaces = json.loads(list_workspaces(owner["id"]))
        assert ws_name in [w["name"] for w in workspaces]
    finally:
        _cleanup_workspace(ws_name)
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


# --- rename_workspace ---


def test_rename_workspace_changes_name(owner, machine, ws_name):
    new_name = f"renamed-ws-{uuid.uuid4()}"
    path = f"/tmp/test-ws-rename-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine=machine
        )

        result = json.loads(
            rename_workspace(old_name=ws_name, new_name=new_name, user_id=owner["id"])
        )
        assert result["name"] == new_name

        workspaces = json.loads(list_workspaces(owner["id"]))
        ws_names = [w["name"] for w in workspaces]
        assert new_name in ws_names
        assert ws_name not in ws_names
    finally:
        _cleanup_workspace(new_name)
        _cleanup_workspace(ws_name)


def test_rename_workspace_not_found(owner):
    result = json.loads(
        rename_workspace(
            old_name="nonexistent-ws-12345", new_name="anything", user_id=owner["id"]
        )
    )
    assert "error" in result


def test_rename_workspace_rejects_other_owner(owner, machine, ws_name):
    other_email = f"test-ws-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(other_email)
    path = f"/tmp/test-ws-rename-other-{uuid.uuid4()}"
    try:
        workspace_start(
            path=path, workspace=ws_name, user_id=owner["id"], machine=machine
        )

        result = json.loads(
            rename_workspace(
                old_name=ws_name, new_name="hijacked-name", user_id=other["id"]
            )
        )
        assert "error" in result

        workspaces = json.loads(list_workspaces(owner["id"]))
        assert ws_name in [w["name"] for w in workspaces]
    finally:
        _cleanup_workspace(ws_name)
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))
