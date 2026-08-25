import json
import uuid

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema
from memodi.tools.memory import (
    context,
    list_paths,
    save,
    workspace_forget,
    workspace_repoint,
)
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


_WS = "SELECT id FROM workspaces WHERE owner_user_id = %s AND name = %s"


def _drop_workspace(user_id: str, name: str) -> None:
    args = (user_id, name)
    cleanup_rows(
        f"""
        DELETE FROM observations WHERE project_id IN (
            SELECT id FROM projects WHERE workspace_id IN ({_WS})
        )
        """,
        args,
    )
    cleanup_rows(f"DELETE FROM projects WHERE workspace_id IN ({_WS})", args)
    cleanup_rows(f"DELETE FROM workspace_paths WHERE workspace_id IN ({_WS})", args)
    cleanup_rows("DELETE FROM workspaces WHERE owner_user_id = %s AND name = %s", args)


def test_repoint_moves_the_registration_to_another_workspace(registered_workspace):
    target = f"test-repoint-ws-{uuid.uuid4()}"
    try:
        result = json.loads(
            workspace_repoint(
                path=registered_workspace["root"],
                workspace=target,
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
            )
        )
        assert result["changed"] is True
        assert result["workspace"] == target
        assert (
            result["previous_workspace"] == registered_workspace["workspace"]["name"]
        )

        resolved = repository.resolve_workspace(
            registered_workspace["user_id"],
            registered_workspace["machine"],
            registered_workspace["root"],
        )
        assert resolved["name"] == target
    finally:
        _drop_workspace(registered_workspace["user_id"], target)


def test_repoint_leaves_the_old_workspace_memory_untouched(registered_workspace):
    """Only the address moves — an accidental repoint must never strand or
    delete memory, which is what makes the tool safe to reach for."""
    target = f"test-repoint-ws-{uuid.uuid4()}"
    project = f"test-proj-{uuid.uuid4()}"
    try:
        save(
            path=f"{registered_workspace['root']}/{project}",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Memory that predates the repoint",
            content="Saved before the path moved to another workspace",
            type="decision",
        )

        workspace_repoint(
            path=registered_workspace["root"],
            workspace=target,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )

        old_projects = repository.list_projects(
            workspace_id=registered_workspace["workspace"]["id"]
        )
        assert project in [p["name"] for p in old_projects]

        after = json.loads(
            context(
                path=f"{registered_workspace['root']}/{project}",
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
            )
        )
        titles = [o["title"] for o in after["observations"]]
        assert "Memory that predates the repoint" not in titles
    finally:
        _drop_workspace(registered_workspace["user_id"], target)


def test_repoint_to_the_same_workspace_is_a_no_op(registered_workspace):
    result = json.loads(
        workspace_repoint(
            path=registered_workspace["root"],
            workspace=registered_workspace["workspace"]["name"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["changed"] is False


def test_repoint_an_unregistered_path_names_the_fix(registered_workspace):
    result = json.loads(
        workspace_repoint(
            path=f"/never/registered/{uuid.uuid4()}",
            workspace=f"test-repoint-ws-{uuid.uuid4()}",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert "memodi_workspace_start" in result["error"]


def test_repoint_ignores_another_machines_registration(registered_workspace):
    """Registrations are per machine, so repointing from machine B must not
    silently move machine A's row."""
    other_machine = f"test-machine-{uuid.uuid4()}"
    result = json.loads(
        workspace_repoint(
            path=registered_workspace["root"],
            workspace=f"test-repoint-ws-{uuid.uuid4()}",
            user_id=registered_workspace["user_id"],
            machine=other_machine,
        )
    )
    assert "error" in result

    resolved = repository.resolve_workspace(
        registered_workspace["user_id"],
        registered_workspace["machine"],
        registered_workspace["root"],
    )
    assert resolved["name"] == registered_workspace["workspace"]["name"]


def test_forget_makes_the_path_dormant_without_touching_memory(registered_workspace):
    project = f"test-proj-{uuid.uuid4()}"
    save(
        path=f"{registered_workspace['root']}/{project}",
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Memory that outlives the registration",
        content="Forgetting a path must not delete anything",
        type="decision",
    )

    result = json.loads(
        workspace_forget(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["forgotten"] is True

    assert (
        repository.resolve_workspace(
            registered_workspace["user_id"],
            registered_workspace["machine"],
            registered_workspace["root"],
        )
        is None
    )

    projects = repository.list_projects(
        workspace_id=registered_workspace["workspace"]["id"]
    )
    assert project in [p["name"] for p in projects]


def test_forget_an_unregistered_path_reports_rather_than_raises(registered_workspace):
    result = json.loads(
        workspace_forget(
            path=f"/never/registered/{uuid.uuid4()}",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["forgotten"] is False


def test_list_paths_shows_the_registration_and_its_workspace(registered_workspace):
    rows = json.loads(list_paths(registered_workspace["user_id"]))
    mine = [r for r in rows if r["path"] == registered_workspace["root"]]
    assert len(mine) == 1
    assert mine[0]["machine"] == registered_workspace["machine"]
    assert mine[0]["workspace"] == registered_workspace["workspace"]["name"]


def test_list_paths_follows_a_repoint(registered_workspace):
    target = f"test-repoint-ws-{uuid.uuid4()}"
    try:
        workspace_repoint(
            path=registered_workspace["root"],
            workspace=target,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
        rows = json.loads(list_paths(registered_workspace["user_id"]))
        mine = [r for r in rows if r["path"] == registered_workspace["root"]]
        assert mine[0]["workspace"] == target
    finally:
        _drop_workspace(registered_workspace["user_id"], target)


def test_list_paths_never_shows_another_owners_registration(registered_workspace):
    """The inventory is the map of someone's machines — it must not leak
    across accounts."""
    other = auth_repository.create_user(f"test-paths-{uuid.uuid4()}@example.com")
    try:
        rows = json.loads(list_paths(other["id"]))
        assert all(r["path"] != registered_workspace["root"] for r in rows)
    finally:
        cleanup_rows("DELETE FROM api_keys WHERE user_id = %s", (other["id"],))
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))
