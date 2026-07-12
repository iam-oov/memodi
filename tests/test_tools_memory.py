import json
import uuid

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    context,
    list_projects,
    save,
    search,
    search_global,
    search_hybrid,
    search_similar,
)
from tests.conftest import _path, cleanup_rows


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
