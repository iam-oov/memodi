import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.database.graph import _prepare_connection, ensure_graph
from memodi.tools.graph import relate
from memodi.tools.memory import (
    link_project,
    list_workspaces,
    purge_workspace,
    register_path,
    save,
)
from memodi.tools.workflow import plan as workflow_plan


@pytest.fixture(autouse=True)
def setup():
    ensure_schema()


@pytest.fixture
def ws_name():
    return f"test-purge-ws-{uuid.uuid4()}"


@pytest.fixture
def project_name():
    return f"test-purge-proj-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(ws_name, project_name):
    yield
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM workflow_transitions
        WHERE workflow_id IN (
            SELECT id FROM workflows
            WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        )
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM sessions
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM workspace_paths
        WHERE workspace_id IN (SELECT id FROM workspaces WHERE name = %s)
        """,
        (ws_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.execute("DELETE FROM workspaces WHERE name = %s", (ws_name,))
    conn.commit()


def _seed_workspace(ws_name: str, project_name: str) -> None:
    """Create workspace with some observations, a workflow, and a path."""
    link_project(project=project_name, workspace=ws_name)
    register_path(path=f"/tmp/fake-path-{uuid.uuid4()}", workspace=ws_name)
    save(
        project=project_name,
        title="Seed decision",
        content="A decision that should be wiped",
        type="decision",
    )
    save(
        project=project_name,
        title="Seed discovery",
        content="Something I found that should be wiped",
        type="discovery",
    )
    workflow_plan(
        project=project_name,
        name="seed-workflow",
        objective="Seed for purge tests",
    )


# --- Validation ---


def test_purge_unknown_workspace_returns_error():
    result = json.loads(
        purge_workspace(
            workspace=f"does-not-exist-{uuid.uuid4()}",
            dry_run=True,
        )
    )
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_purge_invalid_mode_returns_error(ws_name, project_name):
    _seed_workspace(ws_name, project_name)
    result = json.loads(
        purge_workspace(workspace=ws_name, mode="soft", dry_run=True)
    )
    assert "error" in result


# --- Dry run ---


def test_dry_run_reports_counts_without_deleting(ws_name, project_name):
    _seed_workspace(ws_name, project_name)

    result = json.loads(purge_workspace(workspace=ws_name, dry_run=True))

    assert result["dry_run"] is True
    assert result["mode"] == "medium"
    assert result["would_delete"]["observations"] >= 2
    assert result["would_delete"]["workflows"] >= 1
    assert project_name in result["would_preserve"]["projects"]

    # Nothing actually deleted.
    conn = get_connection()
    obs_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    ).fetchone()["c"]
    assert obs_count >= 2


def test_dry_run_hard_mode_shows_workspace_in_would_delete(
    ws_name, project_name
):
    _seed_workspace(ws_name, project_name)

    result = json.loads(
        purge_workspace(workspace=ws_name, mode="hard", dry_run=True)
    )

    assert result["would_delete"]["workspace"] is True
    assert result["would_delete"]["projects"] >= 1
    assert result["would_preserve"] == {}


# --- Medium mode ---


def test_medium_deletes_observations_preserves_workspace(
    ws_name, project_name
):
    _seed_workspace(ws_name, project_name)

    result = json.loads(
        purge_workspace(workspace=ws_name, mode="medium", dry_run=False)
    )

    assert result["dry_run"] is False
    assert result["observations"] >= 2
    assert result["workflows"] >= 1
    assert result["workspace_deleted"] is False

    # Workspace still exists.
    workspaces = json.loads(list_workspaces())
    assert any(w["name"] == ws_name for w in workspaces)

    # Observations gone.
    conn = get_connection()
    obs_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    ).fetchone()["c"]
    assert obs_count == 0

    # Workflows gone too.
    wf_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    ).fetchone()["c"]
    assert wf_count == 0

    # Project still exists (medium preserves structure).
    proj_count = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE name = %s",
        (project_name,),
    ).fetchone()["c"]
    assert proj_count == 1


# --- Hard mode ---


def test_hard_deletes_everything_including_workspace(ws_name, project_name):
    _seed_workspace(ws_name, project_name)

    result = json.loads(
        purge_workspace(workspace=ws_name, mode="hard", dry_run=False)
    )

    assert result["workspace_deleted"] is True
    assert result["projects"] >= 1

    # Workspace gone.
    workspaces = json.loads(list_workspaces())
    assert not any(w["name"] == ws_name for w in workspaces)

    # Project gone (hard mode removes it).
    conn = get_connection()
    proj_count = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE name = %s",
        (project_name,),
    ).fetchone()["c"]
    assert proj_count == 0


# --- Graph opt-in ---


def test_graph_preserved_by_default(ws_name, project_name):
    """purge_graph defaults to False — the graph is global and should
    not be touched unless explicitly opted in."""
    _seed_workspace(ws_name, project_name)
    ensure_graph()
    # Seed the global graph with something unrelated to this workspace.
    relate("Repo", f"sentinel-{ws_name}", "Repo", "sentinel-target", "DEPENDS_ON")

    try:
        purge_workspace(workspace=ws_name, mode="medium", dry_run=False)

        # Sentinel node survives because we did NOT opt into graph purge.
        conn = get_connection()
        _prepare_connection(conn)
        row = conn.execute(
            "SELECT * FROM cypher('memodi',"
            f" $$ MATCH (n {{name: 'sentinel-{ws_name}'}}) RETURN count(n) AS c $$)"
            " AS (c agtype);"
        ).fetchone()
        conn.commit()
        assert row is not None
        assert int(str(row["c"])) == 1
    finally:
        # Always clean the sentinel to keep the shared graph tidy.
        conn = get_connection()
        _prepare_connection(conn)
        conn.execute(
            "SELECT * FROM cypher('memodi',"
            f" $$ MATCH (n {{name: 'sentinel-{ws_name}'}}) DETACH DELETE n $$)"
            " AS (result agtype);"
        )
        conn.execute(
            "SELECT * FROM cypher('memodi',"
            " $$ MATCH (n {name: 'sentinel-target'}) DETACH DELETE n $$)"
            " AS (result agtype);"
        )
        conn.commit()


def test_graph_wiped_when_opt_in(ws_name, project_name):
    _seed_workspace(ws_name, project_name)
    ensure_graph()
    relate("Repo", f"purge-me-{ws_name}", "Repo", "purge-target", "DEPENDS_ON")

    result = json.loads(
        purge_workspace(
            workspace=ws_name,
            mode="medium",
            purge_graph=True,
            dry_run=False,
        )
    )

    assert "graph_nodes_deleted" in result
    assert result["graph_nodes_deleted"] >= 2
    assert result["graph_edges_deleted"] >= 1

    # Graph now empty.
    conn = get_connection()
    _prepare_connection(conn)
    row = conn.execute(
        "SELECT * FROM cypher('memodi', $$ MATCH (n) RETURN count(n) AS c $$)"
        " AS (c agtype);"
    ).fetchone()
    conn.commit()
    assert int(str(row["c"])) == 0


def test_dry_run_with_graph_reports_graph_counts(ws_name, project_name):
    _seed_workspace(ws_name, project_name)
    ensure_graph()
    relate("Repo", f"preview-{ws_name}", "Repo", "preview-target", "DEPENDS_ON")

    try:
        result = json.loads(
            purge_workspace(
                workspace=ws_name,
                mode="medium",
                purge_graph=True,
                dry_run=True,
            )
        )
        assert result["dry_run"] is True
        assert result["purge_graph"] is True
        assert "graph_nodes" in result["would_delete"]
        assert result["would_delete"]["graph_nodes"] >= 2

        # Still present — it was a dry run.
        conn = get_connection()
        _prepare_connection(conn)
        row = conn.execute(
            "SELECT * FROM cypher('memodi',"
            f" $$ MATCH (n {{name: 'preview-{ws_name}'}}) RETURN count(n) AS c $$)"
            " AS (c agtype);"
        ).fetchone()
        conn.commit()
        assert int(str(row["c"])) == 1
    finally:
        conn = get_connection()
        _prepare_connection(conn)
        conn.execute(
            "SELECT * FROM cypher('memodi',"
            f" $$ MATCH (n {{name: 'preview-{ws_name}'}}) DETACH DELETE n $$)"
            " AS (result agtype);"
        )
        conn.execute(
            "SELECT * FROM cypher('memodi',"
            " $$ MATCH (n {name: 'preview-target'}) DETACH DELETE n $$)"
            " AS (result agtype);"
        )
        conn.commit()
