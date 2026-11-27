import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.server import CORE_TOOLS, _list_tools_with_deferred
from memodi.tools.memory import (
    claim_todo,
    context,
    digest_for_session_start,
    get_observation,
    release_todo,
    reopen_todo,
    resolve_todo,
    save,
    todo_progress,
)
from tests.test_tools_memory import _cleanup_workspace, _extra_workspace

SESSION_A = "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION_B = "22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _schema():
    ensure_schema()


@pytest.fixture
def repo(registered_workspace):
    name = f"test-claims-{uuid.uuid4()}"
    return {
        **registered_workspace,
        "path": f"{registered_workspace['root']}/{name}",
    }


def _caller(ws: dict) -> dict:
    return {"user_id": ws["user_id"], "machine": ws["machine"]}


def _todo(ws: dict, title: str = "Rotate the staging key") -> str:
    ack = json.loads(
        save(
            path=ws["path"],
            title=title,
            content=f"{title} {uuid.uuid4()}",
            type="decision",
            horizon="todo",
            **_caller(ws),
        )
    )
    return ack["id"]


def _claim(ws: dict, todo_id: str, session: str = SESSION_A, **extra) -> dict:
    return json.loads(
        claim_todo(
            path=ws["path"],
            observation_id=todo_id,
            client_session_id=session,
            **_caller(ws),
            **extra,
        )
    )


def _progress(ws: dict, todo_id: str, session: str = SESSION_A, **extra) -> dict:
    return json.loads(
        todo_progress(
            path=ws["path"],
            observation_id=todo_id,
            client_session_id=session,
            **_caller(ws),
            **extra,
        )
    )


def _release(ws: dict, todo_id: str, session: str = SESSION_A) -> dict:
    return json.loads(
        release_todo(
            path=ws["path"],
            observation_id=todo_id,
            client_session_id=session,
            **_caller(ws),
        )
    )


def _read(ws: dict, todo_id: str) -> dict:
    return json.loads(
        get_observation(path=ws["path"], observation_id=todo_id, **_caller(ws))
    )


def _digest(ws: dict) -> str:
    return json.loads(digest_for_session_start(path=ws["path"], **_caller(ws)))[
        "digest"
    ]


def _expire_claim(todo_id: str) -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        "UPDATE observations SET claimed_until = now() - interval '1 minute'"
        " WHERE id = %s",
        (todo_id,),
    )
    conn.commit()


def test_claim_takes_a_free_todo(repo):
    todo_id = _todo(repo)

    ack = _claim(repo, todo_id, plan_ref="https://slack.example/p1", phase_total=4)

    assert ack["claimed"] is True
    assert ack["claimed_by"] == f"{repo['machine']}/{SESSION_A}"
    assert ack["plan_ref"] == "https://slack.example/p1"
    assert ack["phase_total"] == 4
    assert ack["claimed_until"]


def test_second_session_is_refused_and_told_who_holds_it(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, holder_label="Mi Todo Slack · worker compliance")

    ack = _claim(repo, todo_id, session=SESSION_B)

    assert ack["claimed"] is False
    assert ack["reason"] == "held_by_another"
    assert ack["claimed_by"] == f"{repo['machine']}/{SESSION_A}"
    assert ack["claimed_label"] == "Mi Todo Slack · worker compliance"


def test_same_session_reclaims_and_keeps_its_label(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, holder_label="worker billing")

    ack = _claim(repo, todo_id)

    assert ack["claimed"] is True
    assert ack["claimed_label"] == "worker billing"


def test_an_expired_claim_can_be_taken_by_another_session(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, holder_label="crashed worker")
    _expire_claim(todo_id)

    ack = _claim(repo, todo_id, session=SESSION_B)

    assert ack["claimed"] is True
    assert ack["claimed_by"].endswith(SESSION_B)
    assert "claimed_label" not in ack


