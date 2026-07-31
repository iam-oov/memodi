import json
import uuid

import pytest
from psycopg.pq import TransactionStatus

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    context,
    delete,
    get_observation,
    list_projects,
    save,
    search,
    search_global,
    search_hybrid,
    search_similar,
)
from memodi.tools.session import session_end, session_start
from tests.conftest import _path, cleanup_rows

DB_INTERNALS = (
    "invalid input syntax",
    "unnamed portal",
    "operator does not exist",
    "psycopg",
    "select",
    "where",
    "$1",
)


def _assert_no_db_internals(ack: dict) -> None:
    payload = json.dumps(ack).lower()
    leaked = [marker for marker in DB_INTERNALS if marker in payload]
    assert not leaked, f"ack leaked database internals {leaked}: {payload}"


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-memodi-{uuid.uuid4()}"


def _extra_workspace(user_id: str) -> dict:
    """A second workspace owned by the given user, for cross-workspace tests."""
    machine = f"test-machine-{uuid.uuid4()}"
    root = f"/tmp/test-extra-{uuid.uuid4()}"
    name = f"test-extra-ws-{uuid.uuid4()}"
    workspace = repository.workspace_start(user_id, machine, root, name)
    return {
        "user_id": user_id,
        "machine": machine,
        "root": root,
        "workspace": workspace,
    }


def _backdate_updated_at(observation_id: str, hours: int) -> None:
    """Pin updated_at so a most-recent-first assertion cannot pass by
    riding on insertion order."""
    conn = get_connection()
    conn.execute(
        "UPDATE observations SET updated_at = now() - make_interval(hours => %s) "
        "WHERE id = %s",
        (hours, observation_id),
    )
    conn.commit()


def _cleanup_workspace(workspace: dict) -> None:
    ws_id = workspace["id"]
    cleanup_rows(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    )
    cleanup_rows(
        """
        DELETE FROM workflow_transitions WHERE workflow_id IN (
            SELECT wf.id FROM workflows wf
            JOIN projects p ON p.id = wf.project_id
            WHERE p.workspace_id = %s
        )
        """,
        (ws_id,),
    )
    cleanup_rows(
        "DELETE FROM workflows WHERE project_id IN "
        "(SELECT id FROM projects WHERE workspace_id = %s)",
        (ws_id,),
    )
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws_id,))


def test_save_and_search(registered_workspace, project_name):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Authentication decision",
        content="We decided to use JWT tokens for stateless auth",
        type="decision",
    )

    results = json.loads(
        search(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="JWT tokens",
        )
    )
    assert len(results) >= 1
    assert any("Authentication decision" in r["title"] for r in results)


def test_save_upsert_by_topic_key(registered_workspace, project_name):
    topic = "architecture/auth-model"
    path = _path(registered_workspace, project_name)

    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Auth model v1",
        content="First version of auth model using sessions",
        type="architecture",
        topic_key=topic,
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Auth model v2",
        content="Updated auth model using JWT tokens",
        type="architecture",
        topic_key=topic,
    )

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    observations = repository.get_recent_observations(proj["id"])

    topic_obs = [o for o in observations if o["topic_key"] == topic]
    assert len(topic_obs) == 1
    assert topic_obs[0]["revision_count"] == 2
    assert topic_obs[0]["title"] == "Auth model v2"


