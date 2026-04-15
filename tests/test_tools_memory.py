import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    check_workspace,
    context,
    link_project,
    list_projects,
    save,
    search,
    search_global,
)


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-memodi-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(project_name):
    yield
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.commit()


def _cleanup_workspace(ws_name: str) -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM observations
        WHERE project_id IN (
            SELECT p.id FROM projects p
            JOIN workspaces w ON w.id = p.workspace_id
            WHERE w.name = %s
        )
        """,
        (ws_name,),
    )
    conn.execute(
        """
        DELETE FROM workflow_transitions
        WHERE workflow_id IN (
            SELECT wf.id FROM workflows wf
            JOIN projects p ON p.id = wf.project_id
            JOIN workspaces w ON w.id = p.workspace_id
            WHERE w.name = %s
        )
        """,
        (ws_name,),
    )
    conn.execute(
        """
        DELETE FROM workflows
        WHERE project_id IN (
            SELECT p.id FROM projects p
            JOIN workspaces w ON w.id = p.workspace_id
            WHERE w.name = %s
        )
        """,
        (ws_name,),
    )
    conn.execute(
        """
        DELETE FROM projects
        WHERE workspace_id IN (
            SELECT id FROM workspaces WHERE name = %s
        )
        """,
        (ws_name,),
    )
    conn.execute("DELETE FROM workspaces WHERE name = %s", (ws_name,))
    conn.commit()


def test_save_and_search(project_name):
    save(
        project=project_name,
        title="Authentication decision",
        content="We decided to use JWT tokens for stateless auth",
        type="decision",
    )

    results = json.loads(search(project=project_name, query="JWT tokens"))
    assert len(results) >= 1
    assert any("Authentication decision" in r["title"] for r in results)


def test_save_upsert_by_topic_key(project_name):
    topic = "architecture/auth-model"

    save(
        project=project_name,
        title="Auth model v1",
        content="First version of auth model using sessions",
        type="architecture",
        topic_key=topic,
    )
    save(
        project=project_name,
        title="Auth model v2",
        content="Updated auth model using JWT tokens",
        type="architecture",
        topic_key=topic,
    )

    proj = repository.get_or_create_project(project_name)
    observations = repository.get_recent_observations(proj["id"])

    topic_obs = [o for o in observations if o["topic_key"] == topic]
    assert len(topic_obs) == 1
    assert topic_obs[0]["revision_count"] == 2
    assert topic_obs[0]["title"] == "Auth model v2"


def test_context_returns_recent(project_name):
    titles = ["First obs", "Second obs", "Third obs"]
    for title in titles:
        save(
            project=project_name,
            title=title,
            content=f"Content for {title}",
            type="discovery",
        )

    results = json.loads(context(project=project_name, limit=10))
    result_titles = [r["title"] for r in results["observations"]]

    assert "Third obs" in result_titles
    assert "Second obs" in result_titles
    assert "First obs" in result_titles
    assert result_titles.index("Third obs") < result_titles.index("First obs")


def test_list_projects(project_name):
    save(
        project=project_name,
        title="Some observation",
        content="Some content",
        type="config",
    )

    results = json.loads(list_projects())
    names = [r["name"] for r in results]
    assert project_name in names


def test_workspace_isolation():
    suffix = uuid.uuid4()
    ws_a = f"test-ws-a-{suffix}"
    ws_b = f"test-ws-b-{suffix}"
    proj_a = f"test-proj-a-{suffix}"
    proj_b = f"test-proj-b-{suffix}"

    try:
        link_project(proj_a, ws_a)
        save(
            project=proj_a,
            title="Decision in workspace A",
            content="JWT tokens for workspace A auth",
            type="decision",
        )

        link_project(proj_b, ws_b)
        save(
            project=proj_b,
            title="Decision in workspace B",
            content="Session cookies for workspace B auth",
            type="decision",
        )

        results_a = json.loads(search(project=proj_a, query="auth"))
        titles_a = [r["title"] for r in results_a]
        assert "Decision in workspace A" in titles_a
        assert "Decision in workspace B" not in titles_a

        results_b = json.loads(search(project=proj_b, query="auth"))
        titles_b = [r["title"] for r in results_b]
        assert "Decision in workspace B" in titles_b
        assert "Decision in workspace A" not in titles_b
    finally:
        _cleanup_workspace(ws_a)
        _cleanup_workspace(ws_b)


def test_search_global_crosses_workspaces():
    suffix = uuid.uuid4()
    ws_a = f"test-ws-a-{suffix}"
    ws_b = f"test-ws-b-{suffix}"
    proj_a = f"test-proj-a-{suffix}"
    proj_b = f"test-proj-b-{suffix}"

    try:
        link_project(proj_a, ws_a)
        save(
            project=proj_a,
            title="Global decision alpha",
            content="Hexagonal architecture for ws A",
            type="architecture",
        )

        link_project(proj_b, ws_b)
        save(
            project=proj_b,
            title="Global decision beta",
            content="Hexagonal architecture for ws B",
            type="architecture",
        )

        results = json.loads(search_global(query="hexagonal architecture"))
        titles = [r["title"] for r in results]
        assert "Global decision alpha" in titles
        assert "Global decision beta" in titles
    finally:
        _cleanup_workspace(ws_a)
        _cleanup_workspace(ws_b)


def test_no_workspace_backward_compatible(project_name):
    save(
        project=project_name,
        title="Backward compatible observation",
        content="This observation has no workspace",
        type="discovery",
    )

    results = json.loads(search(project=project_name, query="backward compatible"))
    assert len(results) >= 1
    assert any("Backward compatible observation" in r["title"] for r in results)

    ctx = json.loads(context(project=project_name))
    obs = ctx["observations"]
    assert any("Backward compatible observation" in r["title"] for r in obs)

    projects = json.loads(list_projects())
    names = [r["name"] for r in projects]
    assert project_name in names


def test_check_workspace_unlinked(project_name):
    result = json.loads(check_workspace(project_name))
    assert result["linked"] is False
    assert "available_workspaces" in result


def test_check_workspace_linked(project_name):
    ws_name = f"test-ws-{uuid.uuid4()}"
    try:
        link_project(project_name, ws_name)
        result = json.loads(check_workspace(project_name))
        assert result["linked"] is True
        assert result["workspace"]["name"] == ws_name
    finally:
        _cleanup_workspace(ws_name)


def test_save_no_warning_when_workspace_linked():
    ws_name = f"test-ws-{uuid.uuid4()}"
    proj_name = f"test-proj-{uuid.uuid4()}"
    try:
        link_project(proj_name, ws_name)
        result = json.loads(
            save(
                project=proj_name,
                title="Should not warn",
                content="Project is linked to a workspace",
                type="decision",
            )
        )
        assert "_warning" not in result
    finally:
        _cleanup_workspace(ws_name)


def test_save_session_type_accepted(project_name):
    result = json.loads(
        save(
            project=project_name,
            title="Session summary: testing",
            content="Goal: test coverage. Accomplished: P0 gaps filled.",
            type="session",
        )
    )
    assert result["type"] == "session"
    assert "error" not in result


def test_save_invalid_type_rejected(project_name):
    result = json.loads(
        save(
            project=project_name,
            title="Should fail",
            content="Invalid type",
            type="banana",
        )
    )
    assert "error" in result
