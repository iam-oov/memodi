import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    context,
    digest_for_session_start,
    get_observation,
    resolve_todo,
    save,
    search_for_prompt,
    search_hybrid,
    set_coordinator,
)
from tests.test_tools_memory import _cleanup_workspace, _extra_workspace


@pytest.fixture(autouse=True)
def _schema():
    ensure_schema()


@pytest.fixture
def repo(registered_workspace):
    name = f"test-horizon-{uuid.uuid4()}"
    return {
        **registered_workspace,
        "name": name,
        "path": f"{registered_workspace['root']}/{name}",
    }


def _caller(ws: dict) -> dict:
    return {"user_id": ws["user_id"], "machine": ws["machine"]}


def _save(ws: dict, title: str, type: str = "discovery", path=None, **extra) -> dict:
    return json.loads(
        save(
            path=path or ws["path"],
            title=title,
            content=f"{title} about the wombat ledger {uuid.uuid4()}",
            type=type,
            **_caller(ws),
            **extra,
        )
    )


def _context(ws: dict, path=None, **extra) -> list[dict]:
    return json.loads(context(path=path or ws["path"], **_caller(ws), **extra))[
        "observations"
    ]


def _titles(rows: list[dict]) -> list[str]:
    return [r["title"] for r in rows]


def _age(observation_id: str, days: int) -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        "UPDATE observations SET updated_at = now() - make_interval(days => %s)"
        " WHERE id = %s",
        (days, observation_id),
    )
    conn.commit()


@pytest.mark.parametrize(
    ("type_", "expected"),
    [
        ("preference", "long"),
        ("pattern", "long"),
        ("decision", "medium"),
        ("architecture", "medium"),
        ("discovery", "short"),
        ("config", "short"),
        ("bugfix", "short"),
    ],
)
def test_horizon_defaults_from_type(repo, type_, expected):
    assert _save(repo, f"Default {type_}", type=type_)["horizon"] == expected


def test_explicit_horizon_wins_over_type(repo):
    assert (
        _save(repo, "Durable gotcha", type="discovery", horizon="long")["horizon"]
        == "long"
    )


def test_invalid_horizon_is_rejected(repo):
    ack = _save(repo, "Bad horizon", horizon="forever")

    assert ack["type"] == "validation"
    assert "horizon" in ack["error"]


def test_upsert_keeps_the_horizon_unless_one_is_passed(repo):
    _save(repo, "Evolving rule", horizon="long", topic_key="test/evolving")

    kept = _save(repo, "Evolving rule v2", topic_key="test/evolving")
    changed = _save(
        repo, "Evolving rule v3", topic_key="test/evolving", horizon="medium"
    )

    assert kept["horizon"] == "long"
    assert changed["horizon"] == "medium"


def test_context_groups_by_horizon_in_fixed_order(repo):
    _save(repo, "Diary entry", type="config")
    _save(repo, "Chosen approach", type="decision")
    _save(repo, "Team convention", type="pattern")
    _save(repo, "Write the runbook", type="decision", horizon="todo")

    rows = _context(repo)

    assert [r["horizon"] for r in rows] == ["todo", "long", "medium", "short"]
    assert _titles(rows) == [
        "Write the runbook",
        "Team convention",
        "Chosen approach",
        "Diary entry",
    ]


def test_context_caps_each_horizon_separately(repo):
    for i in range(3):
        _save(repo, f"Convention {i}", type="pattern")
    _save(repo, "Only diary entry", type="config")

    rows = _context(repo, limit=2)

    assert [r["horizon"] for r in rows] == ["long", "long", "short"]


def test_a_flood_of_diary_entries_no_longer_buries_long_term_knowledge(repo):
    _save(repo, "Old but durable convention", type="pattern")
    for i in range(15):
        _save(repo, f"Status update {i}", type="config")

    assert "Old but durable convention" in _titles(_context(repo))