def test_context_returns_recent(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    titles = ["First obs", "Second obs", "Third obs"]
    for title in titles:
        save(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title=title,
            content=f"Content for {title}",
            type="discovery",
        )

    results = json.loads(
        context(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            limit=10,
        )
    )
    result_titles = [r["title"] for r in results["observations"]]

    assert "Third obs" in result_titles
    assert "Second obs" in result_titles
    assert "First obs" in result_titles
    assert result_titles.index("Third obs") < result_titles.index("First obs")


def test_context_returns_workspace_wide_observations(registered_workspace):
    """Regression test for the cross-machine 'what were we working on?' bug:
    an observation saved via project A must surface in project B's context
    call when both resolve to the same workspace, labeled with A's name."""
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    path_a = f"{registered_workspace['root']}/{proj_a}"
    path_b = f"{registered_workspace['root']}/{proj_b}"

    save(
        path=path_a,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Cross-project observation",
        content="Saved from project A, should surface via project B's context",
        type="discovery",
    )

    results = json.loads(
        context(
            path=path_b,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    match = next(
        (
            o
            for o in results["observations"]
            if o["title"] == "Cross-project observation"
        ),
        None,
    )
    assert match is not None
    assert match["project"] == proj_a


def test_context_never_crosses_workspaces(registered_workspace):
    """Guard: workspace-wide context must stop at the workspace boundary —
    an accidental broadening of the WHERE clause would surface observations
    from the caller's OTHER workspaces."""
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    other = _extra_workspace(registered_workspace["user_id"])

    try:
        save(
            path=f"{other['root']}/{proj_b}",
            user_id=other["user_id"],
            machine=other["machine"],
            title="Observation in the other workspace",
            content="Must never surface through workspace A's context",
            type="decision",
        )

        results = json.loads(
            context(
                path=f"{registered_workspace['root']}/{proj_a}",
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
            )
        )
        titles = [o["title"] for o in results["observations"]]
        assert "Observation in the other workspace" not in titles
    finally:
        _cleanup_workspace(other["workspace"])


def test_context_last_session_from_another_project_in_workspace(registered_workspace):
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    path_a = f"{registered_workspace['root']}/{proj_a}"
    path_b = f"{registered_workspace['root']}/{proj_b}"

    session_start(
        path=path_a,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    session_end(
        path=path_a,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        summary="Session summary from project A",
    )

    results = json.loads(
        context(
            path=path_b,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    last_session = results["last_session"]
    assert last_session is not None
    assert last_session["summary"] == "Session summary from project A"
    assert last_session["project"] == proj_a


def test_list_projects(registered_workspace, project_name):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Some observation",
        content="Some content",
        type="config",
    )

    results = json.loads(list_projects(registered_workspace["user_id"]))
    names = [r["name"] for r in results]
    assert project_name in names


def test_workspace_isolation(registered_workspace):
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    other = _extra_workspace(registered_workspace["user_id"])

    try:
        save(
            path=f"{registered_workspace['root']}/{proj_a}",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Decision in workspace A",
            content="JWT tokens for workspace A auth",
            type="decision",
        )
        save(
            path=f"{other['root']}/{proj_b}",
            user_id=other["user_id"],
            machine=other["machine"],
            title="Decision in workspace B",
            content="Session cookies for workspace B auth",
            type="decision",
        )

        results_a = json.loads(
            search(
                path=f"{registered_workspace['root']}/{proj_a}",
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
                query="auth",
            )
        )
        titles_a = [r["title"] for r in results_a]
        assert "Decision in workspace A" in titles_a
        assert "Decision in workspace B" not in titles_a

        results_b = json.loads(
            search(
                path=f"{other['root']}/{proj_b}",
                user_id=other["user_id"],
                machine=other["machine"],
                query="auth",
            )
        )
        titles_b = [r["title"] for r in results_b]
        assert "Decision in workspace B" in titles_b
        assert "Decision in workspace A" not in titles_b
    finally:
        cleanup_rows(
            "DELETE FROM observations WHERE project_id IN "
            "(SELECT id FROM projects WHERE name = %s)",
            (proj_a,),
        )
        cleanup_rows("DELETE FROM projects WHERE name = %s", (proj_a,))
        _cleanup_workspace(other["workspace"])


def test_search_global_crosses_caller_workspaces(registered_workspace):
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    second_ws = _extra_workspace(registered_workspace["user_id"])

    try:
        save(
            path=f"{registered_workspace['root']}/{proj_a}",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Global decision alpha",
            content="Hexagonal architecture for ws A",
            type="architecture",
        )
        save(
            path=f"{second_ws['root']}/{proj_b}",
            user_id=second_ws["user_id"],
            machine=second_ws["machine"],
            title="Global decision beta",
            content="Hexagonal architecture for ws B",
            type="architecture",
        )

        results = json.loads(
            search_global(
                user_id=registered_workspace["user_id"], query="hexagonal architecture"
            )
        )
        titles = [r["title"] for r in results]
        assert "Global decision alpha" in titles
        assert "Global decision beta" in titles
    finally:
        cleanup_rows(
            "DELETE FROM observations WHERE project_id IN "
            "(SELECT id FROM projects WHERE name = %s)",
            (proj_a,),
        )
        cleanup_rows("DELETE FROM projects WHERE name = %s", (proj_a,))
        _cleanup_workspace(second_ws["workspace"])


def test_search_global_never_crosses_other_owners():
    email_a = f"test-owner-a-{uuid.uuid4()}@example.com"
    email_b = f"test-owner-b-{uuid.uuid4()}@example.com"
    user_a = auth_repository.create_user(email_a)
    user_b = auth_repository.create_user(email_b)

    machine_a = f"test-machine-{uuid.uuid4()}"
    machine_b = f"test-machine-{uuid.uuid4()}"
    root_a = f"/tmp/test-owner-a-{uuid.uuid4()}"
    root_b = f"/tmp/test-owner-b-{uuid.uuid4()}"
    ws_a = repository.workspace_start(
        user_a["id"], machine_a, root_a, f"test-owner-a-ws-{uuid.uuid4()}"
    )
    ws_b = repository.workspace_start(
        user_b["id"], machine_b, root_b, f"test-owner-b-ws-{uuid.uuid4()}"
    )
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"

    try:
        save(
            path=f"{root_a}/{proj_a}",
            user_id=user_a["id"],
            machine=machine_a,
            title="Owner A secret decision",
            content="Only owner A should see this via search_global",
            type="decision",
        )
        save(
            path=f"{root_b}/{proj_b}",
            user_id=user_b["id"],
            machine=machine_b,
            title="Owner B secret decision",
            content="Only owner B should see this via search_global",
            type="decision",
        )

        results_a = json.loads(
            search_global(user_id=user_a["id"], query="secret decision")
        )
        titles_a = [r["title"] for r in results_a]
        assert "Owner A secret decision" in titles_a
        assert "Owner B secret decision" not in titles_a
    finally:
        _cleanup_workspace(ws_a)
        _cleanup_workspace(ws_b)
        cleanup_rows("DELETE FROM users WHERE id = %s", (user_a["id"],))
        cleanup_rows("DELETE FROM users WHERE id = %s", (user_b["id"],))


def test_save_session_type_accepted(registered_workspace, project_name):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Session summary: testing",
            content="Goal: test coverage. Accomplished: P0 gaps filled.",
            type="session",
        )
    )
    assert result["type"] == "session"
    assert "error" not in result


def test_save_invalid_type_rejected(registered_workspace, project_name):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Should fail",
            content="Invalid type",
            type="banana",
        )
    )
    assert "error" in result
    assert result["type"] == "validation"


def test_save_unregistered_path_returns_not_started(registered_workspace):
    result = json.loads(
        save(
            path="/never/registered/anywhere",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Should not save",
            content="Path was never onboarded",
            type="discovery",
        )
    )
    assert result["type"] == "not_started"
    assert "memodi_workspace_start" in result["error"]


def test_cross_owner_same_project_name_never_bleeds():
    """Regression test for the original resolution bug: two workspaces
    owned by different users, with a project of the SAME name, must land
    in distinct projects — saving/searching in one never surfaces the
    other's data."""
    email_a = f"test-bleed-a-{uuid.uuid4()}@example.com"
    email_b = f"test-bleed-b-{uuid.uuid4()}@example.com"
    user_a = auth_repository.create_user(email_a)
    user_b = auth_repository.create_user(email_b)

    machine_a = f"test-machine-{uuid.uuid4()}"
    machine_b = f"test-machine-{uuid.uuid4()}"
    root_a = f"/tmp/test-bleed-a-{uuid.uuid4()}"
    root_b = f"/tmp/test-bleed-b-{uuid.uuid4()}"
    shared_project_name = "shared-name"

    ws_a = repository.workspace_start(
        user_a["id"], machine_a, root_a, f"test-bleed-a-ws-{uuid.uuid4()}"
    )
    ws_b = repository.workspace_start(
        user_b["id"], machine_b, root_b, f"test-bleed-b-ws-{uuid.uuid4()}"
    )

    try:
        save(
            path=f"{root_a}/{shared_project_name}",
            user_id=user_a["id"],
            machine=machine_a,
            title="Owner A memory",
            content="This belongs to owner A's project",
            type="decision",
        )
        save(
            path=f"{root_b}/{shared_project_name}",
            user_id=user_b["id"],
            machine=machine_b,
            title="Owner B memory",
            content="This belongs to owner B's project",
            type="decision",
        )

        conn = get_connection()
        rows = conn.execute(
            "SELECT id, workspace_id FROM projects WHERE name = %s",
            (shared_project_name,),
        ).fetchall()
        assert len({r["id"] for r in rows}) == 2
        assert len({r["workspace_id"] for r in rows}) == 2

        results_a = json.loads(
            search(
                path=f"{root_a}/{shared_project_name}",
                user_id=user_a["id"],
                machine=machine_a,
                query="memory",
            )
        )
        titles_a = [r["title"] for r in results_a]
        assert "Owner A memory" in titles_a
        assert "Owner B memory" not in titles_a

        results_b = json.loads(
            search(
                path=f"{root_b}/{shared_project_name}",
                user_id=user_b["id"],
                machine=machine_b,
                query="memory",
            )
        )
        titles_b = [r["title"] for r in results_b]
        assert "Owner B memory" in titles_b
        assert "Owner A memory" not in titles_b

        # The same isolation must hold through the semantic and hybrid paths,
        # not just keyword search.
        for search_fn in (search_similar, search_hybrid):
            titles_a = [
                r["title"]
                for r in json.loads(
                    search_fn(
                        path=f"{root_a}/{shared_project_name}",
                        user_id=user_a["id"],
                        machine=machine_a,
                        query="memory",
                    )
                )
            ]
            assert "Owner A memory" in titles_a
            assert "Owner B memory" not in titles_a

            titles_b = [
                r["title"]
                for r in json.loads(
                    search_fn(
                        path=f"{root_b}/{shared_project_name}",
                        user_id=user_b["id"],
                        machine=machine_b,
                        query="memory",
                    )
                )
            ]
            assert "Owner B memory" in titles_b
            assert "Owner A memory" not in titles_b
    finally:
        _cleanup_workspace(ws_a)
        _cleanup_workspace(ws_b)
        cleanup_rows("DELETE FROM users WHERE id = %s", (user_a["id"],))
        cleanup_rows("DELETE FROM users WHERE id = %s", (user_b["id"],))


def test_occurred_at_preserves_historical_order(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    # Simulates a bulk import: insert a historical note AFTER a recent one,
    # but the historical one should surface older in chronological listing.
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Recent note",
        content="Happened today",
        type="discovery",
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Historical note",
        content="Happened last year",
        type="discovery",
        occurred_at="2024-01-15T10:00:00Z",
    )

    results = json.loads(
        context(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            limit=10,
        )
    )
    titles = [r["title"] for r in results["observations"]]

    assert "Recent note" in titles
    assert "Historical note" in titles
    # Recent (occurred_at NULL → falls back to now()) ranks before historical.
    assert titles.index("Recent note") < titles.index("Historical note")


def test_occurred_at_orders_within_historical_batch(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    # Bulk import order ≠ real chronological order. Verify occurred_at wins.
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="March event",
        content="Third in real time",
        type="discovery",
        occurred_at="2025-03-01T00:00:00Z",
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="January event",
        content="First in real time",
        type="discovery",
        occurred_at="2025-01-01T00:00:00Z",
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="February event",
        content="Second in real time",
        type="discovery",
        occurred_at="2025-02-01T00:00:00Z",
    )

    results = json.loads(
        context(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            limit=10,
        )
    )
    titles = [r["title"] for r in results["observations"]]

    assert titles.index("March event") < titles.index("February event")
    assert titles.index("February event") < titles.index("January event")


def test_save_without_occurred_at_stays_backward_compatible(
    registered_workspace, project_name
):
    path = _path(registered_workspace, project_name)
    # Existing callers that omit occurred_at should behave exactly as before:
    # ordering falls back to created_at.
    for title in ["Alpha", "Beta", "Gamma"]:
        save(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title=title,
            content=f"Content for {title}",
            type="discovery",
        )

    results = json.loads(
        context(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            limit=10,
        )
    )
    titles = [r["title"] for r in results["observations"]]

    assert titles.index("Gamma") < titles.index("Beta")
    assert titles.index("Beta") < titles.index("Alpha")


def test_delete_hides_observation_from_context_and_hybrid_search(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Zebra migration note",
            content="Throwaway test data that must be deletable",
            type="discovery",
        )
    )["id"]

    result = json.loads(delete(**common, observation_id=obs_id))
    assert result["deleted"] is True
    assert result["already_deleted"] is False

    context_titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Zebra migration note" not in context_titles

    hybrid_titles = [
        r["title"] for r in json.loads(search_hybrid(**common, query="zebra migration"))
    ]
    assert "Zebra migration note" not in hybrid_titles


