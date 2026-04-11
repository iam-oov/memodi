from memodi.database.graph import cypher_query, cypher_write, ensure_graph


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
) -> dict:
    ensure_graph()
    props = properties or {}
    if props:
        props_str = " {" + ", ".join(f"{k}: '{v}'" for k, v in props.items()) + "}"
    else:
        props_str = ""
    # First ensure both nodes exist
    cypher_write(
        f"MERGE (n:{from_label} {{name: '{from_name}'}})",
        "n agtype",
    )
    cypher_write(
        f"MERGE (n:{to_label} {{name: '{to_name}'}})",
        "n agtype",
    )
    # Delete existing edge of same type between same nodes, then create
    delete_q = (
        f"MATCH (a:{from_label} {{name: '{from_name}'}})"
        f"-[r:{edge_label}]->"
        f"(b:{to_label} {{name: '{to_name}'}})"
        " DELETE r"
    )
    cypher_write(delete_q, "r agtype")
    create_q = (
        f"MATCH (a:{from_label} {{name: '{from_name}'}})"
        f", (b:{to_label} {{name: '{to_name}'}})"
        f" CREATE (a)-[r:{edge_label}{props_str}]->(b)"
        " RETURN r"
    )
    results = cypher_write(create_q, "r agtype")
    return results[0] if results else {}


def get_dependencies(name: str) -> list[dict]:
    """What does this node depend on?"""
    ensure_graph()
    return cypher_query(
        f"MATCH (a {{name: '{name}'}})-[:DEPENDS_ON]->(b) RETURN b.name AS name",
        "name agtype",
    )


def get_dependents(name: str) -> list[dict]:
    """What depends on this node?"""
    ensure_graph()
    return cypher_query(
        f"MATCH (a)-[:DEPENDS_ON]->(b {{name: '{name}'}}) RETURN a.name AS name",
        "name agtype",
    )


def get_impact(name: str, max_depth: int = 5) -> list[dict]:
    """Transitive impact analysis: what is affected if this changes?"""
    ensure_graph()
    query = (
        f"MATCH (start {{name: '{name}'}})"
        f"<-[:DEPENDS_ON*1..{max_depth}]-(affected)"
        " RETURN DISTINCT affected.name AS name"
    )
    return cypher_query(query, "name agtype")


def get_modules(repo_name: str) -> list[dict]:
    """Get modules contained in a repo."""
    ensure_graph()
    query = (
        f"MATCH (r:Repo {{name: '{repo_name}'}})"
        "-[:CONTAINS]->(m:Module) RETURN m.name AS name"
    )
    return cypher_query(query, "name agtype")


def remove_edge(
    from_name: str,
    to_name: str,
    edge_label: str,
) -> bool:
    ensure_graph()
    results = cypher_write(
        f"""
        MATCH (a {{name: '{from_name}'}})-[r:{edge_label}]->(b {{name: '{to_name}'}})
        DELETE r
        RETURN true AS deleted
        """,
        "deleted agtype",
    )
    return len(results) > 0


def get_graph_overview() -> dict:
    """Get a summary of the graph."""
    ensure_graph()
    nodes = cypher_query(
        "MATCH (n) RETURN labels(n) AS label, n.name AS name",
        "label agtype, name agtype",
    )
    edges = cypher_query(
        "MATCH (a)-[r]->(b)"
        " RETURN type(r) AS rel, a.name AS from_name, b.name AS to_name",
        "rel agtype, from_name agtype, to_name agtype",
    )
    return {"nodes": nodes, "edges": edges}
