import uuid

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import NotAuthenticatedError, NotStartedError
from memodi.tools.scope import require_user, require_workspace, resolve_project
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def created_user():
    email = f"test-scope-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)
    yield user
    cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))


def test_require_user_missing_api_key_raises():
    with pytest.raises(NotAuthenticatedError):
        require_user(None)


def test_require_user_unknown_api_key_raises():
    with pytest.raises(NotAuthenticatedError):
        require_user("mmd_does-not-exist")


def test_require_user_valid_api_key_returns_user(created_user):
    user = require_user(created_user["api_key"])
    assert user["id"] == created_user["id"]


def test_require_workspace_unregistered_path_raises_not_started(created_user):
    with pytest.raises(NotStartedError):
        require_workspace(
            created_user["id"], f"machine-{uuid.uuid4()}", "/never/registered/path"
        )


def test_require_workspace_machine_mismatch_raises_not_started(created_user):
    machine_a = f"machine-a-{uuid.uuid4()}"
    machine_b = f"machine-b-{uuid.uuid4()}"
    root = f"/tmp/test-scope-mismatch-{uuid.uuid4()}"
    ws = repository.workspace_start(
        created_user["id"], machine_a, root, f"ws-{uuid.uuid4()}"
    )
    try:
        with pytest.raises(NotStartedError):
            require_workspace(created_user["id"], machine_b, root)
    finally:
        cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws["id"],))
        cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws["id"],))


def test_require_workspace_rejects_legacy_machine(created_user):
    with pytest.raises(ValueError, match="legacy"):
        require_workspace(created_user["id"], "legacy", "/some/path")


def test_resolve_project_defaults_name_to_basename(registered_workspace):
    path = f"{registered_workspace['root']}/my-repo"

    proj = resolve_project(
        registered_workspace["user_id"],
        registered_workspace["machine"],
        path,
        None,
    )

    assert proj["name"] == "my-repo"
    assert proj["workspace_id"] == registered_workspace["workspace"]["id"]

    cleanup_rows("DELETE FROM projects WHERE id = %s", (proj["id"],))


def test_resolve_project_uses_explicit_project_name(registered_workspace):
    path = f"{registered_workspace['root']}/my-repo"

    proj = resolve_project(
        registered_workspace["user_id"],
        registered_workspace["machine"],
        path,
        "custom-name",
    )

    assert proj["name"] == "custom-name"

    cleanup_rows("DELETE FROM projects WHERE id = %s", (proj["id"],))


def _drop(ws: dict) -> None:
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws["id"],))
    cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws["id"],))
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws["id"],))


def test_resolve_project_at_a_root_with_no_basename_uses_the_workspace_name(
    created_user,
):
    """Registering '/' used to be a landmine — basename('') derived nothing and
    the call blew up. The root project comes from the workspace now, so there
    is no name left to fail to derive."""
    machine = f"machine-{uuid.uuid4()}"
    name = f"root-ws-{uuid.uuid4()}"
    ws = repository.workspace_start(created_user["id"], machine, "/", name)
    try:
        proj = resolve_project(created_user["id"], machine, "/", None)
        assert proj["name"] == name
        assert proj["at_workspace_root"] is True
    finally:
        _drop(ws)


def test_every_root_of_one_workspace_resolves_to_the_same_container_project(
    created_user,
):
    """The point of multi-path workspaces: several folders — on this machine
    and on others — are ONE workspace. Their roots must land on one container
    project, whatever the folders are called, or the shared layer splits per
    machine all over again."""
    name = f"multi-ws-{uuid.uuid4()}"
    machine_a = f"machine-a-{uuid.uuid4()}"
    machine_b = f"machine-b-{uuid.uuid4()}"
    root_a = f"/tmp/test-scope-{uuid.uuid4()}/TirielInc"
    root_a2 = f"/tmp/test-scope-{uuid.uuid4()}/TirielInc_Automatice"
    root_b = f"/tmp/test-scope-{uuid.uuid4()}/work/tiriel-monorepo"

    ws = repository.workspace_start(created_user["id"], machine_a, root_a, name)
    repository.workspace_start(created_user["id"], machine_a, root_a2, name)
    repository.workspace_start(created_user["id"], machine_b, root_b, name)
    try:
        resolved = [
            resolve_project(created_user["id"], machine_a, root_a, None),
            resolve_project(created_user["id"], machine_a, root_a2, None),
            resolve_project(created_user["id"], machine_b, root_b, None),
        ]
        assert {p["name"] for p in resolved} == {name}
        assert len({str(p["id"]) for p in resolved}) == 1
        assert all(p["at_workspace_root"] for p in resolved)
    finally:
        _drop(ws)


def test_children_of_different_roots_inherit_the_same_container(created_user):
    """A repo under one root and a repo under another inherit ONE parent —
    the container project, not their own folder's ancestor."""
    name = f"multi-ws-{uuid.uuid4()}"
    machine_a = f"machine-a-{uuid.uuid4()}"
    machine_b = f"machine-b-{uuid.uuid4()}"
    root_a = f"/tmp/test-scope-{uuid.uuid4()}/TirielInc"
    root_b = f"/tmp/test-scope-{uuid.uuid4()}/TirielInc_Automatice"

    ws = repository.workspace_start(created_user["id"], machine_a, root_a, name)
    repository.workspace_start(created_user["id"], machine_b, root_b, name)
    try:
        container = resolve_project(created_user["id"], machine_a, root_a, None)
        child_a = resolve_project(
            created_user["id"], machine_a, f"{root_a}/tiriel-gateway-service", None
        )
        child_b = resolve_project(
            created_user["id"],
            machine_b,
            f"{root_b}/System/Backend/tiriel-gateway-service",
            None,
        )

        assert child_a["name"] == child_b["name"] == "tiriel-gateway-service"
        assert str(child_a["id"]) == str(child_b["id"])
        expected = [str(container["id"])]
        assert [str(i) for i in child_a["inherited_ids"]] == expected
        assert [str(i) for i in child_b["inherited_ids"]] == expected
    finally:
        _drop(ws)


def test_resolve_project_unregistered_path_raises(created_user):
    with pytest.raises(NotStartedError):
        resolve_project(
            created_user["id"],
            f"machine-{uuid.uuid4()}",
            "/never/registered/path",
            None,
        )
