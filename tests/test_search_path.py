"""The AGE graph is named 'memodi' and so is the DB role, so create_graph's
schema is exactly what `"$user"` resolves to. An unpinned search_path
therefore puts the graph's schema ahead of public and every unqualified
statement — ensure_schema's own CREATE TABLE included — silently builds and
reads shadow copies of the app's tables. These tests pin the two legs that
keep that from happening: the session default, and the fact that the graph's
own search_path never outlives its transaction."""

import uuid

import psycopg
import pytest

from memodi.database import graph_repository
from memodi.database.connection import get_connection, rollback
from memodi.database.graph import _prepare_connection
from memodi.tools.memory import save
from tests.conftest import _path


def _delete_topic_nodes(workspace_id: str) -> None:
    conn = get_connection()
    _prepare_connection(conn)
    try:
        conn.execute(
            "SELECT * FROM cypher('memodi', $$ "
            f"MATCH (n:Topic {{workspace_id: '{workspace_id}'}}) DETACH DELETE n "
            "$$) AS (result agtype);"
        )
        conn.commit()
    except Exception:
        conn.rollback()


@pytest.fixture(autouse=True)
def cleanup_topic_nodes(registered_workspace):
    yield
    _delete_topic_nodes(registered_workspace["workspace"]["id"])


@pytest.fixture
def project_name():
    return f"test-search-path-{uuid.uuid4()}"


def _linked_save(registered_workspace, project_name, topic_key):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Linked note",
        content="depends on [[test-search-path/target]]",
        type="discovery",
        topic_key=topic_key,
    )


def _current_search_path(conn: psycopg.Connection) -> str:
    value = conn.execute("SHOW search_path").fetchone()["search_path"]
    rollback()
    return value


def _drop_probe(conn: psycopg.Connection, probe: str) -> None:
    schemas = [
        row["schemaname"]
        for row in conn.execute(
            "SELECT schemaname FROM pg_tables WHERE tablename = %s", (probe,)
        ).fetchall()
    ]
    for schema in schemas:
        conn.execute(f'DROP TABLE IF EXISTS "{schema}".{probe}')
    conn.commit()


def test_linked_save_leaves_search_path_pinned_to_public(
    registered_workspace, project_name
):
    conn = get_connection()

    _linked_save(registered_workspace, project_name, "test-search-path/save")

    assert _current_search_path(conn) == "public"


def test_graph_read_leaves_search_path_pinned_to_public(
    registered_workspace, project_name
):
    conn = get_connection()
    _linked_save(registered_workspace, project_name, "test-search-path/read")

    graph_repository.get_topic_links_out(
        registered_workspace["workspace"]["id"], "test-search-path/read"
    )
    rollback()

    assert _current_search_path(conn) == "public"


def test_graph_transaction_keeps_public_ahead_of_ag_catalog(
    registered_workspace, project_name
):
    """App SQL interleaved with a graph read runs inside the graph's own
    transaction — tools.graph.dependencies reads the graph and then resolves
    a workspace — so ag_catalog must never shadow the app's tables either."""
    conn = get_connection()

    graph_repository.get_topic_links_out(
        registered_workspace["workspace"]["id"], "test-search-path/order"
    )

    assert conn.execute("SHOW search_path").fetchone()["search_path"] == (
        "public, ag_catalog"
    )
    rollback()


def test_unqualified_create_after_a_graph_call_lands_in_public(
    registered_workspace, project_name
):
    conn = get_connection()
    probe = f"search_path_probe_{uuid.uuid4().hex}"
    _linked_save(registered_workspace, project_name, "test-search-path/probe")

    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {probe} (id INT)")
        conn.commit()
        landed = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS in_public", (f"public.{probe}",)
        ).fetchone()
        assert landed["in_public"]
    finally:
        _drop_probe(conn, probe)
