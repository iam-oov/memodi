import json
import uuid

import pytest
from psycopg.pq import TransactionStatus
from starlette.testclient import TestClient

from memodi import server
from memodi.database import graph_repository, repository
from memodi.database.connection import get_connection
from memodi.database.graph import _prepare_connection, cypher_query
from memodi.tools.memory import backfill_links, save
from tests.conftest import _path

SAVE_ACK_FIELDS = {
    "id",
    "title",
    "type",
    "topic_key",
    "revision_count",
    "duplicate_count",
    "created_at",
    "updated_at",
}


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
    return f"test-links-{uuid.uuid4()}"


def _save(registered_workspace, project_name, **kwargs):
    return save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        **kwargs,
    )


# --- save() auto-linking ---


def test_save_with_links_creates_edges_and_acks(registered_workspace, project_name):
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Source note",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key="test-links/source",
        )
    )

    assert ack["links"] == {
        "created": ["test-links/target"],
        "invalidated": [],
        "skipped_invalid": 0,
    }
    ws = registered_workspace["workspace"]["id"]
    links = graph_repository.get_topic_links_out(ws, "test-links/source")
    assert links == [{"name": "test-links/target"}]


def test_save_ack_exact_field_set_with_links(registered_workspace, project_name):
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Source note",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key="test-links/source",
        )
    )

    assert set(ack.keys()) == SAVE_ACK_FIELDS | {"links"}


def test_save_linkless_content_has_no_links_key(registered_workspace, project_name):
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="No links here",
            content="just plain content",
            type="discovery",
            topic_key="test-links/linkless",
        )
    )

    assert "links" not in ack


def test_save_link_without_topic_key_skips_and_creates_no_nodes(
    registered_workspace, project_name
):
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="No topic key",
            content="depends on [[test-links/target]]",
            type="discovery",
        )
    )

    assert ack["links"] == {"skipped": "no_topic_key"}
    ws = registered_workspace["workspace"]["id"]
    nodes = cypher_query(
        f"MATCH (n:Topic {{workspace_id: '{ws}'}}) RETURN n.name AS name",
        "name agtype",
    )
    assert nodes == []


def test_save_unclosed_bracket_content_has_no_links_key(
    registered_workspace, project_name
):
    """`[[` on its own is not a link: prose about array indexing must not
    earn a `links` ack claiming something was skipped."""
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Prose about brackets",
            content="the array index a[[i] is wrong",
            type="discovery",
        )
    )

    assert "links" not in ack


def test_save_link_with_invalid_topic_key_skips(registered_workspace, project_name):
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Invalid topic key",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key="bad key with spaces",
        )
    )

    assert ack["links"] == {"skipped": "invalid_topic_key"}


def test_save_upsert_with_unchanged_links_does_not_churn(
    registered_workspace, project_name
):
    topic = "test-links/upsert"
    _save(
        registered_workspace,
        project_name,
        title="v1",
        content="depends on [[test-links/target]]",
        type="discovery",
        topic_key=topic,
    )

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="v2",
            content="still depends on [[test-links/target]]",
            type="discovery",
            topic_key=topic,
        )
    )

    assert ack["links"] == {"created": [], "invalidated": [], "skipped_invalid": 0}
    ws = registered_workspace["workspace"]["id"]
    assert len(graph_repository.get_topic_links_out(ws, topic)) == 1


def test_save_removing_link_invalidates_it(registered_workspace, project_name):
    topic = "test-links/removal"
    _save(
        registered_workspace,
        project_name,
        title="v1",
        content="depends on [[test-links/target]]",
        type="discovery",
        topic_key=topic,
    )

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="v2",
            content="no longer depends on anything",
            type="discovery",
            topic_key=topic,
        )
    )

    assert ack["links"] == {
        "created": [],
        "invalidated": ["test-links/target"],
        "skipped_invalid": 0,
    }
    ws = registered_workspace["workspace"]["id"]
    assert graph_repository.get_topic_links_out(ws, topic) == []


def test_save_self_link_ignored(registered_workspace, project_name):
    topic = "test-links/self"
    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Self reference",
            content=f"see [[{topic}]] for details",
            type="discovery",
            topic_key=topic,
        )
    )

    assert ack["links"] == {"created": [], "invalidated": [], "skipped_invalid": 0}
    ws = registered_workspace["workspace"]["id"]
    assert graph_repository.get_topic_links_out(ws, topic) == []


