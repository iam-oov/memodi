import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.server import CORE_TOOLS, _list_tools_with_deferred
from memodi.tools.memory import (
    context,
    save,
    search,
    search_for_prompt,
    search_hybrid,
    search_similar,
    set_coordinator,
)
from memodi.tools.session import session_end, session_start


@pytest.fixture(autouse=True)
def _schema():
    ensure_schema()


@pytest.fixture
def family(registered_workspace):
    # A workspace with one coordinator folder and one child repo, both direct
    # children of the registered root — siblings at the project level.
    root = registered_workspace["root"]
    names = {
        "coordinator": f"test-coord-{uuid.uuid4()}",
        "child": f"test-child-{uuid.uuid4()}",
    }
    return {
        **registered_workspace,
        "coordinator_name": names["coordinator"],
        "child_name": names["child"],
        "coordinator_path": f"{root}/{names['coordinator']}",
        "child_path": f"{root}/{names['child']}",
    }


def _caller(family: dict) -> dict:
    return {"user_id": family["user_id"], "machine": family["machine"]}


def _save(family: dict, path: str, title: str) -> None:
    ack = json.loads(
        save(
            path=path,
            title=title,
            content=f"{title} body about the quokka ledger reconciliation",
            type="decision",
            **_caller(family),
        )
    )
    assert "error" not in ack, ack


def _mark(family: dict, enabled: bool = True) -> dict:
    return json.loads(
        set_coordinator(
            path=family["coordinator_path"], enabled=enabled, **_caller(family)
        )
    )


def _context_titles(family: dict, path: str) -> list[str]:
    result = json.loads(context(path=path, **_caller(family)))
    return [o["title"] for o in result["observations"]]


def test_set_coordinator_marks_the_project(family):
    ack = _mark(family)

    assert ack == {"project": family["coordinator_name"], "coordinator": True}
    project = repository.get_project_by_name(
        family["coordinator_name"], family["workspace"]["id"]
    )
    assert project["metadata"]["coordinator"] is True


def test_set_coordinator_is_refused_at_the_workspace_root(family):
    result = json.loads(set_coordinator(path=family["root"], **_caller(family)))

    assert result["type"] == "validation"


def test_coordinator_context_sees_a_child_observation(family):
    _save(family, family["child_path"], "Child decision")
    _mark(family)

    assert "Child decision" in _context_titles(family, family["coordinator_path"])


def test_unmarked_folder_does_not_see_a_sibling(family):
    # Baseline: without the flag the coordinator folder is just another child.
    _save(family, family["child_path"], "Child decision")

    assert "Child decision" not in _context_titles(family, family["coordinator_path"])


def test_unmarking_restores_narrowing(family):
    _save(family, family["child_path"], "Child decision")
    _mark(family)
    _mark(family, enabled=False)

    assert "Child decision" not in _context_titles(family, family["coordinator_path"])


def test_child_never_sees_the_coordinator_memory(family):
    _mark(family)
    _save(family, family["coordinator_path"], "Coordinator plan")

    assert "Coordinator plan" not in _context_titles(family, family["child_path"])
    rows = json.loads(
        search_hybrid(
            path=family["child_path"], query="quokka ledger", **_caller(family)
        )
    )
    assert all(r["title"] != "Coordinator plan" for r in rows)


def test_coordinator_save_stays_in_its_own_project(family):
    _mark(family)
    _save(family, family["coordinator_path"], "Coordinator plan")

    result = json.loads(context(path=family["root"], **_caller(family)))
    match = next(o for o in result["observations"] if o["title"] == "Coordinator plan")
    assert match["project"] == family["coordinator_name"]


@pytest.mark.parametrize("tool", [search, search_hybrid, search_similar])
def test_coordinator_searches_reach_a_child(family, tool):
    _save(family, family["child_path"], "Child decision")
    _mark(family)

    rows = json.loads(
        tool(path=family["coordinator_path"], query="quokka ledger", **_caller(family))
    )
    assert any(r["title"] == "Child decision" for r in rows)


def test_coordinator_prompt_search_reaches_a_child_without_creating_projects(family):
    _save(family, family["child_path"], "Child decision")
    _mark(family)
    before = repository.get_project_names(family["workspace"]["id"])

    rows = json.loads(
        search_for_prompt(
            path=family["coordinator_path"], query="quokka ledger", **_caller(family)
        )
    )

    assert any(r["title"] == "Child decision" for r in rows)
    assert repository.get_project_names(family["workspace"]["id"]) == before


def test_explicit_project_still_narrows_a_coordinator(family):
    _save(family, family["child_path"], "Child decision")
    _mark(family)

    result = json.loads(
        context(
            path=family["coordinator_path"],
            project=family["coordinator_name"],
            **_caller(family),
        )
    )
    assert "Child decision" not in [o["title"] for o in result["observations"]]


def test_coordinator_resumes_only_its_own_session(family):
    _mark(family)
    # The child's session ends AFTER the coordinator's, so a workspace-wide
    # session scope would answer with the child's summary.
    for path, summary in [
        (family["coordinator_path"], "Coordinator session summary"),
        (family["child_path"], "Child session summary"),
    ]:
        session_start(path=path, **_caller(family))
        session_end(path=path, summary=summary, **_caller(family))

    last = json.loads(context(path=family["coordinator_path"], **_caller(family)))[
        "last_session"
    ]
    assert last["summary"] == "Coordinator session summary"


@pytest.mark.asyncio
async def test_set_coordinator_tool_is_deferred():
    tools = {t.name: t for t in await _list_tools_with_deferred()}

    assert "memodi_set_coordinator" in tools
    assert "memodi_set_coordinator" not in CORE_TOOLS
    dump = tools["memodi_set_coordinator"].model_dump(by_alias=True, exclude_none=True)
    assert dump.get("defer_loading") is True