def test_delete_makes_get_observation_return_none(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="To be deleted",
            content="Should vanish from get_observation too",
            type="discovery",
        )
    )["id"]

    delete(**common, observation_id=obs_id)

    assert repository.get_observation(obs_id) is None


def test_delete_nonexistent_id_returns_not_found(registered_workspace, project_name):
    result = json.loads(
        delete(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            observation_id=str(uuid.uuid4()),
        )
    )
    assert result["deleted"] is False


def test_delete_cross_workspace_returns_not_found(registered_workspace, project_name):
    other = _extra_workspace(registered_workspace["user_id"])
    try:
        obs_id = json.loads(
            save(
                path=f"{other['root']}/{project_name}",
                user_id=other["user_id"],
                machine=other["machine"],
                title="Belongs to the other workspace",
                content="Must not be deletable from workspace A",
                type="discovery",
            )
        )["id"]

        result = json.loads(
            delete(
                path=_path(registered_workspace, project_name),
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
                observation_id=obs_id,
            )
        )
        assert result["deleted"] is False
        assert repository.get_observation(obs_id) is not None
    finally:
        _cleanup_workspace(other["workspace"])


def test_delete_twice_is_idempotent(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Delete me twice",
            content="Second delete should still ack success",
            type="discovery",
        )
    )["id"]

    first = json.loads(delete(**common, observation_id=obs_id))
    second = json.loads(delete(**common, observation_id=obs_id))

    assert first["deleted"] is True
    assert first["already_deleted"] is False
    assert second["deleted"] is True
    assert second["already_deleted"] is True


def test_save_with_supersedes_hides_old_and_keeps_audit_trail(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Old indentation rule",
            content="Use tabs for indentation",
            type="decision",
        )
    )["id"]

    new_ack = json.loads(
        save(
            **common,
            title="New indentation rule",
            content="Use spaces for indentation",
            type="decision",
            supersedes=old_id,
        )
    )
    assert new_ack["supersedes_applied"] is True

    context_titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "New indentation rule" in context_titles
    assert "Old indentation rule" not in context_titles

    old_obs = repository.get_observation(old_id)
    assert old_obs is not None
    assert str(old_obs["superseded_by"]) == new_ack["id"]


def test_save_with_bogus_supersedes_id_still_saves(registered_workspace, project_name):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="New note with a bad supersedes reference",
            content="The save must succeed even if supersedes is garbage",
            type="discovery",
            supersedes="not-a-real-observation-id",
        )
    )
    assert "id" in result
    assert result["supersedes_applied"] is False
    assert "supersedes_error" in result


def test_topic_key_upsert_skips_superseded_rows(registered_workspace, project_name):
    topic = "test/topic-supersede-skip"
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Topic v1",
            content="First version under this topic_key",
            type="architecture",
            topic_key=topic,
        )
    )["id"]

    save(
        **common,
        title="Superseding note",
        content="Marks v1 as superseded",
        type="discovery",
        supersedes=old_id,
    )

    new_ack = json.loads(
        save(
            **common,
            title="Topic v2 (fresh insert, not an upsert of v1)",
            content="Same topic_key, but v1 is superseded so this must insert",
            type="architecture",
            topic_key=topic,
        )
    )
    # The real invariant: a brand-new row, not an in-place update of v1.
    assert new_ack["id"] != old_id
    assert new_ack["revision_count"] == 1

    old_obs = repository.get_observation(old_id)
    assert old_obs["title"] == "Topic v1"


def test_supersedes_on_topic_key_upsert_never_self_supersedes(
    registered_workspace, project_name
):
    """A topic_key upsert returns the SAME row it corrected. Superseding that
    id would point the row at itself and erase it from every read path."""
    topic = "test/self-supersede-upsert"
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Indentation rule v1",
            content="Use tabs for indentation",
            type="decision",
            topic_key=topic,
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Indentation rule v2",
            content="Use spaces for indentation",
            type="decision",
            topic_key=topic,
            supersedes=old_id,
        )
    )
    assert ack["id"] == old_id
    assert ack["supersedes_applied"] is False

    titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Indentation rule v2" in titles
    assert repository.get_observation(old_id)["superseded_by"] is None


def test_supersedes_on_deduplicated_save_never_self_supersedes(
    registered_workspace, project_name
):
    """A re-save inside the 15-minute dedup window returns the existing row's
    id. Superseding it would make the observation vanish."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    first_id = json.loads(
        save(
            **common,
            title="Repeated note",
            content="Exactly the same content twice",
            type="discovery",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Repeated note",
            content="Exactly the same content twice",
            type="discovery",
            supersedes=first_id,
        )
    )
    assert ack["id"] == first_id
    assert ack["supersedes_applied"] is False

    titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Repeated note" in titles
    assert repository.get_observation(first_id)["superseded_by"] is None


def test_dedup_never_absorbs_into_a_superseded_observation(
    registered_workspace, project_name
):
    """Re-saving content identical to a SUPERSEDED row must create a new
    visible observation — absorbing it would ack success while surfacing
    nowhere."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Deploy note",
            content="Deploys run through the tunnel",
            type="config",
        )
    )["id"]
    json.loads(
        save(
            **common,
            title="Deploy note v2",
            content="Deploys run through the tunnel with a service token",
            type="config",
            supersedes=old_id,
        )
    )

    re_ack = json.loads(
        save(
            **common,
            title="Deploy note",
            content="Deploys run through the tunnel",
            type="config",
        )
    )
    assert re_ack["id"] != old_id

    titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Deploy note" in titles


