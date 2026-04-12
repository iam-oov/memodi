from datetime import UTC, datetime

from memodi.database.graph import cypher_query, cypher_write, ensure_graph


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def get_impact(name: str, max_depth: int = 5) -> list[dict]:
    """Transitive impact analysis: what is affected if this changes?

    BFS traversal that only follows current edges (invalid_at IS NULL).
    AGE does not support ALL() predicates on variable-length paths,
    so we walk one hop at a time and filter in each step.
    """
    ensure_graph()
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
        visited.update(next_frontier)
        frontier = next_frontier

    return [{"name": n} for n in visited]


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
