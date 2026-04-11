import json

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.database.graph import _prepare_connection, ensure_graph
from memodi.tools.graph import (
    dependencies,
    graph_overview,
    impact_analysis,
    relate,
    remove_relation,
)


@pytest.fixture(autouse=True)
def setup():
    ensure_schema()
    ensure_graph()


@pytest.fixture(autouse=True)
def cleanup():
    yield
    # Clean all nodes and edges from the graph
    conn = get_connection()
    _prepare_connection(conn)
    try:
        conn.execute(
            "SELECT * FROM cypher('memodi', $$ MATCH (n) DETACH DELETE n $$)"
            " AS (result agtype);"
        )
        conn.commit()
    except Exception:
        conn.rollback()


def test_relate_creates_dependency():
    result = json.loads(relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON"))
    assert result["created"] is True
    assert result["from"] == "repo-a"
    assert result["to"] == "repo-b"


def test_dependencies_shows_both_directions():
    relate("Repo", "repo-c", "Repo", "repo-a", "DEPENDS_ON")
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")

    result = json.loads(dependencies("repo-a"))
    depends_on_names = [d["name"] for d in result["depends_on"]]
    depended_by_names = [d["name"] for d in result["depended_by"]]
    assert "repo-b" in depends_on_names
    assert "repo-c" in depended_by_names


def test_impact_analysis_transitive():
    # repo-c -> repo-b -> repo-a (dependency chain)
    relate("Repo", "repo-c", "Repo", "repo-b", "DEPENDS_ON")
    relate("Repo", "repo-b", "Repo", "repo-a", "DEPENDS_ON")

    result = json.loads(impact_analysis("repo-a"))
    affected_names = [a["name"] for a in result["affected"]]
    assert "repo-b" in affected_names
    assert "repo-c" in affected_names


def test_remove_relation():
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    result = json.loads(remove_relation("repo-a", "repo-b", "DEPENDS_ON"))
    assert result["deleted"] is True

    # Verify it's gone
    deps = json.loads(dependencies("repo-a"))
    assert len(deps["depends_on"]) == 0


def test_graph_overview():
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    relate("Repo", "repo-a", "Module", "auth", "CONTAINS")

    result = json.loads(graph_overview())
    assert len(result["nodes"]) >= 3
    assert len(result["edges"]) >= 2


def test_duplicate_edge_upsert():
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON", {"severity": "low"})
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON", {"severity": "high"})

    # Should only have one edge, not two
    deps = json.loads(dependencies("repo-a"))
    assert len(deps["depends_on"]) == 1
