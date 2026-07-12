import json
import uuid

import pytest

from memodi.database import auth_repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.database.graph import _prepare_connection, ensure_graph
from memodi.tools.graph import relate
from memodi.tools.memory import list_workspaces, purge_workspace, save
from memodi.tools.workflow import plan as workflow_plan
from tests.conftest import _path, cleanup_rows


@pytest.fixture(autouse=True)
def setup():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-purge-proj-{uuid.uuid4()}"


def _seed_workspace(registered_workspace, project_name) -> None:
    """Create observations and a workflow inside the registered workspace."""
    path = _path(registered_workspace, project_name)
    user_id = registered_workspace["user_id"]
    machine = registered_workspace["machine"]

    save(
        path=path,
        user_id=user_id,
        machine=machine,
        title="Seed decision",
        content="A decision that should be wiped",
        type="decision",
    )
    save(
        path=path,
        user_id=user_id,
        machine=machine,
        title="Seed discovery",
        content="Something I found that should be wiped",
        type="discovery",
    )
    workflow_plan(
        path=path,
        user_id=user_id,
        machine=machine,
        name="seed-workflow",
        objective="Seed for purge tests",
    )


# --- Validation ---


def test_purge_unknown_workspace_returns_error(registered_workspace):
    result = json.loads(
        purge_workspace(
            workspace=f"does-not-exist-{uuid.uuid4()}",
            user_id=registered_workspace["user_id"],
            dry_run=True,
        )
    )
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_purge_invalid_mode_returns_error(registered_workspace, project_name):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    result = json.loads(
        purge_workspace(
            workspace=ws_name,
            user_id=registered_workspace["user_id"],
            mode="soft",
            dry_run=True,
        )
    )
    assert "error" in result


def test_purge_rejects_other_owner(registered_workspace, project_name):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    other_email = f"test-purge-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(other_email)
    try:
        result = json.loads(
            purge_workspace(workspace=ws_name, user_id=other["id"], dry_run=True)
        )
        assert "error" in result
        assert "not found" in result["error"].lower()
    finally:
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


def test_purge_rejects_other_owner_execute_and_preserves_data(
    registered_workspace, project_name
):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    other_email = f"test-purge-owner-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(other_email)
    try:
        result = json.loads(
            purge_workspace(workspace=ws_name, user_id=other["id"], dry_run=False)
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

        # A non-owner execute must not touch the real owner's data.
        conn = get_connection()
        obs_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM observations
            WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
            """,
            (project_name,),
        ).fetchone()["c"]
        assert obs_count >= 2
        wf_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM workflows
            WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
            """,
            (project_name,),
        ).fetchone()["c"]
        assert wf_count >= 1
    finally:
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


# --- Dry run ---


def test_dry_run_reports_counts_without_deleting(registered_workspace, project_name):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]

    result = json.loads(
        purge_workspace(
            workspace=ws_name, user_id=registered_workspace["user_id"], dry_run=True
        )
    )

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
    registered_workspace, project_name
):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]

    result = json.loads(
        purge_workspace(
            workspace=ws_name,
            user_id=registered_workspace["user_id"],
            mode="hard",
            dry_run=True,
        )
    )

    assert result["would_delete"]["workspace"] is True
    assert result["would_delete"]["projects"] >= 1
    assert result["would_preserve"] == {}


# --- Medium mode ---


def test_medium_deletes_observations_preserves_workspace(
    registered_workspace, project_name
):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    user_id = registered_workspace["user_id"]

    result = json.loads(
        purge_workspace(
            workspace=ws_name, user_id=user_id, mode="medium", dry_run=False
        )
    )

    assert result["dry_run"] is False
    assert result["observations"] >= 2
    assert result["workflows"] >= 1
    assert result["workspace_deleted"] is False

    # Workspace still exists.
    workspaces = json.loads(list_workspaces(user_id))
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


def test_hard_deletes_everything_including_workspace(
    registered_workspace, project_name
):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    user_id = registered_workspace["user_id"]

    result = json.loads(
        purge_workspace(workspace=ws_name, user_id=user_id, mode="hard", dry_run=False)
    )

    assert result["workspace_deleted"] is True
    assert result["projects"] >= 1

    # Workspace gone.
    workspaces = json.loads(list_workspaces(user_id))
    assert not any(w["name"] == ws_name for w in workspaces)

    # Project gone (hard mode removes it).
    conn = get_connection()
    proj_count = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE name = %s",
        (project_name,),
    ).fetchone()["c"]
    assert proj_count == 0


# --- Graph opt-in ---


def test_graph_preserved_by_default(registered_workspace, project_name):
    """purge_graph defaults to False — the graph is global and should
    not be touched unless explicitly opted in."""
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    user_id = registered_workspace["user_id"]
    ensure_graph()
    # Seed the global graph with something unrelated to this workspace.
    relate("Repo", f"sentinel-{ws_name}", "Repo", "sentinel-target", "DEPENDS_ON")

    try:
        purge_workspace(
            workspace=ws_name, user_id=user_id, mode="medium", dry_run=False
        )

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


def test_graph_wiped_when_opt_in(registered_workspace, project_name):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    user_id = registered_workspace["user_id"]
    ensure_graph()
    relate("Repo", f"purge-me-{ws_name}", "Repo", "purge-target", "DEPENDS_ON")

    result = json.loads(
        purge_workspace(
            workspace=ws_name,
            user_id=user_id,
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


def test_dry_run_with_graph_reports_graph_counts(registered_workspace, project_name):
    _seed_workspace(registered_workspace, project_name)
    ws_name = registered_workspace["workspace"]["name"]
    user_id = registered_workspace["user_id"]
    ensure_graph()
    relate("Repo", f"preview-{ws_name}", "Repo", "preview-target", "DEPENDS_ON")

    try:
        result = json.loads(
            purge_workspace(
                workspace=ws_name,
                user_id=user_id,
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