def test_delete_malformed_id_returns_clean_error(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    result = json.loads(delete(**common, observation_id="not-a-uuid"))

    assert result["deleted"] is False
    assert result["error"] == "invalid observation id"
    _assert_no_db_internals(result)

    # The shared connection must still be usable afterwards.
    assert "id" in json.loads(
        save(
            **common,
            title="Still working",
            content="A malformed delete must not poison the connection",
            type="discovery",
        )
    )


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "", 12])
def test_save_with_invalid_supersedes_leaks_no_db_internals(
    registered_workspace, project_name, bad_id
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    ack = json.loads(
        save(
            **common,
            title="Note with an unusable supersedes value",
            content="The save must persist and the ack must stay domain-level",
            type="discovery",
            supersedes=bad_id,
        )
    )

    assert "id" in ack
    assert ack["supersedes_applied"] is False
    _assert_no_db_internals(ack)
    assert repository.get_observation(ack["id"]) is not None


def test_supersede_failure_never_breaks_the_save(
    registered_workspace, project_name, monkeypatch
):
    """The observation is already committed when the supersede runs — a
    failure there must never turn into an error the client would retry."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Old rule",
            content="To be replaced while the supersede path is broken",
            type="decision",
        )
    )["id"]

    def boom(**kwargs):
        raise RuntimeError("transient database failure")

    monkeypatch.setattr(repository, "supersede_observation", boom)

    ack = json.loads(
        save(
            **common,
            title="New rule",
            content="Saved even though superseding blew up",
            type="decision",
            supersedes=old_id,
        )
    )
    assert "error" not in ack
    assert "id" in ack
    assert ack["supersedes_applied"] is False
    _assert_no_db_internals(ack)


def test_deleting_successor_resurfaces_predecessor(registered_workspace, project_name):
    """Deleting the replacement is the natural undo — superseded_by must never
    point at a deleted row, or the predecessor stays invisible forever."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Predecessor rule",
            content="Original decision that got replaced",
            type="decision",
        )
    )["id"]
    new_ack = json.loads(
        save(
            **common,
            title="Successor rule",
            content="Replacement decision that turns out to be wrong",
            type="decision",
            supersedes=old_id,
        )
    )
    assert new_ack["supersedes_applied"] is True

    undo = json.loads(delete(**common, observation_id=new_ack["id"]))
    assert undo["resurfaced"] == 1

    titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Predecessor rule" in titles
    assert "Successor rule" not in titles
    assert repository.get_observation(old_id)["superseded_by"] is None


def test_supersedes_reasons_are_discriminated(registered_workspace, project_name):
    """The LLM must be able to tell a pointless retry from a harmful one, so
    each failure mode reports its own reason instead of one lumped message."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )

    def _reason(supersedes, **overrides):
        payload = dict(
            title=f"Reason probe {uuid.uuid4()}",
            content=f"Distinct content {uuid.uuid4()}",
            type="discovery",
        )
        payload.update(overrides)
        ack = json.loads(save(**common, **payload, supersedes=supersedes))
        assert ack["supersedes_applied"] is False
        return ack["supersedes_reason"]

    assert _reason("not-a-uuid") == "invalid_id"
    assert _reason(str(uuid.uuid4())) == "not_found"

    deleted_id = json.loads(
        save(**common, title="Doomed", content="About to be deleted", type="discovery")
    )["id"]
    delete(**common, observation_id=deleted_id)
    assert _reason(deleted_id) == "already_deleted"

    superseded_id = json.loads(
        save(
            **common,
            title="Replaced once",
            content="Already has an heir",
            type="config",
        )
    )["id"]
    save(
        **common,
        title="The heir",
        content="First replacement",
        type="config",
        supersedes=superseded_id,
    )
    assert _reason(superseded_id) == "already_superseded"

    topic = "test/discriminated-self"
    self_id = json.loads(
        save(
            **common,
            title="Topic under a key",
            content="Will be corrected in place",
            type="config",
            topic_key=topic,
        )
    )["id"]
    assert _reason(self_id, topic_key=topic) == "self"


def test_superseded_observation_hidden_from_every_search_path(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Superseded flamingo note",
            content="Flamingo migration runs on Sunday",
            type="config",
        )
    )["id"]
    save(
        **common,
        title="Current flamingo note",
        content="Flamingo migration runs on Monday",
        type="config",
        supersedes=old_id,
    )

    for search_fn in (search, search_hybrid, search_similar):
        rows = json.loads(search_fn(**common, query="flamingo migration"))
        titles = [r["title"] for r in rows]
        assert "Current flamingo note" in titles, search_fn.__name__
        assert "Superseded flamingo note" not in titles, search_fn.__name__


def test_get_observation_returns_superseded_row_with_pointer(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Audited old rule",
            content="Kept readable by id after being replaced",
            type="decision",
        )
    )["id"]
    new_ack = json.loads(
        save(
            **common,
            title="Audited new rule",
            content="The replacement",
            type="decision",
            supersedes=old_id,
        )
    )

    payload = json.loads(get_observation(**common, observation_id=old_id))
    assert payload["id"] == old_id
    assert payload["title"] == "Audited old rule"
    assert payload["superseded_by"] == new_ack["id"]


def test_get_observation_returns_supersedes_for_successor(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Reverse lookup old rule",
            content="The predecessor discoverable from its successor",
            type="decision",
        )
    )["id"]
    new_ack = json.loads(
        save(
            **common,
            title="Reverse lookup new rule",
            content="The replacement that should expose supersedes",
            type="decision",
            supersedes=old_id,
        )
    )

    new_payload = json.loads(get_observation(**common, observation_id=new_ack["id"]))
    assert new_payload["supersedes"] == [old_id]

    old_payload = json.loads(get_observation(**common, observation_id=old_id))
    assert old_payload["superseded_by"] == new_ack["id"]
    assert "supersedes" not in old_payload


def test_get_observation_supersedes_lists_every_predecessor_recent_first(
    registered_workspace, project_name
):
    """Two acked corrections can land on the same successor row through the
    topic_key upsert flow, so the reverse pointer has to carry both ids —
    reporting only one would deny a correction the server already acked."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    topic = "test/reverse-multi-predecessor"
    first_old_id = json.loads(
        save(
            **common,
            title="First scattered rule",
            content="One of two rules folded into a single successor",
            type="decision",
        )
    )["id"]
    second_old_id = json.loads(
        save(
            **common,
            title="Second scattered rule",
            content="The other rule folded into the same successor",
            type="decision",
        )
    )["id"]
    successor_id = json.loads(
        save(
            **common,
            title="Consolidated rule",
            content="The row both corrections resolve to",
            type="decision",
            topic_key=topic,
        )
    )["id"]
    first_ack = json.loads(
        save(
            **common,
            title="Consolidated rule",
            content="Now also covering the first scattered rule",
            type="decision",
            topic_key=topic,
            supersedes=first_old_id,
        )
    )
    second_ack = json.loads(
        save(
            **common,
            title="Consolidated rule",
            content="Now also covering the second scattered rule",
            type="decision",
            topic_key=topic,
            supersedes=second_old_id,
        )
    )
    assert first_ack["id"] == successor_id
    assert second_ack["id"] == successor_id
    assert first_ack["supersedes_applied"] is True
    assert second_ack["supersedes_applied"] is True

    _backdate_updated_at(first_old_id, hours=1)
    _backdate_updated_at(second_old_id, hours=2)

    payload = json.loads(get_observation(**common, observation_id=successor_id))
    assert payload["supersedes"] == [first_old_id, second_old_id]


