import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    delete_workspace,
    link_project,
    list_workspaces,
    register_path,
    rename_workspace,
    resolve_path,
)


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def ws_name():
    return f"test-ws-{uuid.uuid4()}"


@pytest.fixture
def project_name():
    return f"test-proj-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(ws_name, project_name):
    yield
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
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.execute("DELETE FROM workspaces WHERE name = %s", (ws_name,))
    conn.commit()


# --- resolve_path + register_path ---


def test_register_and_resolve_path(ws_name):
    path = f"/home/test/{uuid.uuid4()}"

    reg = json.loads(register_path(path=path, workspace=ws_name))
    assert reg["path"] == path
    assert reg["workspace"] == ws_name

    res = json.loads(resolve_path(path=path))
    assert res["resolved"] is True
    assert res["workspace"]["name"] == ws_name


def test_resolve_path_unknown():
    res = json.loads(resolve_path(path="/nonexistent/path/that/never/exists"))
    assert res["resolved"] is False
    assert "/nonexistent" in res["path"]


def test_register_path_creates_workspace(ws_name):
    path = f"/home/test/{uuid.uuid4()}"
    register_path(path=path, workspace=ws_name)

    workspaces = json.loads(list_workspaces())
    ws_names = [w["name"] for w in workspaces]
    assert ws_name in ws_names


# --- list_workspaces ---


def test_list_workspaces_includes_created(ws_name, project_name):
    link_project(project=project_name, workspace=ws_name)

    workspaces = json.loads(list_workspaces())
    match = [w for w in workspaces if w["name"] == ws_name]
    assert len(match) == 1
    assert match[0]["project_count"] >= 1


# --- delete_workspace ---


def test_delete_workspace_removes_it(ws_name, project_name):
    link_project(project=project_name, workspace=ws_name)

    result = json.loads(delete_workspace(workspace=ws_name))
    assert result["deleted"] is True

    workspaces = json.loads(list_workspaces())
    ws_names = [w["name"] for w in workspaces]
    assert ws_name not in ws_names


def test_delete_workspace_not_found():
    result = json.loads(delete_workspace(workspace="nonexistent-ws-12345"))
    assert result["deleted"] is False
    assert "error" in result


# --- rename_workspace ---


def test_rename_workspace_changes_name(ws_name, project_name):
    new_name = f"renamed-ws-{uuid.uuid4()}"
    link_project(project=project_name, workspace=ws_name)

    result = json.loads(rename_workspace(old_name=ws_name, new_name=new_name))
    assert result["name"] == new_name

    workspaces = json.loads(list_workspaces())
    ws_names = [w["name"] for w in workspaces]
    assert new_name in ws_names
    assert ws_name not in ws_names

    # Cleanup the renamed workspace too
    conn = get_connection()
    conn.execute(
        """
        DELETE FROM workspace_paths
        WHERE workspace_id IN (SELECT id FROM workspaces WHERE name = %s)
        """,
        (new_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.execute("DELETE FROM workspaces WHERE name = %s", (new_name,))
    conn.commit()


def test_rename_workspace_not_found():
    result = json.loads(
        rename_workspace(old_name="nonexistent-ws-12345", new_name="anything")
    )
    assert "error" in result
