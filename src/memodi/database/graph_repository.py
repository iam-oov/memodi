import re
import uuid
from datetime import UTC, datetime

from memodi.database.graph import cypher_query, cypher_write, ensure_graph

TOPIC_LINK_KEY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}\Z")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _link_workspace(workspace_id: str) -> str:
    """Canonical uuid string for a workspace_id interpolated into Cypher.

    AGE has no parameterized Cypher, so every value reaching the driver
    must be validated first; raises ValueError on anything that isn't a
    real uuid.
    """
    return str(uuid.UUID(str(workspace_id)))


def _require_link_keys(keys: list[str]) -> None:
    """Raise ValueError if any topic link key fails the charset invariant.

    Defense in depth: callers already filter invalid keys before reaching
    here (memory.parse_links), but this is the layer that interpolates
    them into Cypher, so it re-validates everything itself.
    """
    for key in keys:
        if not TOPIC_LINK_KEY_RE.match(key):
            raise ValueError(f"invalid topic link key: {key!r}")


def add_node(label: str, name: str, properties: dict | None = None) -> dict:
    ensure_graph()
    props = properties or {}
    props["name"] = name
    props_str = ", ".join(f"{k}: '{v}'" for k, v in props.items())
    # Upsert: MERGE creates if not exists, SET updates if exists
    results = cypher_write(
        f"MERGE (n:{label} {{name: '{name}'}}) SET n = {{{props_str}}} RETURN n",
        "n agtype",
    )
    return results[0] if results else {}


def add_edge(
    from_label: str,
    from_name: str,
    to_label: str,
    to_name: str,
    edge_label: str,
    properties: dict | None = None,
    valid_at: str | None = None,
) -> dict:
    ensure_graph()
    now = valid_at or _now_iso()
    props = properties or {}
    props["valid_at"] = now

    props_str = " {" + ", ".join(f"{k}: '{v}'" for k, v in props.items()) + "}"

    # First ensure both nodes exist
    cypher_write(
        f"MERGE (n:{from_label} {{name: '{from_name}'}})",
        "n agtype",
    )
    cypher_write(
        f"MERGE (n:{to_label} {{name: '{to_name}'}})",
        "n agtype",
    )
    # Invalidate existing current edge (soft delete, preserves history)
    invalidate_q = (
        f"MATCH (a:{from_label} {{name: '{from_name}'}})"
        f"-[r:{edge_label}]->"
        f"(b:{to_label} {{name: '{to_name}'}})"
        f" WHERE r.invalid_at IS NULL"
        f" SET r.invalid_at = '{now}'"
        " RETURN r"
    )
    cypher_write(invalidate_q, "r agtype")
    # Create new edge with temporal properties
    create_q = (
        f"MATCH (a:{from_label} {{name: '{from_name}'}})"
        f", (b:{to_label} {{name: '{to_name}'}})"
        f" CREATE (a)-[r:{edge_label}{props_str}]->(b)"
        " RETURN r"
    )
    results = cypher_write(create_q, "r agtype")
    return results[0] if results else {}


def get_dependencies(name: str) -> list[dict]:
    """What does this node depend on? (current relationships only)"""
    ensure_graph()
    return cypher_query(
        f"MATCH (a {{name: '{name}'}})-[r:DEPENDS_ON]->(b)"
        " WHERE r.invalid_at IS NULL"
        " RETURN b.name AS name",
        "name agtype",
    )


def get_dependents(name: str) -> list[dict]:
    """What depends on this node? (current relationships only)"""
    ensure_graph()
    return cypher_query(
        f"MATCH (a)-[r:DEPENDS_ON]->(b {{name: '{name}'}})"
        " WHERE r.invalid_at IS NULL"
        " RETURN a.name AS name",
        "name agtype",
    )


