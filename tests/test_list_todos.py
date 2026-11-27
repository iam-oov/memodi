import json
import uuid

import pytest

from memodi.config import settings
from memodi.database.connection import ensure_schema, get_connection
from memodi.server import CORE_TOOLS, _list_tools_with_deferred
from memodi.tools.memory import (
    claim_todo,
    context,
    list_todos,
    resolve_todo,
    save,
    set_coordinator,
    todo_progress,
)
from tests.test_tools_memory import _cleanup_workspace, _extra_workspace

SESSION = "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _schema():
    ensure_schema()


@pytest.fixture
def repo(registered_workspace, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://memodi.example")
    return {
        **registered_workspace,
        "path": f"{registered_workspace['root']}/test-list-{uuid.uuid4()}",
    }


def _caller(ws: dict) -> dict:
    return {"user_id": ws["user_id"], "machine": ws["machine"]}


def _todo(ws: dict, title: str, path: str | None = None, **extra) -> str:
    ack = json.loads(
        save(
            path=path or ws["path"],
            title=title,
            content=f"{title} {uuid.uuid4()}",
            type="incident",
            **_caller(ws),
            **extra,
        )
    )
    return ack["id"]


def _listed(ws: dict, path: str | None = None, **extra) -> dict:
    return json.loads(list_todos(path or ws["path"], **_caller(ws), **extra))


def _open_ids(listed: dict) -> set[str]:
    return {t["id"] for t in listed["open"]}


@pytest.fixture
def other_workspace_todo(repo):
    other = _extra_workspace(repo["user_id"])
    todo_id = json.loads(
        save(
            path=f"{other['root']}/elsewhere",
            title="Elsewhere todo",
            content="In another workspace",
            type="incident",
            user_id=other["user_id"],
            machine=other["machine"],
        )
    )["id"]
    yield todo_id
    _cleanup_workspace(other["workspace"])


def test_default_lists_only_this_workspace(repo, other_workspace_todo):
    here = _todo(repo, "Here todo")

    listed = _listed(repo)

    assert listed["scope"] == "workspace"
    assert here in _open_ids(listed)
    assert other_workspace_todo not in _open_ids(listed)


def test_all_workspaces_lists_every_workspace(repo, other_workspace_todo):
    here = _todo(repo, "Here todo")

    listed = _listed(repo, all_workspaces=True)

    assert listed["scope"] == "all_workspaces"
    assert {here, other_workspace_todo} <= _open_ids(listed)


def test_a_repo_never_sees_a_coordinators_todos(repo):
    # Same read scope as memodi_context: a child repo sees its own todos,
    # never a coordinator sibling's.
    root = repo["root"]
    coordinator = f"{root}/test-coord-{uuid.uuid4()}"
    set_coordinator(path=coordinator, **_caller(repo))
    case = _todo(repo, "Coordinator case", path=coordinator)
    mine = _todo(repo, "Repo todo")

    from_repo = _open_ids(_listed(repo))
    from_coordinator = _open_ids(_listed(repo, path=coordinator))

    assert mine in from_repo
    assert case not in from_repo
    assert {mine, case} <= from_coordinator


def test_unregistered_path_is_not_started(repo):
    listed = json.loads(list_todos("/nowhere/at/all", **_caller(repo)))

    assert listed["type"] == "not_started"


def test_entry_carries_holder_phase_and_plan(repo):
    todo_id = _todo(repo, "BUG-test quota")
    claim_todo(
        path=repo["path"],
        observation_id=todo_id,
        client_session_id=SESSION,
        holder_label="Mi Todo Slack",
        phase_total=4,
        **_caller(repo),
    )
    todo_progress(
        path=repo["path"],
        observation_id=todo_id,
        client_session_id=SESSION,
        phase_current=1,
        phase_label="Diagnosis",
        plan_md="# Plan",
        **_caller(repo),
    )

    entry = next(t for t in _listed(repo)["open"] if t["id"] == todo_id)

    assert entry["short_id"] == todo_id[:4]
    assert entry["workspace"] == repo["workspace"]["name"]
    assert entry["held_by"] == "Mi Todo Slack"
    assert entry["phase"] == "F-1/4 (Diagnosis)"
    assert entry["plan"] == f"https://memodi.example/o/{todo_id}"


def test_free_todo_has_no_holder(repo):
    todo_id = _todo(repo, "Free todo")

    entry = next(t for t in _listed(repo)["open"] if t["id"] == todo_id)

    assert "held_by" not in entry
    assert "plan" not in entry


def test_recently_resolved_carries_the_outcome(repo):
    todo_id = _todo(repo, "Done todo")
    resolve_todo(
        path=repo["path"],
        observation_id=todo_id,
        outcome="Fixed; see [[ops/x]]",
        **_caller(repo),
    )

    listed = _listed(repo)
    done = next(t for t in listed["recently_resolved"] if t["id"] == todo_id)

    assert done["outcome"] == "Fixed; see [[ops/x]]"
    assert all(t["id"] != todo_id for t in listed["open"])


def test_old_resolutions_drop_off_the_list(repo):
    todo_id = _todo(repo, "Old todo")
    resolve_todo(path=repo["path"], observation_id=todo_id, **_caller(repo))
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        "UPDATE observations SET resolved_at = now() - interval '8 days' WHERE id = %s",
        (todo_id,),
    )
    conn.commit()

    assert all(t["id"] != todo_id for t in _listed(repo)["recently_resolved"])


def test_non_todo_notes_are_not_listed(repo):
    note = json.loads(
        save(
            path=repo["path"],
            title="Plain decision",
            content="Not a todo",
            type="decision",
            **_caller(repo),
        )
    )["id"]

    assert all(t["id"] != note for t in _listed(repo)["open"])


def test_another_users_todos_are_not_listed_even_with_all_workspaces(repo):
    todo_id = _todo(repo, "Mine")

    listed = json.loads(
        list_todos(
            repo["path"],
            user_id=str(uuid.uuid4()),
            machine=repo["machine"],
            all_workspaces=True,
        )
    )

    assert todo_id not in _open_ids(listed)


@pytest.mark.parametrize("limit", [0, 201, True])
def test_limit_out_of_range_is_rejected(repo, limit):
    assert _listed(repo, limit=limit)["type"] == "validation"


def test_context_pointer_links_a_todo_with_a_plan(repo):
    todo_id = _todo(repo, "Planned")
    claim_todo(
        path=repo["path"],
        observation_id=todo_id,
        client_session_id=SESSION,
        **_caller(repo),
    )
    todo_progress(
        path=repo["path"],
        observation_id=todo_id,
        client_session_id=SESSION,
        plan_md="# Plan",
        **_caller(repo),
    )

    rows = json.loads(context(path=repo["path"], **_caller(repo)))["observations"]
    pointer = next(r for r in rows if r["id"] == todo_id)

    assert pointer["plan_url"] == f"https://memodi.example/o/{todo_id}"


@pytest.mark.asyncio
async def test_list_todos_is_a_core_tool():
    tools = {t.name: t for t in await _list_tools_with_deferred()}

    assert "memodi_list_todos" in CORE_TOOLS
    assert not getattr(tools["memodi_list_todos"], "defer_loading", False)