def test_get_observation_supersedes_excludes_predecessor_outside_workspace(
    registered_workspace, project_name
):
    """A predecessor whose project was merged into another workspace must
    drop out of the reverse pointer — the caller could not read that id."""
    other = _extra_workspace(registered_workspace["user_id"])
    try:
        predecessor_project = f"{project_name}-predecessor"
        old_id = json.loads(
            save(
                path=_path(registered_workspace, predecessor_project),
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
                title="Predecessor about to leave the workspace",
                content="Its project gets merged into another workspace",
                type="decision",
            )
        )["id"]
        common = dict(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
        new_ack = json.loads(
            save(
                **common,
                title="Successor left behind",
                content="Must not point at an id outside the caller's workspace",
                type="decision",
                supersedes=old_id,
            )
        )
        assert new_ack["supersedes_applied"] is True

        before = json.loads(get_observation(**common, observation_id=new_ack["id"]))
        assert before["supersedes"] == [old_id], (
            "a predecessor in another project of the SAME workspace is readable, "
            "so the reverse pointer must scope by workspace, not by project"
        )

        save(
            path=f"{other['root']}/{project_name}-sink",
            user_id=other["user_id"],
            machine=other["machine"],
            title="Merge target in the other workspace",
            content="Receives the predecessor's project",
            type="decision",
        )
        source = repository.get_or_create_project(
            predecessor_project,
            workspace_id=registered_workspace["workspace"]["id"],
        )
        target = repository.get_or_create_project(
            f"{project_name}-sink", workspace_id=other["workspace"]["id"]
        )
        repository.merge_projects(source["id"], target["id"])

        moved = json.loads(get_observation(**common, observation_id=old_id))
        assert "error" in moved

        payload = json.loads(get_observation(**common, observation_id=new_ack["id"]))
        assert "supersedes" not in payload
    finally:
        _cleanup_workspace(other["workspace"])


def test_repository_get_observation_returns_uuid_ids(
    registered_workspace, project_name
):
    """The reverse pointer must use the same id type as id/superseded_by —
    json.dumps(default=str) handles the wire, so no eager stringifying."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Typed predecessor",
            content="Read back through the repository, not the tool",
            type="decision",
        )
    )["id"]
    new_id = json.loads(
        save(
            **common,
            title="Typed successor",
            content="Its supersedes entries must be uuid.UUID",
            type="decision",
            supersedes=old_id,
        )
    )["id"]

    obs = repository.get_observation(
        new_id, workspace_id=registered_workspace["workspace"]["id"]
    )
    assert isinstance(obs["id"], uuid.UUID)
    assert obs["supersedes"] == [uuid.UUID(old_id)]
    assert all(isinstance(pred, uuid.UUID) for pred in obs["supersedes"])


def test_get_observation_supersedes_absent_when_nothing_replaced(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Standalone rule",
            content="Never replaced anything",
            type="decision",
        )
    )["id"]

    payload = json.loads(get_observation(**common, observation_id=obs_id))
    assert "supersedes" not in payload


def test_get_observation_supersedes_absent_after_predecessor_deleted(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Deletable predecessor",
            content="Will be deleted after being superseded",
            type="decision",
        )
    )["id"]
    new_ack = json.loads(
        save(
            **common,
            title="Successor of a deletable predecessor",
            content="Its supersedes pointer must clear once the predecessor is gone",
            type="decision",
            supersedes=old_id,
        )
    )

    delete(**common, observation_id=old_id)

    payload = json.loads(get_observation(**common, observation_id=new_ack["id"]))
    assert "supersedes" not in payload


def test_save_with_supersedes_list_hides_all_and_acks_applied(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    first_id = json.loads(
        save(
            **common,
            title="Pi note part 1",
            content="Raspberry Pi runs the server via systemd",
            type="config",
        )
    )["id"]
    second_id = json.loads(
        save(
            **common,
            title="Pi note part 2",
            content="Cloudflare Tunnel handles TLS for the Pi",
            type="config",
        )
    )["id"]

    new_ack = json.loads(
        save(
            **common,
            title="Consolidated Pi operational notes",
            content="Systemd + Cloudflare Tunnel, distilled from two notes",
            type="config",
            supersedes=[first_id, second_id],
        )
    )

    assert new_ack["supersedes_applied"] is True
    assert "supersedes_results" not in new_ack

    context_titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Consolidated Pi operational notes" in context_titles
    assert "Pi note part 1" not in context_titles
    assert "Pi note part 2" not in context_titles

    assert str(repository.get_observation(first_id)["superseded_by"]) == new_ack["id"]
    assert str(repository.get_observation(second_id)["superseded_by"]) == new_ack["id"]


def test_save_with_mixed_supersedes_list_reports_discriminated_results(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Valid predecessor",
            content="Will be superseded",
            type="discovery",
        )
    )["id"]

    already_superseded_id = json.loads(
        save(
            **common,
            title="Already replaced once",
            content="Has an heir already",
            type="discovery",
        )
    )["id"]
    save(
        **common,
        title="Existing heir",
        content="First replacement",
        type="discovery",
        supersedes=already_superseded_id,
    )

    bogus_id = "not-a-real-observation-id"

    ack = json.loads(
        save(
            **common,
            title="Consolidated with mixed outcomes",
            content="One valid, one bogus, one already superseded",
            type="discovery",
            supersedes=[valid_id, bogus_id, already_superseded_id],
        )
    )

    assert ack["supersedes_applied"] is False
    assert ack["supersedes_results"] == {
        valid_id: "applied",
        bogus_id: "invalid_id",
        already_superseded_id: "already_superseded",
    }
    assert str(repository.get_observation(valid_id)["superseded_by"]) == ack["id"]

    context_titles = [o["title"] for o in json.loads(context(**common))["observations"]]
    assert "Consolidated with mixed outcomes" in context_titles


def test_supersedes_list_containing_own_id_reports_self_others_applied(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    topic = "test/self-in-supersedes-list"
    existing_id = json.loads(
        save(
            **common,
            title="Topic v1",
            content="First version under this topic_key",
            type="architecture",
            topic_key=topic,
        )
    )["id"]
    other_old_id = json.loads(
        save(
            **common,
            title="Scattered note",
            content="A separate note folded into the same consolidation",
            type="architecture",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Topic v2",
            content="Upsert of v1, also folding in the scattered note",
            type="architecture",
            topic_key=topic,
            supersedes=[existing_id, other_old_id],
        )
    )

    assert ack["id"] == existing_id
    assert ack["supersedes_applied"] is False
    assert ack["supersedes_results"] == {
        existing_id: "self",
        other_old_id: "applied",
    }
    assert str(repository.get_observation(other_old_id)["superseded_by"]) == existing_id


def test_supersedes_list_duplicates_deduped_before_applying(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Duplicated predecessor",
            content="Referenced twice in one list",
            type="discovery",
        )
    )["id"]
    bogus_id = "still-not-a-real-id"

    ack = json.loads(
        save(
            **common,
            title="Consolidated, deduped",
            content="Same id listed twice plus a bogus one",
            type="discovery",
            supersedes=[valid_id, valid_id, bogus_id],
        )
    )

    assert ack["supersedes_applied"] is False
    assert ack["supersedes_results"] == {valid_id: "applied", bogus_id: "invalid_id"}
    assert str(repository.get_observation(valid_id)["superseded_by"]) == ack["id"]


def test_supersedes_list_over_cap_applies_nothing(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_ids = [
        json.loads(
            save(
                **common,
                title=f"Over-cap predecessor {i}",
                content=f"Distinct content {i}",
                type="discovery",
            )
        )["id"]
        for i in range(21)
    ]

    ack = json.loads(
        save(
            **common,
            title="Attempted mega-consolidation",
            content="Too many ids in one call",
            type="discovery",
            supersedes=old_ids,
        )
    )

    assert ack["supersedes_applied"] is False
    assert ack["supersedes_reason"] == "too_many"
    assert "supersedes_results" not in ack

    context_titles = [
        o["title"] for o in json.loads(context(**common, limit=50))["observations"]
    ]
    for i in range(21):
        assert f"Over-cap predecessor {i}" in context_titles
    for old_id in old_ids:
        assert repository.get_observation(old_id)["superseded_by"] is None


def test_supersedes_empty_list_is_a_no_op(registered_workspace, project_name):
    ack = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="No supersedes at all",
            content="Empty list must behave exactly like omitting supersedes",
            type="discovery",
            supersedes=[],
        )
    )
    assert "supersedes_applied" not in ack
    assert "supersedes_reason" not in ack
    assert "supersedes_error" not in ack
    assert "supersedes_results" not in ack


def test_supersedes_list_non_string_element_invalid_id_for_in_process_callers(
    registered_workspace, project_name
):
    """Internal robustness only, NOT the MCP contract: over MCP, pydantic
    rejects a non-string element with a validation error before this body
    runs, so nothing is ever persisted. This pins that a direct in-process
    caller still gets a per-id invalid_id instead of a TypeError on an ack
    whose observation is already committed."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Valid one",
            content="Superseded by the consolidation",
            type="discovery",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Consolidation with a garbage element",
            content="One valid id, one int, one None",
            type="discovery",
            supersedes=[valid_id, 12, None],
        )
    )

    assert ack["supersedes_applied"] is False
    assert ack["supersedes_results"][valid_id] == "applied"
    assert ack["supersedes_results"]["12"] == "invalid_id"
    assert ack["supersedes_results"]["None"] == "invalid_id"
    _assert_no_db_internals(ack)