def get_impact(
    name: str, max_depth: int = 5, workspace_id: str | None = None
) -> list[dict]:
    """Transitive impact analysis: what is affected if this changes?

    BFS traversal that only follows current edges (invalid_at IS NULL).
    AGE does not support ALL() predicates on variable-length paths,
    so we walk one hop at a time and filter in each step.

    When workspace_id is given, reverse LINKS_TO edges (workspace-scoped
    Topic nodes) feed the same frontier alongside the reverse DEPENDS_ON
    query, so a name reachable through either edge kind is found in one
    BFS. Frontier names failing the topic link key regex skip the
    LINKS_TO sub-query.
    """
    ensure_graph()
    ws = _link_workspace(workspace_id) if workspace_id is not None else None
    visited: set[str] = set()
    frontier: set[str] = {name}

    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for node in frontier:
            results = cypher_query(
                f"MATCH (a)-[r:DEPENDS_ON]->(b {{name: '{node}'}})"
                " WHERE r.invalid_at IS NULL"
                " RETURN a.name AS name",
                "name agtype",
            )
            for r in results:
                if r["name"] not in visited and r["name"] != name:
                    next_frontier.add(r["name"])
            if ws is not None and TOPIC_LINK_KEY_RE.match(node):
                for r in get_topic_links_in(ws, node):
                    if r["name"] not in visited and r["name"] != name:
                        next_frontier.add(r["name"])
        visited.update(next_frontier)
        frontier = next_frontier

    return [{"name": n} for n in visited]


def get_topic_links_out(workspace_id: str, name: str) -> list[dict]:
    """Topics this workspace-scoped Topic node currently links to."""
    ensure_graph()
    if not TOPIC_LINK_KEY_RE.match(name):
        return []
    ws = _link_workspace(workspace_id)
    return cypher_query(
        f"MATCH (a:Topic {{name: '{name}', workspace_id: '{ws}'}})"
        f"-[r:LINKS_TO]->(b:Topic {{workspace_id: '{ws}'}})"
        " WHERE r.invalid_at IS NULL"
        " RETURN b.name AS name",
        "name agtype",
    )


def get_topic_links_in(workspace_id: str, name: str) -> list[dict]:
    """Topics that currently link to this workspace-scoped Topic node."""
    ensure_graph()
    if not TOPIC_LINK_KEY_RE.match(name):
        return []
    ws = _link_workspace(workspace_id)
    return cypher_query(
        f"MATCH (a:Topic {{workspace_id: '{ws}'}})"
        f"-[r:LINKS_TO]->(b:Topic {{name: '{name}', workspace_id: '{ws}'}})"
        " WHERE r.invalid_at IS NULL"
        " RETURN a.name AS name",
        "name agtype",
    )


def sync_topic_links(workspace_id: str, from_key: str, to_keys: list[str]) -> dict:
    """Reconcile from_key's outgoing LINKS_TO edges to exactly to_keys.

    Diffs desired-vs-current instead of invalidate+recreate on every call:
    an unchanged re-save costs one read and zero writes. Removed links get
    invalid_at (history kept); new ones MERGE both Topic nodes and CREATE
    the edge. Raises ValueError (defense in depth) if workspace_id or any
    key fails validation — callers must have already filtered invalid keys.
    """
    ensure_graph()
    ws = _link_workspace(workspace_id)
    _require_link_keys([from_key, *to_keys])

    current = {row["name"] for row in get_topic_links_out(ws, from_key)}
    desired = set(to_keys)
    to_create = desired - current
    to_invalidate = current - desired

    if not to_create and not to_invalidate:
        return {"created": [], "invalidated": []}

    now = _now_iso()
    if to_create:
        cypher_write(
            f"MERGE (a:Topic {{name: '{from_key}', workspace_id: '{ws}'}})",
            "a agtype",
        )
        for key in to_create:
            cypher_write(
                f"MERGE (a:Topic {{name: '{from_key}', workspace_id: '{ws}'}}) "
                f"MERGE (b:Topic {{name: '{key}', workspace_id: '{ws}'}}) "
                f"CREATE (a)-[r:LINKS_TO {{valid_at: '{now}'}}]->(b) "
                "RETURN r",
                "r agtype",
            )
    for key in to_invalidate:
        cypher_write(
            f"MATCH (a:Topic {{name: '{from_key}', workspace_id: '{ws}'}})"
            f"-[r:LINKS_TO]->(b:Topic {{name: '{key}', workspace_id: '{ws}'}})"
            " WHERE r.invalid_at IS NULL"
            f" SET r.invalid_at = '{now}'"
            " RETURN r",
            "r agtype",
        )

    return {"created": sorted(to_create), "invalidated": sorted(to_invalidate)}


