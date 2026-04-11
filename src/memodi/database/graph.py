import json

import psycopg

from memodi.database.connection import get_connection

GRAPH_NAME = "memodi"
_graph_ensured = False


def _prepare_connection(conn: psycopg.Connection) -> None:
    """Load AGE and set search path. Must be called per-transaction."""
    conn.execute("LOAD 'age';")
    conn.execute('SET search_path = ag_catalog, "$user", public;')


def ensure_graph() -> None:
    global _graph_ensured
    if _graph_ensured:
        return
    conn = get_connection()
    _prepare_connection(conn)
    # Check if graph exists
    row = conn.execute(
        "SELECT count(*) as cnt FROM ag_graph WHERE name = %s",
        (GRAPH_NAME,),
    ).fetchone()
    if row["cnt"] == 0:
        conn.execute("SELECT create_graph(%s);", (GRAPH_NAME,))
    conn.commit()
    _graph_ensured = True


def cypher_query(query: str, columns: str = "result agtype") -> list[dict]:
    """Execute a Cypher query and return results as list of dicts."""
    conn = get_connection()
    _prepare_connection(conn)
    sql = f"SELECT * FROM cypher('{GRAPH_NAME}', $${query}$$) AS ({columns});"
    rows = conn.execute(sql).fetchall()
    results = []
    for row in rows:
        parsed = {}
        for key, value in row.items():
            if value is not None:
                # agtype comes as string, parse it
                try:
                    parsed[key] = json.loads(str(value))
                except (json.JSONDecodeError, TypeError):
                    parsed[key] = str(value)
            else:
                parsed[key] = None
        results.append(parsed)
    return results


def cypher_write(query: str, columns: str = "result agtype") -> list[dict]:
    """Execute a Cypher write query, commit, and return results."""
    results = cypher_query(query, columns)
    conn = get_connection()
    conn.commit()
    return results