def test_save_invalid_link_keys_skipped_connection_stays_healthy(
    registered_workspace, project_name
):
    """The status is read off a held reference: get_connection() runs its
    own liveness SELECT, which would open a transaction and mask the
    answer."""
    conn = get_connection()

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Injection attempt",
            content="see [[bad'key]] and [[valid/key]]",
            type="discovery",
            topic_key="test-links/injection",
        )
    )

    assert ack["links"] == {
        "created": ["valid/key"],
        "invalidated": [],
        "skipped_invalid": 1,
    }
    assert conn.info.transaction_status == TransactionStatus.IDLE


def test_save_links_scoped_to_workspace(registered_workspace, project_name):
    ws_a = registered_workspace["workspace"]["id"]
    user_id = registered_workspace["user_id"]
    machine = f"test-machine-{uuid.uuid4()}"
    root = f"/tmp/test-links-extra-{uuid.uuid4()}"
    ws_name = f"test-links-extra-ws-{uuid.uuid4()}"
    other_workspace = repository.workspace_start(user_id, machine, root, ws_name)

    try:
        save(
            path=f"{root}/{project_name}",
            user_id=user_id,
            machine=machine,
            title="Other workspace note",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key="test-links/isolated",
        )

        assert graph_repository.get_topic_links_out(ws_a, "test-links/isolated") == []
        assert graph_repository.get_topic_links_out(
            other_workspace["id"], "test-links/isolated"
        ) == [{"name": "test-links/target"}]
    finally:
        _delete_topic_nodes(other_workspace["id"])
        repository.delete_workspace(ws_name, user_id)


def test_save_graph_failure_never_breaks_save(
    registered_workspace, project_name, monkeypatch
):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(graph_repository, "sync_topic_links", _raise)

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Survives a graph failure",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key="test-links/graph-failure",
        )
    )

    assert "error" not in ack
    assert "id" in ack
    assert "links" not in ack


def test_save_graph_db_failure_leaves_connection_usable(
    registered_workspace, project_name, monkeypatch
):
    topic = "test-links/db-failure"

    def broken_sync(*args, **kwargs):
        return [
            dict(r)
            for r in get_connection()
            .execute("SELECT no_such_column FROM observations LIMIT 1")
            .fetchall()
        ]

    monkeypatch.setattr(graph_repository, "sync_topic_links", broken_sync)
    conn_before = get_connection()

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Save during a graph db failure",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key=topic,
        )
    )
    assert "error" not in ack
    assert "id" in ack
    assert "links" not in ack
    assert get_connection() is conn_before

    monkeypatch.undo()

    recovered = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Recovered save",
            content="depends on [[test-links/target]]",
            type="discovery",
            topic_key=topic,
        )
    )
    assert "error" not in recovered
    assert recovered["links"]["created"] == ["test-links/target"]


def test_save_leaves_no_open_transaction_with_links(registered_workspace, project_name):
    conn = get_connection()
    topic = "test-links/no-open-tx"

    _save(
        registered_workspace,
        project_name,
        title="First save",
        content="depends on [[test-links/target]]",
        type="discovery",
        topic_key=topic,
    )
    assert conn.info.transaction_status == TransactionStatus.IDLE

    ack = json.loads(
        _save(
            registered_workspace,
            project_name,
            title="Second save, unchanged link",
            content="still depends on [[test-links/target]]",
            type="discovery",
            topic_key=topic,
        )
    )
    assert ack["links"] == {"created": [], "invalidated": [], "skipped_invalid": 0}
    assert conn.info.transaction_status == TransactionStatus.IDLE


def test_capture_route_passes_through_links(registered_workspace, project_name):
    client = TestClient(server.mcp.streamable_http_app())

    response = client.post(
        "/hooks/capture",
        json={
            "path": _path(registered_workspace, project_name),
            "title": "Subagent finding",
            "content": "depends on [[test-links/target]]",
            "topic_key": "test-links/capture",
        },
        headers={
            "X-Memodi-Api-Key": registered_workspace["api_key"],
            "X-Memodi-Machine": registered_workspace["machine"],
        },
    )

    assert response.status_code == 200
    assert response.json()["links"] == {
        "created": ["test-links/target"],
        "invalidated": [],
        "skipped_invalid": 0,
    }


# --- backfill_links ---


