import json
import uuid

import pytest

from memodi.database import graph_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.database.graph import _prepare_connection, ensure_graph
from memodi.tools.graph import (
    delete_relation,
    dependencies,
    graph_overview,
    impact_analysis,
    relate,
    remove_relation,
)
from tests.conftest import _path


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


# --- Topic auto-linking (LINKS_TO) ---


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


def test_sync_topic_links_creates_workspace_scoped_edges(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    result = graph_repository.sync_topic_links(ws, "source/key", ["target/key"])

    assert result == {"created": ["target/key"], "invalidated": []}
    assert graph_repository.get_topic_links_out(ws, "source/key") == [
        {"name": "target/key"}
    ]
    assert graph_repository.get_topic_links_in(ws, "target/key") == [
        {"name": "source/key"}
    ]


def test_sync_topic_links_idempotent_on_unchanged_links(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    graph_repository.sync_topic_links(ws, "source/key", ["a/one", "a/two"])

    result = graph_repository.sync_topic_links(ws, "source/key", ["a/one", "a/two"])

    assert result == {"created": [], "invalidated": []}
    assert len(graph_repository.get_topic_links_out(ws, "source/key")) == 2


def test_sync_topic_links_invalidates_removed_links(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    graph_repository.sync_topic_links(ws, "source/key", ["a/one", "a/two"])

    result = graph_repository.sync_topic_links(ws, "source/key", ["a/one"])

    assert result == {"created": [], "invalidated": ["a/two"]}
    links = graph_repository.get_topic_links_out(ws, "source/key")
    assert [row["name"] for row in links] == ["a/one"]


def test_sync_topic_links_raises_on_invalid_key(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    with pytest.raises(ValueError):
        graph_repository.sync_topic_links(ws, "bad'key", ["a/one"])


def test_sync_topic_links_raises_on_invalid_workspace():
    with pytest.raises(ValueError):
        graph_repository.sync_topic_links("not-a-uuid", "source/key", ["a/one"])


def test_topic_links_not_visible_across_workspaces(registered_workspace):
    ws_a = registered_workspace["workspace"]["id"]
    other = _extra_workspace(registered_workspace["user_id"])
    ws_b = other["workspace"]["id"]
    try:
        graph_repository.sync_topic_links(ws_a, "shared/key", ["target/only-in-a"])
        graph_repository.sync_topic_links(ws_b, "shared/key", ["target/only-in-b"])

        out_a = graph_repository.get_topic_links_out(ws_a, "shared/key")
        out_b = graph_repository.get_topic_links_out(ws_b, "shared/key")
        links_a = [r["name"] for r in out_a]
        links_b = [r["name"] for r in out_b]

        assert links_a == ["target/only-in-a"]
        assert links_b == ["target/only-in-b"]
    finally:
        repository.delete_workspace(
            other["workspace"]["name"], registered_workspace["user_id"]
        )


def test_topic_link_reads_reject_an_invalid_name(registered_workspace):
    """`name` arrives unvalidated from the MCP caller — memodi_dependencies
    with a path hands it straight through — and these two guards are all
    that stands between it and an interpolated Cypher string. A rejected
    name yields no links and leaves the shared connection able to answer
    the next query."""
    ws = registered_workspace["workspace"]["id"]
    conn = get_connection()

    assert graph_repository.get_topic_links_out(ws, "bad'key") == []
    assert graph_repository.get_topic_links_in(ws, "bad'key") == []

    assert conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    conn.rollback()


def test_dependencies_includes_links_only_with_path(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    graph_repository.sync_topic_links(ws, "source/key", ["target/key"])

    without_path = json.loads(dependencies("source/key"))
    assert "links_to" not in without_path
    assert "linked_from" not in without_path

    with_path = json.loads(
        dependencies(
            "source/key",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            path=_path(registered_workspace, "test-project"),
        )
    )
    assert with_path["links_to"] == [{"name": "target/key"}]
    assert with_path["linked_from"] == []


def test_impact_traverses_links_to_only_when_scoped(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    graph_repository.sync_topic_links(ws, "consumer/key", ["provider/key"])

    scoped = json.loads(
        impact_analysis(
            "provider/key",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            path=_path(registered_workspace, "test-project"),
        )
    )
    assert "consumer/key" in [a["name"] for a in scoped["affected"]]

    unscoped = json.loads(impact_analysis("provider/key"))
    assert unscoped["affected"] == []


def test_impact_bridges_links_to_and_depends_on_by_name(registered_workspace):
    """A BFS frontier name is shared across edge kinds: a Repo DEPENDS_ON
    node and a workspace-scoped Topic LINKS_TO node with the same `name`
    both feed the same next_frontier."""
    ws = registered_workspace["workspace"]["id"]
    relate("Repo", "repo-x", "Repo", "shared/topic", "DEPENDS_ON")
    graph_repository.sync_topic_links(ws, "doc/note", ["shared/topic"])

    result = json.loads(
        impact_analysis(
            "shared/topic",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            path=_path(registered_workspace, "test-project"),
        )
    )
    affected_names = [a["name"] for a in result["affected"]]
    assert "repo-x" in affected_names
    assert "doc/note" in affected_names


def test_impact_hides_invalidated_links(registered_workspace):
    ws = registered_workspace["workspace"]["id"]
    graph_repository.sync_topic_links(ws, "consumer/key", ["provider/key"])
    graph_repository.sync_topic_links(ws, "consumer/key", [])

    result = json.loads(
        impact_analysis(
            "provider/key",
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            path=_path(registered_workspace, "test-project"),
        )
    )
    assert result["affected"] == []