def test_lease_minutes_sets_the_claim_length(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, lease_minutes=360)

    remaining = (
        get_connection()
        .execute(
            "SELECT EXTRACT(EPOCH FROM claimed_until - now()) / 60 AS minutes,"
            " claim_minutes FROM observations WHERE id = %s",
            (todo_id,),
        )
        .fetchone()
    )
    get_connection().rollback()

    assert 355 < remaining["minutes"] <= 360
    assert remaining["claim_minutes"] == 360


@pytest.mark.parametrize("minutes", [0, 1441, True])
def test_lease_minutes_out_of_range_is_rejected(repo, minutes):
    ack = _claim(repo, _todo(repo), lease_minutes=minutes)

    assert ack["type"] == "validation"


def test_progress_by_the_holder_updates_phase_and_renews_the_lease(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, phase_total=4, lease_minutes=360)

    ack = _progress(repo, todo_id, phase_current=2, phase_label="Fix")

    assert ack["updated"] is True
    assert (ack["phase_current"], ack["phase_total"], ack["phase_label"]) == (
        2,
        4,
        "Fix",
    )
    minutes = (
        get_connection()
        .execute(
            "SELECT EXTRACT(EPOCH FROM claimed_until - now()) / 60 AS m"
            " FROM observations WHERE id = %s",
            (todo_id,),
        )
        .fetchone()["m"]
    )
    get_connection().rollback()
    assert minutes > 355


def test_progress_from_another_session_is_refused(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, phase_total=4)

    ack = _progress(repo, todo_id, session=SESSION_B, phase_current=3)

    assert ack["updated"] is False
    assert ack["reason"] == "held_by_another"
    assert "phase_current" not in _read(repo, todo_id)


def test_progress_without_a_claim_is_refused(repo):
    ack = _progress(repo, _todo(repo), phase_current=1)

    assert ack == {"updated": False, "reason": "not_holder"}


def test_phase_beyond_the_total_is_rejected(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, phase_total=4)

    ack = _progress(repo, todo_id, phase_current=5)

    assert ack["type"] == "validation"
    assert _read(repo, todo_id)["phase_total"] == 4


@pytest.mark.parametrize("value", [0, -1, True])
def test_phase_must_be_a_positive_whole_number(repo, value):
    todo_id = _todo(repo)
    _claim(repo, todo_id)

    assert _progress(repo, todo_id, phase_current=value)["type"] == "validation"


def test_release_frees_the_todo_but_keeps_plan_and_phase(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, plan_ref="/plans/rotate.md", phase_total=4)
    _progress(repo, todo_id, phase_current=2)

    ack = _release(repo, todo_id)
    stored = _read(repo, todo_id)

    assert ack["released"] is True
    assert "claimed_by" not in stored
    assert (stored["plan_ref"], stored["phase_current"]) == ("/plans/rotate.md", 2)
    assert _claim(repo, todo_id, session=SESSION_B)["claimed"] is True


def test_only_the_holder_can_release(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id)

    assert _release(repo, todo_id, session=SESSION_B)["released"] is False
    assert _read(repo, todo_id)["claimed_by"].endswith(SESSION_A)


def test_resolve_clears_the_claim_and_stores_the_outcome(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id)

    ack = json.loads(
        resolve_todo(
            path=repo["path"],
            observation_id=todo_id,
            outcome="Rotated; see [[ops/staging-key-rotation]]",
            **_caller(repo),
        )
    )
    stored = _read(repo, todo_id)

    assert ack["resolved"] is True
    assert stored["outcome"] == "Rotated; see [[ops/staging-key-rotation]]"
    assert "claimed_by" not in stored


def test_reopen_drops_the_old_outcome(repo):
    todo_id = _todo(repo)
    resolve_todo(
        path=repo["path"], observation_id=todo_id, outcome="Done", **_caller(repo)
    )

    reopen_todo(path=repo["path"], observation_id=todo_id, **_caller(repo))

    assert "outcome" not in _read(repo, todo_id)


def test_a_resolved_todo_cannot_be_claimed(repo):
    todo_id = _todo(repo)
    resolve_todo(path=repo["path"], observation_id=todo_id, **_caller(repo))

    ack = _claim(repo, todo_id)

    assert (ack["claimed"], ack["reason"]) == (False, "not_found")