def get_modules(repo_name: str) -> list[dict]:
    """Get modules contained in a repo."""
    ensure_graph()
    query = (
        f"MATCH (r:Repo {{name: '{repo_name}'}})"
        "-[rel:CONTAINS]->(m:Module)"
        " WHERE rel.invalid_at IS NULL"
        " RETURN m.name AS name"
    )
    return cypher_query(query, "name agtype")


def remove_edge(
    from_name: str,
    to_name: str,
    edge_label: str,
) -> bool:
    """Soft delete: sets invalid_at on current relationships."""
    ensure_graph()
    now = _now_iso()
    results = cypher_write(
        f"MATCH (a {{name: '{from_name}'}})-[r:{edge_label}]->(b {{name: '{to_name}'}})"
        f" WHERE r.invalid_at IS NULL"
        f" SET r.invalid_at = '{now}'"
        " RETURN true AS invalidated",
        "invalidated agtype",
    )
    return len(results) > 0


def hard_delete_edge(
    from_name: str,
    to_name: str,
    edge_label: str,
) -> bool:
    """Hard delete: physically removes all relationships of this type between nodes."""
    ensure_graph()
    results = cypher_write(
        f"MATCH (a {{name: '{from_name}'}})-[r:{edge_label}]->(b {{name: '{to_name}'}})"
        " DELETE r"
        " RETURN true AS deleted",
        "deleted agtype",
    )
    return len(results) > 0


def get_graph_overview() -> dict:
    """Get a summary of the graph (current relationships only)."""
    ensure_graph()
    nodes = cypher_query(
        "MATCH (n) RETURN labels(n) AS label, n.name AS name",
        "label agtype, name agtype",
    )
    edges = cypher_query(
        "MATCH (a)-[r]->(b)"
        " WHERE r.invalid_at IS NULL"
        " RETURN type(r) AS rel, a.name AS from_name, b.name AS to_name,"
        " r.valid_at AS valid_at",
        "rel agtype, from_name agtype, to_name agtype, valid_at agtype",
    )
    return {"nodes": nodes, "edges": edges}


def count_all_graph_resources() -> dict:
    """Count every node and edge in the graph, including invalidated edges.

    Used by purge dry-run to report what would be wiped. The graph is
    global (not workspace-scoped), so this returns totals across the
    whole memodi graph.
    """
    ensure_graph()
    node_rows = cypher_query(
        "MATCH (n) RETURN count(n) AS c",
        "c agtype",
    )
    edge_rows = cypher_query(
        "MATCH ()-[r]->() RETURN count(r) AS c",
        "c agtype",
    )
    nodes = int(node_rows[0]["c"]) if node_rows else 0
    edges = int(edge_rows[0]["c"]) if edge_rows else 0
    return {"nodes": nodes, "edges": edges}


def purge_all_graph() -> dict:
    """Hard-delete every node and edge in the graph.

    Global operation — the graph has no workspace scoping, so this wipes
    the entire memodi graph. Use only when you know the graph only
    contains data tied to the workspace being purged, or when performing
    a total reset.
    """
    ensure_graph()
    before = count_all_graph_resources()
    # DETACH DELETE removes the node and all its relationships in one go.
    cypher_write("MATCH (n) DETACH DELETE n", "n agtype")
    return {
        "nodes_deleted": before["nodes"],
        "edges_deleted": before["edges"],
    }