def test_deleting_successor_of_supersedes_list_resurfaces_all(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_ids = [
        json.loads(
            save(
                **common,
                title=f"Scattered note {i}",
                content=f"Part {i} of the theme",
                type="discovery",
            )
        )["id"]
        for i in range(3)
    ]

    new_ack = json.loads(
        save(
            **common,
            title="Consolidated theme",
            content="Folds three scattered notes into one",
            type="discovery",
            supersedes=old_ids,
        )
    )
    assert new_ack["supersedes_applied"] is True

    undo = json.loads(delete(**common, observation_id=new_ack["id"]))
    assert undo["resurfaced"] == 3

    context_titles = [
        o["title"] for o in json.loads(context(**common, limit=50))["observations"]
    ]
    for i in range(3):
        assert f"Scattered note {i}" in context_titles
    assert "Consolidated theme" not in context_titles


def test_save_related_excludes_every_row_in_supersedes_list(
    registered_workspace, project_name
):
    """The list variant of the order guard: none of the N just-superseded ids
    may come back as related, or the agent gets told to supersede them again."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    sibling_id = json.loads(
        save(
            **common,
            title="Sibling deploy race note",
            content=(
                "Deploy failure: concurrent GitHub Actions runs race for the "
                "same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]
    old_ids = [
        json.loads(
            save(
                **common,
                title=f"Old deploy race note {i}",
                content=(
                    f"We saw run {i} of the deploy pipeline fail because two "
                    "runs raced for the same git ref lock"
                ),
                type="bugfix",
            )
        )["id"]
        for i in range(2)
    ]

    ack = json.loads(
        save(
            **common,
            title="Superseding deploy race note",
            content=(
                "Deploy pipeline breaks when concurrent runs race for the "
                "same git ref lock file"
            ),
            type="bugfix",
            supersedes=old_ids,
        )
    )

    assert ack["supersedes_applied"] is True
    related_ids = [r["id"] for r in ack.get("related", [])]
    for old_id in old_ids:
        assert old_id not in related_ids, (
            "a row this save just superseded came back as related"
        )
    assert sibling_id in related_ids, (
        "a genuine sibling must still surface — otherwise this test proves nothing"
    )


def test_get_observation_supersedes_list_returns_all_predecessors(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_ids = [
        json.loads(
            save(
                **common,
                title=f"Reverse lookup predecessor {i}",
                content=f"Part {i} folded into the consolidated row",
                type="decision",
            )
        )["id"]
        for i in range(3)
    ]
    for i, old_id in enumerate(old_ids):
        _backdate_updated_at(old_id, hours=i + 1)

    new_ack = json.loads(
        save(
            **common,
            title="Consolidated reverse lookup row",
            content="The successor that should expose all three",
            type="decision",
            supersedes=old_ids,
        )
    )
    assert new_ack["supersedes_applied"] is True

    payload = json.loads(get_observation(**common, observation_id=new_ack["id"]))
    assert payload["supersedes"] == old_ids


def test_supersedes_list_dedupes_equivalent_uuid_spellings(
    registered_workspace, project_name
):
    """Uppercase and hyphen-less spellings are the SAME id: deduping on the
    raw string would attempt the same row twice and report a phantom
    already_superseded conflict on an ack that actually consolidated fine."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Spelled three ways",
            content="Referenced as lowercase, uppercase and hyphen-less",
            type="discovery",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Consolidated across spellings",
            content="One id, three spellings, one supersede",
            type="discovery",
            supersedes=[valid_id, valid_id.upper(), valid_id.replace("-", "")],
        )
    )

    assert ack["supersedes_applied"] is True
    assert "supersedes_results" not in ack
    assert "supersedes_reason" not in ack
    assert str(repository.get_observation(valid_id)["superseded_by"]) == ack["id"]


def test_supersedes_repository_shape_surprise_never_breaks_the_save(
    registered_workspace, project_name, monkeypatch
):
    """A repository return-shape surprise must be absorbed as a per-id
    `failed`: the observation is already committed, so letting it escape
    would hand the client an error for a save that DID land."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Predecessor of a shape surprise",
            content="The supersede call will return a dict without applied",
            type="discovery",
        )
    )["id"]

    monkeypatch.setattr(
        repository, "supersede_observation", lambda **kwargs: {"unexpected": True}
    )

    ack = json.loads(
        save(
            **common,
            title="Saved despite the shape surprise",
            content="The save itself must still be acked normally",
            type="discovery",
            supersedes=[valid_id],
        )
    )

    assert "error" not in ack
    assert ack["title"] == "Saved despite the shape surprise"
    assert ack["supersedes_applied"] is False
    assert ack["supersedes_results"] == {valid_id: "failed"}
    _assert_no_db_internals(ack)


def test_supersedes_list_at_the_cap_applies_every_id(
    registered_workspace, project_name
):
    """Exactly 20 ids is legal. The count is spelled out on purpose: reading
    it from MAX_SUPERSEDES would make the test move with the constant and pin
    neither the cap value nor the boundary (a `>=` comparison refuses this)."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_ids = [
        json.loads(
            save(
                **common,
                title=f"At-cap predecessor {i}",
                content=f"Distinct at-cap content {i}",
                type="discovery",
            )
        )["id"]
        for i in range(20)
    ]

    ack = json.loads(
        save(
            **common,
            title="Consolidation exactly at the cap",
            content="Twenty ids is the largest legal list",
            type="discovery",
            supersedes=old_ids,
        )
    )

    assert ack["supersedes_applied"] is True
    assert "supersedes_reason" not in ack
    assert "supersedes_results" not in ack
    for old_id in old_ids:
        assert str(repository.get_observation(old_id)["superseded_by"]) == ack["id"]