def test_a_note_that_is_not_a_todo_cannot_be_claimed(repo):
    ack = json.loads(
        save(
            path=repo["path"],
            title="Plain decision",
            content="Not pending work",
            type="decision",
            **_caller(repo),
        )
    )

    assert _claim(repo, ack["id"])["reason"] == "not_found"


def test_claim_cannot_reach_another_workspace(repo):
    other = _extra_workspace(repo["user_id"])
    try:
        foreign = json.loads(
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

        assert _claim(repo, foreign["id"])["reason"] == "not_found"
    finally:
        _cleanup_workspace(other["workspace"])


def test_claim_requires_a_session_id(repo):
    ack = _claim(repo, _todo(repo), session="  ")

    assert ack["type"] == "validation"


def test_short_id_prefix_works_like_the_full_id(repo):
    todo_id = _todo(repo)

    ack = _claim(repo, todo_id[:4])

    assert ack["claimed"] is True
    assert ack["id"] == todo_id


def test_an_ambiguous_prefix_is_rejected(repo):
    first, second = _todo(repo, "First"), _todo(repo, "Second")
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        "UPDATE observations SET id = %s WHERE id = %s",
        (f"abcd{first[4:]}", first),
    )
    conn.execute(
        "UPDATE observations SET id = %s WHERE id = %s",
        (f"abcd{second[4:]}", second),
    )
    conn.commit()

    ack = _claim(repo, "abcd")

    assert ack["type"] == "validation"
    assert "more than one" in ack["error"]


def test_a_prefix_that_is_not_hex_is_rejected(repo):
    assert _claim(repo, "nope")["type"] == "validation"


def test_digest_shows_holder_phase_and_plan(repo):
    todo_id = _todo(repo, "Rotate the staging key")
    _claim(
        repo,
        todo_id,
        holder_label="Mi Todo Slack",
        plan_ref="https://slack.example/p1",
        phase_total=4,
    )
    _progress(repo, todo_id, phase_current=2, phase_label="Fix")

    digest = _digest(repo)

    line = next(row for row in digest.splitlines() if "Rotate the staging key" in row)
    assert f"[{todo_id[:4]}]" in line
    assert "in progress F-2/4 (Fix)" in line
    assert "Mi Todo Slack" in line
    assert "plan: https://slack.example/p1" in line


def test_digest_shows_an_expired_claim_as_free_with_its_last_phase(repo):
    todo_id = _todo(repo, "Stalled migration")
    _claim(repo, todo_id, phase_total=5)
    _progress(repo, todo_id, phase_current=3)
    _expire_claim(todo_id)

    line = next(row for row in _digest(repo).splitlines() if "Stalled migration" in row)

    assert "free, last progress F-3/5" in line
    assert "in progress" not in line


def test_context_pointer_carries_the_claim_while_active(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, plan_ref="/plans/rotate.md", phase_total=4)

    rows = json.loads(context(path=repo["path"], **_caller(repo)))["observations"]
    pointer = next(r for r in rows if r["id"] == todo_id)

    assert pointer["claimed_by"].endswith(SESSION_A)
    assert pointer["plan_ref"] == "/plans/rotate.md"
    assert pointer["phase_total"] == 4


def test_context_pointer_drops_an_expired_claim(repo):
    todo_id = _todo(repo)
    _claim(repo, todo_id, holder_label="gone")
    _expire_claim(todo_id)

    rows = json.loads(context(path=repo["path"], **_caller(repo)))["observations"]
    pointer = next(r for r in rows if r["id"] == todo_id)

    assert "claimed_by" not in pointer
    assert "claimed_label" not in pointer


@pytest.mark.asyncio
async def test_claim_tools_are_deferred():
    tools = {t.name: t for t in await _list_tools_with_deferred()}

    for name in ("memodi_claim_todo", "memodi_todo_progress", "memodi_release_todo"):
        assert name in tools
        assert name not in CORE_TOOLS
        assert tools[name].defer_loading is True
