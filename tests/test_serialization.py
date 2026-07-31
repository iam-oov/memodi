import json
import uuid

import pytest

from memodi.database.connection import ensure_schema
from memodi.tools.memory import (
    context,
    get_observation,
    save,
    search,
    search_global,
    search_hybrid,
    search_similar,
)
from memodi.tools.serialization import (
    _OBSERVATION_READ_FIELDS,
    _RELATED_FIELDS,
    serialize_observation,
    serialize_related,
)
from memodi.tools.session import session_end, session_start
from tests.conftest import _path

FORBIDDEN_KEYS = {
    "embedding",
    "search_vector",
    "content_hash",
    "deleted_at",
    "session_id",
    "project_id",
}

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

OBSERVATION_READ_ALLOWLIST = {
    "id",
    "type",
    "title",
    "content",
    "topic_key",
    "metadata",
    "occurred_at",
    "created_at",
    "updated_at",
    "revision_count",
    "duplicate_count",
    "project_name",
    "project",
    "rank",
    "similarity",
    "rrf_score",
    "_deduplicated",
    "superseded_by",
    "supersedes",
}

_NON_PERSISTED_READ_FIELDS = {
    "project_name",
    "project",
    "rank",
    "similarity",
    "rrf_score",
    "_deduplicated",
    "supersedes",
}


_CONDITIONALLY_EXPOSED_READ_FIELDS = {"superseded_by", "supersedes"}

OBSERVATION_ROW_FIELDS = (
    OBSERVATION_READ_ALLOWLIST
    - _NON_PERSISTED_READ_FIELDS
    - _CONDITIONALLY_EXPOSED_READ_FIELDS
)


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-serialization-{uuid.uuid4()}"


def _collect_keys(node) -> set:
    keys: set = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            keys |= _collect_keys(v)
    elif isinstance(node, list):
        for item in node:
            keys |= _collect_keys(item)
    return keys


@pytest.mark.parametrize(
    "tool_name",
    ["save", "search", "context", "search_hybrid", "search_similar", "search_global"],
)
def test_no_forbidden_keys_leak(registered_workspace, project_name, tool_name):
    path = _path(registered_workspace, project_name)
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Leak probe",
        content="Checking no internals leak through the wire",
        type="discovery",
    )

    common = dict(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )

    if tool_name == "save":
        raw = save(
            **common,
            title="Second leak probe",
            content="Another observation to exercise the save ack",
            type="discovery",
        )
    elif tool_name == "context":
        raw = context(**common)
    elif tool_name == "search_global":
        raw = search_global(user_id=registered_workspace["user_id"], query="leak")
    else:
        fn = {
            "search": search,
            "search_hybrid": search_hybrid,
            "search_similar": search_similar,
        }[tool_name]
        raw = fn(**common, query="leak")

    payload = json.loads(raw)
    leaked = _collect_keys(payload) & FORBIDDEN_KEYS
    assert not leaked, f"{tool_name} leaked forbidden keys: {leaked}"


def test_context_last_session_never_leaks_project_id(
    registered_workspace, project_name
):
    path = _path(registered_workspace, project_name)
    session_start(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    session_end(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        summary="Closed with a summary so get_latest_session_summary finds it",
    )

    payload = json.loads(
        context(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert payload["last_session"] is not None
    assert payload["last_session"]["summary"].startswith("Closed with a summary")
    assert payload["last_session"]["project"] == project_name
    leaked = _collect_keys(payload["last_session"]) & FORBIDDEN_KEYS
    assert not leaked


def test_save_ack_exact_field_set(registered_workspace, project_name):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Ack contract",
            content="Verifying exact save response shape",
            type="discovery",
        )
    )
    assert set(result.keys()) == SAVE_ACK_FIELDS
    assert "content" not in result
    assert "metadata" not in result


def test_save_ack_includes_metadata_only_when_non_empty(
    registered_workspace, project_name
):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Ack with metadata",
            content="Verifying metadata is included when provided",
            type="discovery",
            metadata={"source": "test"},
        )
    )
    assert set(result.keys()) == SAVE_ACK_FIELDS | {"metadata"}
    assert result["metadata"] == {"source": "test"}


def test_read_allowlist_is_pinned():
    assert _OBSERVATION_READ_FIELDS == OBSERVATION_READ_ALLOWLIST


def test_related_fields_allowlist_is_pinned():
    assert {"id", "title", "topic_key", "project", "similarity"} == _RELATED_FIELDS


