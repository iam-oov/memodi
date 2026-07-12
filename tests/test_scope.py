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


def test_resolve_project_empty_derived_name_raises(created_user):
    machine = f"machine-{uuid.uuid4()}"
    ws = repository.workspace_start(
        created_user["id"], machine, "/", f"root-ws-{uuid.uuid4()}"
    )
    try:
        with pytest.raises(ValueError, match="cannot derive project name"):
            resolve_project(created_user["id"], machine, "/", None)
    finally:
        cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws["id"],))
        cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws["id"],))
        cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws["id"],))


def test_resolve_project_unregistered_path_raises(created_user):
    with pytest.raises(NotStartedError):
        resolve_project(
            created_user["id"],
            f"machine-{uuid.uuid4()}",
            "/never/registered/path",
            None,
        )
