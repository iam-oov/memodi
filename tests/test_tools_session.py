import json
import secrets
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.session import session_end, session_start
from tests.conftest import _path


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-session-{uuid.uuid4()}"


def test_session_start_creates_session(registered_workspace, project_name):
    result = json.loads(
        session_start(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["started"] is True
    assert result["project"] == project_name
    assert "session_id" in result


def test_session_start_closes_previous(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    first = json.loads(session_start(**kwargs))
    second = json.loads(session_start(**kwargs))

    assert first["session_id"] != second["session_id"]

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    active = repository.get_active_session(proj["id"])
    assert str(active["id"]) == second["session_id"]
    assert _row(first["session_id"])["ended_at"] is not None


def test_session_end_with_summary(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    start_result = json.loads(
        session_start(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    session_id = start_result["session_id"]

    end_result = json.loads(
        session_end(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            summary="Worked on auth module",
        )
    )

    assert end_result["ended"] is True
    assert end_result["session_id"] == session_id
    assert end_result["summary"] == "Worked on auth module"


def test_session_end_without_active_session_auto_opens_and_persists(
    registered_workspace, project_name
):
    result = json.loads(
        session_end(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            summary="summary with no prior session_start",
        )
    )

    assert result["ended"] is True
    assert result["auto_started"] is True
    assert result["summary"] == "summary with no prior session_start"

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    assert repository.get_active_session(proj["id"]) is None
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "summary with no prior session_start"


def test_session_end_clears_active(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)
    session_end(**kwargs, summary="done")

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    active = repository.get_active_session(proj["id"])
    assert active is None


def test_session_end_rejects_empty_summary(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)

    result = json.loads(session_end(**kwargs, summary=""))

    assert result["type"] == "validation"

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    assert repository.get_active_session(proj["id"]) is not None


def test_session_end_rejects_whitespace_only_summary(
    registered_workspace, project_name
):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)

    result = json.loads(session_end(**kwargs, summary="   \n\t  "))

    assert result["type"] == "validation"


def test_session_end_empty_summary_does_not_clobber_real_summary(
    registered_workspace, project_name
):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)
    session_end(**kwargs, summary="Worked on the real feature")

    # A later attempt with a blank summary (e.g. a caller bug) must never
    # outrank this real summary in get_latest_session_summary.
    session_start(**kwargs)
    result = json.loads(session_end(**kwargs, summary=""))
    assert result["type"] == "validation"

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "Worked on the real feature"


def test_session_start_persists_client_session_id(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    client_session_id = str(uuid.uuid4())

    result = json.loads(
        session_start(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            client_session_id=client_session_id,
        )
    )

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    active = repository.get_active_session(proj["id"])
    assert str(active["id"]) == result["session_id"]
    assert active["client_session_id"] == client_session_id


def _active_count(project_id: str) -> int:
    """Rows still open for a project, whatever their tag."""
    return (
        get_connection()
        .execute(
            "SELECT COUNT(*) AS c FROM sessions "
            "WHERE project_id = %s AND ended_at IS NULL",
            (project_id,),
        )
        .fetchone()["c"]
    )


def _active_count_for(project_id: str, client_session_id: str | None) -> int:
    """Rows still open for a project carrying this exact tag — None counts
    the untagged ones."""
    return (
        get_connection()
        .execute(
            """
            SELECT COUNT(*) AS c FROM sessions
            WHERE project_id = %s
              AND ended_at IS NULL
              AND client_session_id IS NOT DISTINCT FROM %s
            """,
            (project_id, client_session_id),
        )
        .fetchone()["c"]
    )


def _row(session_id: str) -> dict:
    return dict(
        get_connection()
        .execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        .fetchone()
    )


def _proj(registered_workspace: dict, project_name: str) -> dict:
    return repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )


def test_end_session_never_overwrites_an_existing_summary(
    registered_workspace, project_name
):
    """Lost update guard: end_session only matches rows that are still open,
    so a second close returns None instead of replacing a real summary."""
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    session = repository.create_session(proj["id"])
    repository.end_session(session["id"], summary="the real recap")

    assert repository.end_session(session["id"], summary="a stray later close") is None
    assert _row(session["id"])["summary"] == "the real recap"


def test_session_end_losing_the_race_still_persists_the_summary_on_a_new_row(
    registered_workspace, project_name, monkeypatch
):
    """The targeted row closed between the read and the write (another
    window's hygiene close). The summary must land on a fresh row instead of
    silently overwriting the summary already there."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    client_session_id = str(uuid.uuid4())
    started = json.loads(session_start(**kwargs, client_session_id=client_session_id))
    session_end(
        **kwargs, summary="the recap already there", client_session_id=client_session_id
    )
    stale = _row(started["session_id"])

    monkeypatch.setattr(repository, "get_active_session_by_client_id", lambda *_: stale)

    result = json.loads(
        session_end(
            **kwargs, summary="the racing recap", client_session_id=client_session_id
        )
    )

    assert result["ended"] is True
    assert result["auto_started"] is True
    assert result["session_id"] != started["session_id"]
    assert _row(started["session_id"])["summary"] == "the recap already there"
    assert _row(result["session_id"])["summary"] == "the racing recap"
    assert _row(result["session_id"])["client_session_id"] == client_session_id


# --- A caller-supplied client_session_id is never trusted into SQL (F1) ---

NUL_ID = "window-a\x00window-b"
# High entropy on purpose: index tuples are compressed, so a repeated
# character this long still fits the btree and the hazard hides.
OVERLONG_ID = secrets.token_hex(1400)


def test_session_end_with_a_nul_byte_client_session_id_still_persists_the_summary(
    registered_workspace, project_name
):
    """A bad id must NEVER cost the summary: client_session_id is validated
    before any SQL, the bad one is ignored, the ack says why, and no driver
    text (index name, relation, HINT) can reach the caller."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }

    result = json.loads(
        session_end(
            **kwargs, summary="recap that must survive", client_session_id=NUL_ID
        )
    )

    assert result["ended"] is True
    assert result["client_session_id_ignored"] is True
    assert "NUL" in result["client_session_id_ignored_reason"]
    assert _row(result["session_id"])["client_session_id"] is None

    proj = _proj(registered_workspace, project_name)
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "recap that must survive"


def test_session_end_with_an_overlong_client_session_id_still_persists_the_summary(
    registered_workspace, project_name
):
    """Same contract for a length that would blow the partial btree index
    on client_session_id — capped at the HTTP boundary's own 256 limit so
    both writers to that column agree."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }

    result = json.loads(
        session_end(
            **kwargs, summary="recap that must survive", client_session_id=OVERLONG_ID
        )
    )

    assert result["ended"] is True
    assert result["client_session_id_ignored"] is True
    assert "256" in result["client_session_id_ignored_reason"]
    assert _row(result["session_id"])["client_session_id"] is None

    proj = _proj(registered_workspace, project_name)
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "recap that must survive"


def test_session_start_with_an_invalid_client_session_id_stores_no_tag(
    registered_workspace, project_name
):
    """The same guard on the other writer to the indexed column: the
    session still opens, untagged, and the ack reports the ignored id."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }

    result = json.loads(session_start(**kwargs, client_session_id=OVERLONG_ID))

    assert result["started"] is True
    assert result["client_session_id_ignored"] is True
    assert _row(result["session_id"])["client_session_id"] is None


def test_session_end_with_an_invalid_client_session_id_leaves_tagged_rows_alone(
    registered_workspace, project_name
):
    """An ignored id degrades to the untagged identity — never to "whichever
    session is newest", so it can never close another window's row."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    tagged = json.loads(session_start(**kwargs, client_session_id=str(uuid.uuid4())))

    result = json.loads(
        session_end(
            **kwargs, summary="recap of the bad-id caller", client_session_id=NUL_ID
        )
    )

    assert result["session_id"] != tagged["session_id"]
    row = _row(tagged["session_id"])
    assert row["ended_at"] is None
    assert row["summary"] is None


# --- Blank means the SAME thing on both lifecycle ends (F2) ---


def test_session_end_with_blank_client_session_id_does_not_touch_a_tagged_session(
    registered_workspace, project_name
):
    """A blank id normalizes to the UNTAGGED identity on session_start, so
    it must mean exactly that on session_end too — never "target any newest
    active session", which would close another window's tagged row and
    write this summary there."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    tagged = json.loads(session_start(**kwargs, client_session_id=str(uuid.uuid4())))

    result = json.loads(
        session_end(
            **kwargs, summary="the untagged caller's recap", client_session_id="   "
        )
    )

    assert result["ended"] is True
    assert result["auto_started"] is True
    assert result["session_id"] != tagged["session_id"]
    row = _row(tagged["session_id"])
    assert row["ended_at"] is None
    assert row["summary"] is None


def test_session_end_with_blank_client_session_id_closes_its_own_untagged_row(
    registered_workspace, project_name
):
    """The untagged identity still finds its own untagged row — a blank id
    is not a reason to auto-start."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    untagged = json.loads(session_start(**kwargs))

    result = json.loads(
        session_end(
            **kwargs, summary="the untagged caller's recap", client_session_id=""
        )
    )

    assert result["auto_started"] is False
    assert result["session_id"] == untagged["session_id"]


# --- Concurrent sessions per project (two Claude Code windows, same folder) ---


def test_session_start_with_different_client_id_leaves_other_session_active(
    registered_workspace, project_name
):
    """The root regression: window B's session_start must not close window
    A's session — each client_session_id gets its own active row."""
    path = _path(registered_workspace, project_name)
    base_kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    result_a = json.loads(session_start(**base_kwargs, client_session_id=id_a))
    result_b = json.loads(session_start(**base_kwargs, client_session_id=id_b))

    proj = _proj(registered_workspace, project_name)
    active_a = repository.get_active_session_by_client_id(proj["id"], id_a)
    active_b = repository.get_active_session_by_client_id(proj["id"], id_b)

    assert active_a is not None
    assert str(active_a["id"]) == result_a["session_id"]
    assert active_b is not None
    assert str(active_b["id"]) == result_b["session_id"]
    assert active_a["id"] != active_b["id"]
    assert _active_count(proj["id"]) == 2


def test_session_start_same_client_id_twice_keeps_one_active_row(
    registered_workspace, project_name
):
    path = _path(registered_workspace, project_name)
    client_session_id = str(uuid.uuid4())
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
        "client_session_id": client_session_id,
    }

    first = json.loads(session_start(**kwargs))
    second = json.loads(session_start(**kwargs))

    assert first["session_id"] != second["session_id"]

    proj = _proj(registered_workspace, project_name)
    active = repository.get_active_session_by_client_id(proj["id"], client_session_id)
    assert str(active["id"]) == second["session_id"]
    assert _row(first["session_id"])["ended_at"] is not None
    assert _active_count_for(proj["id"], client_session_id) == 1
    assert _active_count(proj["id"]) == 1


def test_untagged_session_start_twice_closes_the_previous_untagged_row(
    registered_workspace, project_name
):
    """No accumulation regression for hookless clients: matching by
    IS NOT DISTINCT FROM (not =) makes NULL match NULL, so an untagged
    caller still cleans up its own previous untagged row."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }

    first = json.loads(session_start(**kwargs))
    second = json.loads(session_start(**kwargs))

    proj = _proj(registered_workspace, project_name)
    assert _row(first["session_id"])["ended_at"] is not None
    assert _row(second["session_id"])["ended_at"] is None
    assert _active_count_for(proj["id"], None) == 1
    assert _active_count(proj["id"]) == 1


def test_session_end_with_client_id_closes_only_that_session(
    registered_workspace, project_name
):
    """The reported bug: A's session_end must close A's row and write A's
    summary there, while B's row stays active with no summary."""
    path = _path(registered_workspace, project_name)
    base_kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    result_a = json.loads(session_start(**base_kwargs, client_session_id=id_a))
    result_b = json.loads(session_start(**base_kwargs, client_session_id=id_b))

    end_result = json.loads(
        session_end(**base_kwargs, summary="A's real recap", client_session_id=id_a)
    )

    assert end_result["ended"] is True
    assert end_result["auto_started"] is False
    assert end_result["session_id"] == result_a["session_id"]
    assert end_result["summary"] == "A's real recap"

    row_a = _row(result_a["session_id"])
    assert row_a["summary"] == "A's real recap"
    assert row_a["ended_at"] is not None

    row_b = _row(result_b["session_id"])
    assert row_b["summary"] is None
    assert row_b["ended_at"] is None

    proj = _proj(registered_workspace, project_name)
    active_b = repository.get_active_session_by_client_id(proj["id"], id_b)
    assert active_b is not None
    assert str(active_b["id"]) == result_b["session_id"]


def test_session_end_with_client_id_already_closed_auto_starts_tagged_row(
    registered_workspace, project_name
):
    """The window's own row is already closed (its SessionEnd hygiene hook
    ran first). The 'never lose a summary' contract still holds: a new row
    is created TAGGED with that id and closed on the spot."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    client_session_id = str(uuid.uuid4())
    started = json.loads(session_start(**kwargs, client_session_id=client_session_id))
    proj = _proj(registered_workspace, project_name)
    repository.close_session_by_client_id(
        registered_workspace["workspace"]["id"], client_session_id
    )
    assert _row(started["session_id"])["ended_at"] is not None

    result = json.loads(
        session_end(
            **kwargs,
            summary="the recap after the hygiene close",
            client_session_id=client_session_id,
        )
    )

    assert result["ended"] is True
    assert result["auto_started"] is True
    assert result["session_id"] != started["session_id"]

    new_row = _row(result["session_id"])
    assert new_row["client_session_id"] == client_session_id
    assert new_row["summary"] == "the recap after the hygiene close"
    assert _row(started["session_id"])["summary"] is None

    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "the recap after the hygiene close"


def test_session_end_with_a_client_id_never_seen_auto_starts_tagged_row(
    registered_workspace, project_name
):
    """The hook's POST never landed, so no row with this id ever existed —
    the auto-started row still carries the id, so a later close by id can
    match it and attribution stays truthful."""
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    client_session_id = str(uuid.uuid4())

    result = json.loads(
        session_end(
            **kwargs,
            summary="closed with no matching active session",
            client_session_id=client_session_id,
        )
    )

    assert result["ended"] is True
    assert result["auto_started"] is True
    assert _row(result["session_id"])["client_session_id"] == client_session_id

    proj = _proj(registered_workspace, project_name)
    assert (
        repository.get_active_session_by_client_id(proj["id"], client_session_id)
        is None
    )
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "closed with no matching active session"


@pytest.mark.parametrize("close_order", ["a_then_b", "b_then_a"])
def test_concurrent_windows_closing_in_either_order_preserves_both_summaries(
    registered_workspace, project_name, close_order
):
    """Each summary lands on the row of the window that wrote it, in either
    close order — asserting per row, not just that get_latest_session_summary
    happens to return the later text (which holds even when the two
    summaries are swapped onto each other's rows)."""
    path = _path(registered_workspace, project_name)
    base_kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    row_a = json.loads(session_start(**base_kwargs, client_session_id=id_a))
    row_b = json.loads(session_start(**base_kwargs, client_session_id=id_b))

    proj = _proj(registered_workspace, project_name)

    def close_a():
        session_end(**base_kwargs, summary="A's recap", client_session_id=id_a)

    def close_b():
        session_end(**base_kwargs, summary="B's recap", client_session_id=id_b)

    if close_order == "a_then_b":
        close_a()
        close_b()
        later_summary = "B's recap"
    else:
        close_b()
        close_a()
        later_summary = "A's recap"

    assert _row(row_a["session_id"])["summary"] == "A's recap"
    assert _row(row_b["session_id"])["summary"] == "B's recap"
    assert _active_count(proj["id"]) == 0

    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == later_summary
