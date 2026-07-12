import uuid

import psycopg
import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def registry():
    user_ids: list[str] = []
    workspace_names: list[str] = []

    class Registry:
        def new_owner(self, suffix: str = "owner") -> dict:
            email = f"test-resolution-{suffix}-{uuid.uuid4()}@example.com"
            user = auth_repository.create_user(email)
            user_ids.append(user["id"])
            return user

        def new_workspace_name(self, suffix: str = "ws") -> str:
            name = f"test-resolution-{suffix}-{uuid.uuid4()}"
            workspace_names.append(name)
            return name

    yield Registry()

    if workspace_names:
        cleanup_rows(
            """
            DELETE FROM workspace_paths WHERE workspace_id IN
                (SELECT id FROM workspaces WHERE name = ANY(%s))
            """,
            (workspace_names,),
        )
        cleanup_rows("DELETE FROM workspaces WHERE name = ANY(%s)", (workspace_names,))
    if user_ids:
        cleanup_rows("DELETE FROM users WHERE id = ANY(%s)", (user_ids,))


def _machine() -> str:
    return f"machine-{uuid.uuid4()}"


def _path(suffix: str = "") -> str:
    return f"/tmp/test-resolution-{suffix}-{uuid.uuid4()}"


def test_exact_match_resolves(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    path = _path()

    repository.workspace_start(owner["id"], machine, path, ws_name)

    resolved = repository.resolve_workspace(owner["id"], machine, path)

    assert resolved is not None
    assert resolved["name"] == ws_name


def test_deep_child_dir_resolves_to_root(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    root = _path("root")
    child = f"{root}/apps/api/src"

    repository.workspace_start(owner["id"], machine, root, ws_name)

    resolved = repository.resolve_workspace(owner["id"], machine, child)

    assert resolved is not None
    assert resolved["name"] == ws_name


def test_prefix_boundary_does_not_match_sibling(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    root = _path("home-foo")
    sibling = f"{root}bar"

    repository.workspace_start(owner["id"], machine, root, ws_name)

    resolved = repository.resolve_workspace(owner["id"], machine, sibling)

    assert resolved is None


def test_nested_roots_longest_prefix_wins(registry):
    owner = registry.new_owner()
    outer_name = registry.new_workspace_name("outer")
    inner_name = registry.new_workspace_name("inner")
    machine = _machine()
    outer_root = _path("outer")
    inner_root = f"{outer_root}/nested"

    repository.workspace_start(owner["id"], machine, outer_root, outer_name)
    repository.workspace_start(owner["id"], machine, inner_root, inner_name)

    resolved = repository.resolve_workspace(owner["id"], machine, f"{inner_root}/deep")

    assert resolved is not None
    assert resolved["name"] == inner_name


def test_same_path_different_machines_are_isolated(registry):
    owner = registry.new_owner()
    ws_a = registry.new_workspace_name("machine-a")
    ws_b = registry.new_workspace_name("machine-b")
    path = _path()
    machine_a = _machine()
    machine_b = _machine()

    repository.workspace_start(owner["id"], machine_a, path, ws_a)
    repository.workspace_start(owner["id"], machine_b, path, ws_b)

    resolved_a = repository.resolve_workspace(owner["id"], machine_a, path)
    resolved_b = repository.resolve_workspace(owner["id"], machine_b, path)

    assert resolved_a is not None
    assert resolved_b is not None
    assert resolved_a["name"] == ws_a
    assert resolved_b["name"] == ws_b


def test_different_owner_is_invisible(registry):
    owner = registry.new_owner()
    other = registry.new_owner("other")
    ws_name = registry.new_workspace_name()
    machine = _machine()
    path = _path()

    repository.workspace_start(owner["id"], machine, path, ws_name)

    resolved = repository.resolve_workspace(other["id"], machine, path)

    assert resolved is None


def test_workspace_start_reuses_existing_workspace_by_owner_and_name(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    path_a = _path("a")
    path_b = _path("b")

    first = repository.workspace_start(owner["id"], machine, path_a, ws_name)
    second = repository.workspace_start(owner["id"], machine, path_b, ws_name)

    assert first["id"] == second["id"]

    resolved_a = repository.resolve_workspace(owner["id"], machine, path_a)
    resolved_b = repository.resolve_workspace(owner["id"], machine, path_b)
    assert resolved_a["id"] == resolved_b["id"] == first["id"]


def test_workspace_start_same_path_same_workspace_is_idempotent(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    path = _path()

    first = repository.workspace_start(owner["id"], machine, path, ws_name)
    second = repository.workspace_start(owner["id"], machine, path, ws_name)

    assert first["id"] == second["id"]


def test_workspace_start_duplicate_path_names_owning_workspace(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    other_ws_name = registry.new_workspace_name("other")
    machine = _machine()
    path = _path()

    repository.workspace_start(owner["id"], machine, path, ws_name)

    with pytest.raises(ValueError) as excinfo:
        repository.workspace_start(owner["id"], machine, path, other_ws_name)

    assert ws_name in str(excinfo.value)


def test_trailing_slash_normalized_on_write_and_read(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    path = _path()

    repository.workspace_start(owner["id"], machine, f"{path}/", ws_name)

    resolved = repository.resolve_workspace(owner["id"], machine, path)
    resolved_trailing = repository.resolve_workspace(owner["id"], machine, f"{path}/")

    assert resolved is not None
    assert resolved["name"] == ws_name
    assert resolved_trailing is not None
    assert resolved_trailing["name"] == ws_name


def test_list_workspaces_for_user_returns_only_owned(registry):
    owner = registry.new_owner()
    other = registry.new_owner("other")
    ws_name = registry.new_workspace_name()
    other_ws_name = registry.new_workspace_name("other")
    machine = _machine()

    repository.workspace_start(owner["id"], machine, _path("mine"), ws_name)
    repository.workspace_start(other["id"], machine, _path("theirs"), other_ws_name)

    owned = repository.list_workspaces(owner_user_id=owner["id"])

    names = {w["name"] for w in owned}
    assert ws_name in names
    assert other_ws_name not in names


def test_list_projects_for_workspace_scopes_by_workspace(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    workspace = repository.workspace_start(owner["id"], machine, _path(), ws_name)
    project_name = f"test-resolution-proj-{uuid.uuid4()}"
    project = repository.get_or_create_project(
        project_name, workspace_id=workspace["id"]
    )

    try:
        projects = repository.list_projects(workspace["id"])
        assert any(p["id"] == project["id"] for p in projects)
    finally:
        cleanup_rows("DELETE FROM projects WHERE id = %s", (project["id"],))


def test_list_projects_requires_scope():
    with pytest.raises(ValueError):
        repository.list_projects()


def test_prefix_boundary_ignores_like_metachars(registry):
    owner = registry.new_owner()
    ws_name = registry.new_workspace_name()
    machine = _machine()
    base = f"/home/{uuid.uuid4().hex}"
    root = f"{base}/my_project"
    lookalike = f"{base}/myXproject/sub"

    repository.workspace_start(owner["id"], machine, root, ws_name)

    resolved = repository.resolve_workspace(owner["id"], machine, lookalike)

    assert resolved is None


def test_workspace_start_duplicate_path_other_owner_hides_workspace_name(registry):
    owner = registry.new_owner()
    other = registry.new_owner("other")
    ws_name = registry.new_workspace_name()
    other_ws_name = registry.new_workspace_name("other")
    machine = _machine()
    path = _path()

    repository.workspace_start(owner["id"], machine, path, ws_name)

    with pytest.raises(ValueError) as excinfo:
        repository.workspace_start(other["id"], machine, path, other_ws_name)

    assert ws_name not in str(excinfo.value)


def test_workspace_start_duplicate_path_leaves_connection_idle(registry):
    owner = registry.new_owner()
    other_ws_name = registry.new_workspace_name("other")
    machine = _machine()
    path = _path()

    first_ws = registry.new_workspace_name()
    repository.workspace_start(owner["id"], machine, path, first_ws)

    conn = get_connection()
    with pytest.raises(ValueError):
        repository.workspace_start(owner["id"], machine, path, other_ws_name)

    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