def test_supersedes_list_over_cap_counts_raw_length_before_dedup(
    registered_workspace, project_name
):
    """The cap guards the caller's raw list, not the deduped one: 21 copies of
    one id is still an over-cap call, so nothing is applied."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Repeated past the cap",
            content="Listed twenty-one times in one call",
            type="discovery",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Duplicate-heavy over-cap consolidation",
            content="One id, twenty-one entries",
            type="discovery",
            supersedes=[valid_id] * 21,
        )
    )

    assert ack["supersedes_applied"] is False
    assert ack["supersedes_reason"] == "too_many"
    assert "supersedes_results" not in ack
    assert repository.get_observation(valid_id)["superseded_by"] is None


def test_supersedes_results_keys_are_truncated(registered_workspace, project_name):
    """Caller input is echoed back as result keys, so it must be bounded —
    otherwise a megabyte of junk rides back inside the hottest ack."""
    overlong = "x" * 200
    raw = save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Consolidation with an overlong id",
        content="The echoed key must be truncated",
        type="discovery",
        supersedes=[overlong],
    )
    ack = json.loads(raw)

    assert ack["supersedes_results"] == {"x" * 64 + "…": "invalid_id"}
    assert overlong not in raw


def test_supersedes_results_keys_strip_nul(registered_workspace, project_name):
    """A NUL byte in an echoed key is a control character no client should
    have to handle — strip it instead of round-tripping it."""
    raw = save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Consolidation with a NUL in the id",
        content="The echoed key must lose the NUL",
        type="discovery",
        supersedes=["abc\x00def"],
    )
    ack = json.loads(raw)

    assert ack["supersedes_results"] == {"abcdef": "invalid_id"}
    assert "\x00" not in raw
    assert "\\u0000" not in raw


def test_supersedes_results_keys_are_the_raw_strings_the_caller_sent(
    registered_workspace, project_name
):
    """Keys echo the caller's spelling so an agent can match them against the
    list it sent; canonicalizing them would break that lookup."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    valid_id = json.loads(
        save(
            **common,
            title="Referenced in uppercase",
            content="The caller shouted the id",
            type="discovery",
        )
    )["id"]
    bogus_id = "definitely-not-an-id"

    ack = json.loads(
        save(
            **common,
            title="Consolidation with an uppercase id",
            content="One uppercase valid id and one bogus id",
            type="discovery",
            supersedes=[valid_id.upper(), bogus_id],
        )
    )

    assert ack["supersedes_results"] == {
        valid_id.upper(): "applied",
        bogus_id: "invalid_id",
    }
    assert valid_id not in ack["supersedes_results"]


def test_get_observation_hides_deleted(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Deleted and unreadable",
            content="Soft-deleted rows stay hidden from the audit path",
            type="discovery",
        )
    )["id"]
    delete(**common, observation_id=obs_id)

    payload = json.loads(get_observation(**common, observation_id=obs_id))
    assert "error" in payload
    assert "title" not in payload


def test_get_observation_cross_workspace_returns_not_found(
    registered_workspace, project_name
):
    other = _extra_workspace(registered_workspace["user_id"])
    try:
        obs_id = json.loads(
            save(
                path=f"{other['root']}/{project_name}",
                user_id=other["user_id"],
                machine=other["machine"],
                title="Belongs to the other workspace",
                content="Must not be readable from workspace A",
                type="discovery",
            )
        )["id"]

        payload = json.loads(
            get_observation(
                path=_path(registered_workspace, project_name),
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
                observation_id=obs_id,
            )
        )
        assert "error" in payload
        assert "title" not in payload
    finally:
        _cleanup_workspace(other["workspace"])


def test_get_observation_malformed_id_returns_clean_error(
    registered_workspace, project_name
):
    payload = json.loads(
        get_observation(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            observation_id="not-a-uuid",
        )
    )
    assert payload["error"] == "invalid observation id"
    _assert_no_db_internals(payload)


