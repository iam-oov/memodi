import json

import psycopg

from memodi.database.connection import get_connection

GRAPH_NAME = "memodi"
_graph_ensured = False


def _prepare_connection(conn: psycopg.Connection) -> None:
    """Load AGE and set search path. Must be called per-transaction.

    LOAD 'age' requires superuser privileges unless the library is listed in
    session_preload_libraries. On a native PostgreSQL where the memodi user
    is not a superuser, AGE is preloaded at connect time via
    `ALTER DATABASE memodi SET session_preload_libraries = 'age'`. In that
    case the explicit LOAD fails with 'access to library "age" is not
    allowed' even though AGE is already available — we swallow that specific
    error and continue. In local Docker dev the memodi user IS superuser, so
    LOAD succeeds normally.

    The search_path is SET LOCAL, so it dies with the transaction that the
    caller commits or rolls back and never follows the shared connection
    into unrelated SQL. `"$user"` is deliberately absent: the DB role is
    named 'memodi' and so is the graph, so it would resolve to the graph's
    own schema and shadow the app's tables. public stays ahead of
    ag_catalog for the same reason — app SQL interleaved with graph reads
    runs inside this transaction (tools.graph.dependencies does exactly
    that), and only cypher() and agtype need ag_catalog at all. LOAD is
    session-level and unaffected by either.
    """
    try:
        conn.execute("LOAD 'age';")
    except psycopg.errors.InsufficientPrivilege:
        # AGE is preloaded via session_preload_libraries; the explicit LOAD
        # is redundant and denied. Rollback the aborted transaction so
        # subsequent statements can execute.
        conn.rollback()
    conn.execute("SET LOCAL search_path = public, ag_catalog;")


def ensure_graph() -> None:
    global _graph_ensured
    if _graph_ensured:
        return
    conn = get_connection()
    _prepare_connection(conn)
    # Check if graph exists
    row = conn.execute(
        "SELECT count(*) as cnt FROM ag_catalog.ag_graph WHERE name = %s",
        (GRAPH_NAME,),
    ).fetchone()
    if row["cnt"] == 0:
        conn.execute("SELECT ag_catalog.create_graph(%s);", (GRAPH_NAME,))
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
