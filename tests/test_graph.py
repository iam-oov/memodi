import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Apache AGE not available in CI",
)

from memodi.database.connection import ensure_schema, get_connection  # noqa: E402
from memodi.database.graph import _prepare_connection, ensure_graph  # noqa: E402
from memodi.tools.graph import (  # noqa: E402
    delete_relation,
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


def test_remove_relation_soft_deletes():
    """remove_relation sets invalid_at instead of deleting."""
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    result = json.loads(remove_relation("repo-a", "repo-b", "DEPENDS_ON"))
    assert result["invalidated"] is True

    # Should not appear in current dependencies
    deps = json.loads(dependencies("repo-a"))
    assert len(deps["depends_on"]) == 0


def test_hard_delete_removes_edge():
    """delete_relation physically removes the edge."""
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    result = json.loads(delete_relation("repo-a", "repo-b", "DEPENDS_ON"))
    assert result["deleted"] is True

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

    # Should only have one CURRENT edge, not two
    deps = json.loads(dependencies("repo-a"))
    assert len(deps["depends_on"]) == 1


# --- Temporal triplet tests ---


def test_overview_includes_valid_at():
    """New relationships should have valid_at in overview."""
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")

    result = json.loads(graph_overview())
    edges = result["edges"]
    assert len(edges) >= 1
    edge = edges[0]
    assert "valid_at" in edge
    assert edge["valid_at"] is not None


def test_relate_with_explicit_valid_at():
    """Can pass a custom valid_at timestamp."""
    relate(
        "Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON",
        valid_at="2025-01-15T00:00:00+00:00",
    )

    result = json.loads(graph_overview())
    edge = result["edges"][0]
    assert "2025-01-15" in edge["valid_at"]


def test_invalidated_edges_hidden_from_overview():
    """Overview only shows current relationships."""
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    remove_relation("repo-a", "repo-b", "DEPENDS_ON")

    result = json.loads(graph_overview())
    assert len(result["edges"]) == 0


def test_invalidated_edges_hidden_from_dependencies():
    """Dependencies only returns current relationships."""
    relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")
    relate("Repo", "repo-a", "Repo", "repo-c", "DEPENDS_ON")
    remove_relation("repo-a", "repo-b", "DEPENDS_ON")

    deps = json.loads(dependencies("repo-a"))
    names = [d["name"] for d in deps["depends_on"]]
    assert "repo-c" in names
    assert "repo-b" not in names


def test_invalidated_edges_hidden_from_impact():
    """Impact analysis only traverses current relationships."""
    # repo-c -> repo-b -> repo-a
    relate("Repo", "repo-c", "Repo", "repo-b", "DEPENDS_ON")
    relate("Repo", "repo-b", "Repo", "repo-a", "DEPENDS_ON")
    # Break the chain: invalidate repo-b -> repo-a
    remove_relation("repo-b", "repo-a", "DEPENDS_ON")

    result = json.loads(impact_analysis("repo-a"))
    affected_names = [a["name"] for a in result["affected"]]
    # repo-b no longer depends on repo-a (invalidated)
    assert "repo-b" not in affected_names
    assert "repo-c" not in affected_names