def test_backfill_links_creates_edges_for_preexisting_rows(
    registered_workspace, project_name
):
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    repository.save_observation(
        project_id=proj["id"],
        title="Pre-feature note",
        content="depends on [[test-links/backfill-target]]",
        type="discovery",
        topic_key="test-links/backfill-source",
    )

    result = json.loads(
        backfill_links(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["scanned"] == 1
    assert result["edges_created"] == 1
    assert result["edges_invalidated"] == 0
    assert result["project"] == project_name

    ws = registered_workspace["workspace"]["id"]
    links = graph_repository.get_topic_links_out(ws, "test-links/backfill-source")
    assert links == [{"name": "test-links/backfill-target"}]


def test_backfill_links_second_run_creates_no_edges(registered_workspace, project_name):
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    repository.save_observation(
        project_id=proj["id"],
        title="Pre-feature note",
        content="depends on [[test-links/backfill-target]]",
        type="discovery",
        topic_key="test-links/backfill-source",
    )
    path = _path(registered_workspace, project_name)
    kwargs = dict(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    backfill_links(**kwargs)

    result = json.loads(backfill_links(**kwargs))

    assert result["edges_created"] == 0
    assert result["edges_invalidated"] == 0


def test_backfill_links_ignores_keyless_linkless_deleted_and_superseded(
    registered_workspace, project_name
):
    ws_id = registered_workspace["workspace"]["id"]
    proj = repository.get_or_create_project(project_name, workspace_id=ws_id)

    repository.save_observation(
        project_id=proj["id"],
        title="Keyless",
        content="[[test-links/x]]",
        type="discovery",
    )
    repository.save_observation(
        project_id=proj["id"],
        title="Linkless",
        content="no links here",
        type="discovery",
        topic_key="test-links/linkless-key",
    )
    deleted = repository.save_observation(
        project_id=proj["id"],
        title="Deleted",
        content="[[test-links/y]]",
        type="discovery",
        topic_key="test-links/deleted-key",
    )
    repository.delete_observation(str(deleted["id"]), ws_id)
    old = repository.save_observation(
        project_id=proj["id"],
        title="Old",
        content="[[test-links/z]]",
        type="discovery",
        topic_key="test-links/superseded-key",
    )
    new = repository.save_observation(
        project_id=proj["id"], title="New", content="no links now", type="discovery"
    )
    repository.supersede_observation(
        old_id=str(old["id"]), new_id=str(new["id"]), workspace_id=ws_id
    )

    result = json.loads(
        backfill_links(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["scanned"] == 0
    assert result["edges_created"] == 0


def test_backfill_links_isolates_a_failing_row(
    registered_workspace, project_name, monkeypatch
):
    """One poisoned row must not abort the scan: a backfill over three years
    of history that dies on row two would leave the operator with an
    internal error, no counts, and an aborted transaction on the shared
    connection."""
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    for key in ("test-links/bf-one", "test-links/bf-boom", "test-links/bf-three"):
        repository.save_observation(
            project_id=proj["id"],
            title=key,
            content="depends on [[test-links/backfill-target]]",
            type="discovery",
            topic_key=key,
        )
    real_sync = graph_repository.sync_topic_links

    def sync_or_boom(workspace_id, from_key, to_keys):
        if from_key == "test-links/bf-boom":
            raise RuntimeError("boom")
        return real_sync(workspace_id, from_key, to_keys)

    monkeypatch.setattr(graph_repository, "sync_topic_links", sync_or_boom)
    conn = get_connection()

    result = json.loads(
        backfill_links(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["scanned"] == 2
    assert result["failed"] == 1
    assert result["edges_created"] == 2
    assert conn.info.transaction_status == TransactionStatus.IDLE
    assert conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    conn.rollback()


def test_backfill_links_is_project_scoped(registered_workspace, project_name):
    other_project = f"{project_name}-other"
    ws_id = registered_workspace["workspace"]["id"]
    proj_a = repository.get_or_create_project(project_name, workspace_id=ws_id)
    proj_b = repository.get_or_create_project(other_project, workspace_id=ws_id)
    repository.save_observation(
        project_id=proj_a["id"],
        title="A",
        content="[[test-links/a-target]]",
        type="discovery",
        topic_key="test-links/a-source",
    )
    repository.save_observation(
        project_id=proj_b["id"],
        title="B",
        content="[[test-links/b-target]]",
        type="discovery",
        topic_key="test-links/b-source",
    )

    result = json.loads(
        backfill_links(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["scanned"] == 1
    assert result["project"] == project_name
    links_a = graph_repository.get_topic_links_out(ws_id, "test-links/a-source")
    links_b = graph_repository.get_topic_links_out(ws_id, "test-links/b-source")
    assert links_a == [{"name": "test-links/a-target"}]
    assert links_b == []
