import json
import uuid

import pytest

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