def test_save_ack_includes_related_only_when_present(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Related ack probe",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )

    ack = json.loads(
        save(
            **common,
            title="Related ack probe, reworded",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )

    assert set(ack.keys()) == SAVE_ACK_FIELDS | {"related"}


def test_related_entries_never_carry_content_or_internals(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Leaked internals probe",
        content=(
            "The deploy pipeline fails when two GitHub Actions runs race "
            "for the same git ref lock"
        ),
        type="bugfix",
    )

    ack = json.loads(
        save(
            **common,
            title="Leaked internals probe, reworded",
            content=(
                "Concurrent deploys can race for the git ref lock causing "
                "pipeline failures"
            ),
            type="bugfix",
        )
    )

    assert ack.get("related"), "expected a related entry for this test to be meaningful"
    for entry in ack["related"]:
        assert set(entry.keys()) <= _RELATED_FIELDS
        assert {"id", "title", "project", "similarity"} <= set(entry.keys())
        leaked = _collect_keys(entry) & FORBIDDEN_KEYS
        assert not leaked


def test_serialize_related_rounds_similarity_to_three_decimals():
    rows = [
        {"id": "a", "title": "t", "topic_key": "k", "project": "p", "similarity": 0.8},
        {
            "id": "b",
            "title": "t",
            "topic_key": "k",
            "project": "p",
            "similarity": 0.8123456789,
        },
        {
            "id": "c",
            "title": "t",
            "topic_key": "k",
            "project": "p",
            "similarity": 0.6666666666,
        },
    ]
    assert [e["similarity"] for e in serialize_related(rows)] == [0.8, 0.812, 0.667]


def test_serialize_related_omits_topic_key_when_null():
    rows = [
        {"id": "a", "title": "t", "topic_key": None, "project": "p", "similarity": 0.8},
        {"id": "b", "title": "t", "topic_key": "k", "project": "p", "similarity": 0.8},
    ]
    entries = serialize_related(rows)
    assert "topic_key" not in entries[0]
    assert entries[1]["topic_key"] == "k"


def test_serialize_observation_hides_superseded_by_when_null():
    obs = {"id": "abc", "title": "t", "type": "discovery", "superseded_by": None}
    result = serialize_observation(obs)
    assert "superseded_by" not in result


def test_serialize_observation_exposes_superseded_by_when_set():
    obs = {"id": "abc", "title": "t", "type": "discovery", "superseded_by": "new-id"}
    result = serialize_observation(obs)
    assert result["superseded_by"] == "new-id"


def test_serialize_observation_hides_supersedes_when_absent():
    obs = {"id": "abc", "title": "t", "type": "discovery"}
    result = serialize_observation(obs)
    assert "supersedes" not in result


def test_serialize_observation_hides_supersedes_when_empty():
    obs = {"id": "abc", "title": "t", "type": "discovery", "supersedes": []}
    result = serialize_observation(obs)
    assert "supersedes" not in result


def test_serialize_observation_exposes_supersedes_when_set():
    obs = {
        "id": "abc",
        "title": "t",
        "type": "discovery",
        "supersedes": ["old-id", "older-id"],
    }
    result = serialize_observation(obs)
    assert result["supersedes"] == ["old-id", "older-id"]


def test_search_hybrid_result_exact_field_set(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Hybrid boundary probe",
        content="Observation exercised through the hybrid search boundary",
        type="discovery",
    )

    rows = json.loads(search_hybrid(**common, query="hybrid boundary"))
    assert rows, "expected at least one hybrid result"
    row = rows[0]

    assert set(row.keys()) == OBSERVATION_ROW_FIELDS | {"rrf_score"}
    assert row["content"]
    assert row["title"]


def test_get_observation_exact_field_set(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Audit boundary probe",
            content="Observation exercised through the by-id read boundary",
            type="discovery",
        )
    )["id"]

    row = json.loads(get_observation(**common, observation_id=obs_id))

    assert set(row.keys()) == OBSERVATION_ROW_FIELDS
    leaked = _collect_keys(row) & FORBIDDEN_KEYS
    assert not leaked


def test_get_observation_successor_exact_field_set(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Audit boundary predecessor",
            content="Replaced so its successor carries the reverse pointer",
            type="discovery",
        )
    )["id"]
    new_id = json.loads(
        save(
            **common,
            title="Audit boundary successor",
            content="Exercised through the by-id read boundary",
            type="discovery",
            supersedes=old_id,
        )
    )["id"]

    row = json.loads(get_observation(**common, observation_id=new_id))

    assert set(row.keys()) == OBSERVATION_ROW_FIELDS | {"supersedes"}
    leaked = _collect_keys(row) & FORBIDDEN_KEYS
    assert not leaked


def test_successor_carries_no_supersedes_through_context_and_search(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Surfacing boundary predecessor",
            content="Pelican rollout happens on Sunday",
            type="discovery",
        )
    )["id"]
    save(
        **common,
        title="Surfacing boundary successor",
        content="Pelican rollout happens on Monday",
        type="discovery",
        supersedes=old_id,
    )

    payload = json.loads(context(**common))
    surfaced = [
        row
        for row in payload["observations"]
        if row["title"] == "Surfacing boundary successor"
    ]
    assert surfaced, "expected the successor in context"
    assert "supersedes" not in surfaced[0]

    for search_fn in (search, search_hybrid, search_similar):
        rows = json.loads(search_fn(**common, query="pelican rollout"))
        hits = [
            row for row in rows if row["title"] == "Surfacing boundary successor"
        ]
        assert hits, f"expected the successor in {search_fn.__name__}"
        assert "supersedes" not in hits[0], search_fn.__name__


def test_context_observation_exact_field_set(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Context boundary probe",
        content="Observation exercised through the context read boundary",
        type="discovery",
    )

    payload = json.loads(context(**common))
    assert payload["observations"], "expected at least one recent observation"
    row = payload["observations"][0]

    assert set(row.keys()) == OBSERVATION_ROW_FIELDS | {"project"}
    assert row["content"]
    assert row["title"]