def test_save_returns_related_for_similar_existing_observation(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    first_id = json.loads(
        save(
            **common,
            title="Deploy race incident",
            content=(
                "The deploy pipeline fails when two GitHub Actions runs race "
                "for the same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Deploy race incident, reworded",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )

    assert "related" in ack
    match = next((r for r in ack["related"] if r["id"] == first_id), None)
    assert match is not None
    assert match["title"] == "Deploy race incident"
    assert match["similarity"] >= repository.MIN_RELATED_SIMILARITY


def test_save_unrelated_content_returns_no_related_key(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Deploy race incident",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )

    ack = json.loads(
        save(
            **common,
            title="Banana nutrition fact",
            content="Bananas are a good source of potassium and are yellow when ripe",
            type="discovery",
        )
    )

    assert "related" not in ack


def test_save_upsert_by_topic_key_excludes_itself_from_related(
    registered_workspace, project_name
):
    """A topic_key upsert returns the SAME row it corrected. Without
    exclude_id, that row would match its own just-written embedding
    (similarity ~1.0) and list itself as related."""
    topic = "test/related-upsert-self-exclusion"
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Upsert topic v1",
        content="First version of a note that will be upserted in place",
        type="discovery",
        topic_key=topic,
    )

    ack = json.loads(
        save(
            **common,
            title="Upsert topic v2",
            content="Second version of the same note, upserted in place",
            type="discovery",
            topic_key=topic,
        )
    )

    assert "related" not in ack


def test_save_related_excludes_superseded_observations(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Old deploy race note",
            content=(
                "The deploy pipeline fails when two GitHub Actions runs race "
                "for the same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]
    save(
        **common,
        title="Superseding deploy race note",
        content=(
            "Deploy failure: concurrent GitHub Actions runs race for the "
            "same git ref lock"
        ),
        type="bugfix",
        supersedes=old_id,
    )

    ack = json.loads(
        save(
            **common,
            title="Third deploy race note",
            content=(
                "We saw the deploy pipeline fail because two runs raced for "
                "the same git ref lock"
            ),
            type="bugfix",
        )
    )

    related_ids = [r["id"] for r in ack.get("related", [])]
    assert old_id not in related_ids


def test_save_related_excludes_deleted_observations(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    doomed_id = json.loads(
        save(
            **common,
            title="Doomed deploy race note",
            content=(
                "The deploy pipeline fails when two GitHub Actions runs race "
                "for the same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]
    delete(**common, observation_id=doomed_id)

    ack = json.loads(
        save(
            **common,
            title="Fresh deploy race note",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )

    related_ids = [r["id"] for r in ack.get("related", [])]
    assert doomed_id not in related_ids


def test_save_related_capped_at_three_ordered_by_similarity_descending(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    cluster = [
        (
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        "Deploy failure: concurrent GitHub Actions runs race for the same git ref lock",
        (
            "We saw the deploy pipeline fail because two runs raced for "
            "the same git ref lock"
        ),
        "Two simultaneous deploy runs racing for the git ref lock breaks the pipeline",
    ]
    for i, content in enumerate(cluster):
        save(**common, title=f"Deploy race note {i}", content=content, type="bugfix")

    ack = json.loads(
        save(
            **common,
            title="Deploy race note query",
            content=(
                "Deploy pipeline breaks when concurrent runs race for the "
                "same git ref lock file"
            ),
            type="bugfix",
        )
    )

    assert len(ack["related"]) == 3
    similarities = [r["similarity"] for r in ack["related"]]
    assert similarities == sorted(similarities, reverse=True)


def test_save_related_crosses_projects_within_workspace_labeled_with_project_name(
    registered_workspace,
):
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"
    path_a = f"{registered_workspace['root']}/{proj_a}"
    path_b = f"{registered_workspace['root']}/{proj_b}"

    save(
        path=path_a,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Deploy race note in project A",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )

    ack = json.loads(
        save(
            path=path_b,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Deploy race note in project B",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )

    assert "related" in ack
    match = next(
        (r for r in ack["related"] if r["title"] == "Deploy race note in project A"),
        None,
    )
    assert match is not None
    assert match["project"] == proj_a


def test_save_related_never_crosses_users(registered_workspace, project_name):
    """The workspace_id predicate on the related query is the only thing
    between the save ack and cross-user disclosure: another user's
    observation must never surface, however similar it is."""
    email = f"test-related-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(email)
    other_machine = f"test-machine-{uuid.uuid4()}"
    other_root = f"/tmp/test-related-owner-{uuid.uuid4()}"
    other_ws = repository.workspace_start(
        other["id"], other_machine, other_root, f"test-related-owner-ws-{uuid.uuid4()}"
    )

    try:
        secret_id = json.loads(
            save(
                path=f"{other_root}/test-proj-{uuid.uuid4()}",
                user_id=other["id"],
                machine=other_machine,
                title="Deploy race incident",
                content=(
                    "The deploy pipeline fails when two GitHub Actions runs "
                    "race for the same git ref lock"
                ),
                type="bugfix",
            )
        )["id"]

        ack = json.loads(
            save(
                path=_path(registered_workspace, project_name),
                user_id=registered_workspace["user_id"],
                machine=registered_workspace["machine"],
                title="Deploy race incident, reworded",
                content=(
                    "Concurrent deploys can race for the git ref lock causing "
                    "pipeline failures"
                ),
                type="bugfix",
            )
        )

        assert "related" not in ack, (
            f"related leaked outside the caller's workspace: {ack.get('related')}"
        )
        assert secret_id not in [r["id"] for r in ack.get("related", [])]
    finally:
        _cleanup_workspace(other_ws)
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


def test_save_related_excludes_the_row_this_save_just_superseded(
    registered_workspace, project_name
):
    """The supersede has to land BEFORE the related lookup runs, or the row
    this save just replaced comes back as related — advising the agent to
    supersede it a second time."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    sibling_id = json.loads(
        save(
            **common,
            title="Sibling deploy race note",
            content=(
                "Deploy failure: concurrent GitHub Actions runs race for the "
                "same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]
    old_id = json.loads(
        save(
            **common,
            title="Old deploy race note",
            content=(
                "We saw the deploy pipeline fail because two runs raced for "
                "the same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]

    ack = json.loads(
        save(
            **common,
            title="Superseding deploy race note",
            content=(
                "Deploy pipeline breaks when concurrent runs race for the "
                "same git ref lock file"
            ),
            type="bugfix",
            supersedes=old_id,
        )
    )

    assert ack["supersedes_applied"] is True
    related_ids = [r["id"] for r in ack.get("related", [])]
    assert old_id not in related_ids, (
        "the row this save just superseded came back as related"
    )
    assert sibling_id in related_ids, (
        "a genuine sibling must still surface — otherwise this test proves nothing"
    )


def test_save_related_never_surfaces_a_nan_similarity(
    registered_workspace, project_name
):
    """A zero-norm embedding makes cosine distance NaN, and Postgres sorts
    NaN above every float — a similarity threshold would let it through and
    json.dumps would emit a bare NaN, which is not valid JSON."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    zero_id = json.loads(
        save(
            **common,
            title="Zero norm embedding row",
            content="This row gets a zero vector planted under it",
            type="discovery",
        )
    )["id"]
    conn = get_connection()
    conn.execute(
        "UPDATE observations SET embedding = %s WHERE id = %s",
        (str([0.0] * 384), zero_id),
    )
    conn.commit()

    raw = save(
        **common,
        title="Deploy race incident",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )

    assert "NaN" not in raw, f"ack carries a bare NaN, which is invalid JSON: {raw}"
    ack = json.loads(raw)
    assert zero_id not in [r["id"] for r in ack.get("related", [])]


def test_save_related_row_shape_failure_never_breaks_save(
    registered_workspace, project_name, monkeypatch
):
    """Serializing the rows is part of the guarded work: a row the
    repository hands back in an unexpected shape must not error the ack of
    an already committed save."""
    monkeypatch.setattr(
        repository,
        "find_related_observations",
        lambda **kwargs: [
            {
                "id": str(uuid.uuid4()),
                "title": "Row with no similarity",
                "topic_key": None,
                "project": "somewhere",
                "similarity": None,
            }
        ],
    )

    ack = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Save that survives a bad related row",
            content="The ack drops related rather than failing the save",
            type="discovery",
        )
    )

    assert "error" not in ack
    assert "id" in ack
    assert "related" not in ack


def test_save_related_db_failure_leaves_the_connection_usable(
    registered_workspace, project_name, monkeypatch
):
    """A real database error in the related lookup aborts the transaction.
    The save must still ack, the shared connection must survive without
    being torn down and reconnected, and the next save must still work AND
    still return related — proving the aborted transaction was cleared."""
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    first_id = json.loads(
        save(
            **common,
            title="Deploy race incident",
            content=(
                "The deploy pipeline fails when two GitHub Actions runs race "
                "for the same git ref lock"
            ),
            type="bugfix",
        )
    )["id"]

    def broken_query(**kwargs):
        return [
            dict(r)
            for r in get_connection()
            .execute("SELECT no_such_column FROM observations LIMIT 1")
            .fetchall()
        ]

    monkeypatch.setattr(repository, "find_related_observations", broken_query)
    conn_before = get_connection()

    ack = json.loads(
        save(
            **common,
            title="Deploy race incident, reworded",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )
    assert "error" not in ack
    assert "id" in ack
    assert "related" not in ack
    assert get_connection() is conn_before, (
        "the aborted transaction was left behind, so the connection layer had "
        "to tear the connection down and reconnect"
    )

    monkeypatch.undo()

    recovered = json.loads(
        save(
            **common,
            title="Deploy race incident, third wording",
            content=(
                "Deploy pipeline breaks when concurrent runs race for the "
                "same git ref lock file"
            ),
            type="bugfix",
        )
    )
    assert "error" not in recovered
    assert first_id in [r["id"] for r in recovered.get("related", [])]


def test_save_leaves_no_open_transaction(registered_workspace, project_name):
    """The related lookup opens a read transaction on every save. Left open,
    it arms the 30s idle-in-transaction kill on the shared connection.

    The status is read off a held reference: get_connection() runs its own
    liveness SELECT, which would open a transaction and mask the answer.
    """
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    conn = get_connection()
    save(
        **common,
        title="Deploy race incident",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )
    assert conn.info.transaction_status == TransactionStatus.IDLE

    ack = json.loads(
        save(
            **common,
            title="Deploy race incident, reworded",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )
    assert "related" in ack, "the related-present path is the one under test"
    assert conn.info.transaction_status == TransactionStatus.IDLE


def test_occurred_at_persisted_on_upsert(registered_workspace, project_name):
    topic = "architecture/legacy-import"
    path = _path(registered_workspace, project_name)

    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Imported v1",
        content="From legacy .md file",
        type="architecture",
        topic_key=topic,
        occurred_at="2024-06-01T12:00:00Z",
    )
    # Upsert without passing occurred_at → should preserve the historical date.
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Imported v2",
        content="Corrected content",
        type="architecture",
        topic_key=topic,
    )

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    observations = repository.get_recent_observations(proj["id"])
    topic_obs = [o for o in observations if o["topic_key"] == topic]

    assert len(topic_obs) == 1
    assert topic_obs[0]["title"] == "Imported v2"
    assert topic_obs[0]["occurred_at"] is not None
    assert topic_obs[0]["occurred_at"].year == 2024
    assert topic_obs[0]["occurred_at"].month == 6