def test_expired_short_note_leaves_context_and_prompt_hook_but_stays_searchable(repo):
    obs = _save(repo, "Wombat ledger status", type="config")
    _age(obs["id"], days=8)

    prompt_rows = json.loads(
        search_for_prompt(path=repo["path"], query="wombat ledger", **_caller(repo))
    )
    hybrid_rows = json.loads(
        search_hybrid(path=repo["path"], query="wombat ledger status", **_caller(repo))
    )

    assert "Wombat ledger status" not in _titles(_context(repo))
    assert "Wombat ledger status" not in _titles(prompt_rows)
    assert "Wombat ledger status" in _titles(hybrid_rows)


def test_recent_short_note_still_surfaces(repo):
    obs = _save(repo, "Fresh status", type="config")
    _age(obs["id"], days=6)

    assert "Fresh status" in _titles(_context(repo))


def test_old_long_and_medium_notes_never_expire(repo):
    long_obs = _save(repo, "Ancient convention", type="pattern")
    medium_obs = _save(repo, "Ancient decision", type="decision")
    _age(long_obs["id"], days=400)
    _age(medium_obs["id"], days=400)

    titles = _titles(_context(repo))

    assert "Ancient convention" in titles
    assert "Ancient decision" in titles


def test_open_todo_surfaces_in_context_and_digest(repo):
    _save(repo, "Rotate the staging key", horizon="todo")

    digest = json.loads(digest_for_session_start(path=repo["path"], **_caller(repo)))

    assert "Rotate the staging key" in _titles(_context(repo))
    assert "open todos" in digest["digest"]
    assert "Rotate the staging key" in digest["digest"]


def test_resolved_todo_leaves_context_and_digest(repo):
    todo = _save(repo, "Rotate the staging key", horizon="todo")

    ack = json.loads(
        resolve_todo(path=repo["path"], observation_id=todo["id"], **_caller(repo))
    )
    digest = json.loads(digest_for_session_start(path=repo["path"], **_caller(repo)))
    stored = json.loads(
        get_observation(path=repo["path"], observation_id=todo["id"], **_caller(repo))
    )

    assert ack["resolved"] is True
    assert "Rotate the staging key" not in _titles(_context(repo))
    assert "Rotate the staging key" not in digest["digest"]
    assert stored["resolved_at"]


def test_resolve_todo_refuses_a_note_that_is_not_a_todo(repo):
    note = _save(repo, "Plain decision", type="decision")

    ack = json.loads(
        resolve_todo(path=repo["path"], observation_id=note["id"], **_caller(repo))
    )

    assert ack["resolved"] is False


def test_resolve_todo_rejects_an_invalid_id(repo):
    ack = json.loads(
        resolve_todo(path=repo["path"], observation_id="nope", **_caller(repo))
    )

    assert ack["type"] == "validation"


def test_resolve_todo_cannot_reach_another_workspace(repo):
    other = _extra_workspace(repo["user_id"])
    try:
        todo = json.loads(
            save(
                path=f"{other['root']}/elsewhere",
                title="Foreign todo",
                content="Lives in another workspace",
                type="decision",
                horizon="todo",
                user_id=other["user_id"],
                machine=other["machine"],
            )
        )

        ack = json.loads(
            resolve_todo(path=repo["path"], observation_id=todo["id"], **_caller(repo))
        )

        assert ack["resolved"] is False
    finally:
        _cleanup_workspace(other["workspace"])


def test_scope_rules_still_apply_per_horizon(registered_workspace):
    root = registered_workspace["root"]
    coordinator = f"{root}/test-coord-{uuid.uuid4()}"
    child = f"{root}/test-child-{uuid.uuid4()}"
    sibling = f"{root}/test-sibling-{uuid.uuid4()}"
    _save(registered_workspace, "Child convention", type="pattern", path=child)
    _save(registered_workspace, "Child todo", horizon="todo", path=child)
    set_coordinator(path=coordinator, **_caller(registered_workspace))

    coordinator_titles = _titles(_context(registered_workspace, path=coordinator))
    sibling_titles = _titles(_context(registered_workspace, path=sibling))

    assert {"Child convention", "Child todo"} <= set(coordinator_titles)
    assert not {"Child convention", "Child todo"} & set(sibling_titles)
