import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import delete, find_consolidation_clusters, save
from tests.conftest import _path, cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-consolidation-clusters-{uuid.uuid4()}"


def _backdate(observation_id: str, days: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE observations SET created_at = now() - make_interval(days => %s)"
        " WHERE id = %s",
        (days, observation_id),
    )
    conn.commit()


def _extra_workspace(user_id: str) -> dict:
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


def _cleanup_workspace(workspace: dict) -> None:
    ws_id = workspace["id"]
    cleanup_rows(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    )
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws_id,))


def _save_auth_cluster(registered_workspace, project_name, days=40):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    texts = [
        "We use JWT tokens for stateless authentication across all services",
        "We use JWT tokens for stateless authentication across every service",
        "We use JWT tokens for stateless authentication in all our services",
    ]
    ids = []
    for i, text in enumerate(texts):
        obs_id = json.loads(
            save(**common, title=f"Auth note {i}", content=text, type="decision")
        )["id"]
        if days is not None:
            _backdate(obs_id, days)
        ids.append(obs_id)
    return ids


def _save_indexing_cluster(registered_workspace, project_name, days=40):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    texts = [
        "Added a B-tree index on the users table to speed up lookups",
        "Added a B-tree index on the users table to speed up queries",
        "Added a B-tree index on the users table for faster lookups",
    ]
    ids = []
    for i, text in enumerate(texts):
        obs_id = json.loads(
            save(**common, title=f"Indexing note {i}", content=text, type="config")
        )["id"]
        if days is not None:
            _backdate(obs_id, days)
        ids.append(obs_id)
    return ids


def test_finds_a_cluster_of_near_duplicates(registered_workspace, project_name):
    auth_ids = _save_auth_cluster(registered_workspace, project_name)
    outlier_id = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Deploy pipeline note",
            content="Migrated the deploy pipeline from CircleCI to GitHub Actions",
            type="decision",
        )
    )["id"]
    _backdate(outlier_id, 40)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert len(clusters) == 1
    cluster = clusters[0]
    member_ids = {str(m["id"]) for m in cluster["members"]}
    assert member_ids == set(auth_ids)
    assert outlier_id not in member_ids
    assert cluster["member_count"] == 3


def test_respects_min_cluster_size(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    texts = [
        "We use JWT tokens for stateless authentication across all services",
        "We use JWT tokens for stateless authentication across every service",
    ]
    for i, text in enumerate(texts):
        obs_id = json.loads(
            save(**common, title=f"Auth pair note {i}", content=text, type="decision")
        )["id"]
        _backdate(obs_id, 40)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert clusters == []


def test_respects_min_age_days(registered_workspace, project_name):
    auth_ids = _save_auth_cluster(registered_workspace, project_name, days=None)

    fresh_clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )
    assert fresh_clusters == []

    for obs_id in auth_ids:
        _backdate(obs_id, 40)

    aged_clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )
    assert len(aged_clusters) == 1
    assert {str(m["id"]) for m in aged_clusters[0]["members"]} == set(auth_ids)


def test_excludes_deleted_and_superseded(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    texts = [
        "We use JWT tokens for stateless authentication across all services",
        "We use JWT tokens for stateless authentication across every service",
        "We use JWT tokens for stateless authentication in all our services",
        "We use JWT tokens for stateless authentication throughout our services",
        "We use JWT tokens for stateless authentication in every one of our services",
    ]
    ids = []
    for i, text in enumerate(texts):
        obs_id = json.loads(
            save(**common, title=f"Auth note {i}", content=text, type="decision")
        )["id"]
        _backdate(obs_id, 40)
        ids.append(obs_id)

    deleted_id = ids[0]
    delete(**common, observation_id=deleted_id)

    superseded_id = ids[1]
    replacement_id = json.loads(
        save(
            **common,
            title="Auth note replacement",
            content="We use JWT tokens for stateless authentication, updated wording",
            type="decision",
            supersedes=superseded_id,
        )
    )["id"]
    _backdate(replacement_id, 40)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert len(clusters) == 1
    member_ids = {str(m["id"]) for m in clusters[0]["members"]}
    assert deleted_id not in member_ids
    assert superseded_id not in member_ids


def test_no_cross_workspace_leak(registered_workspace, project_name):
    other = _extra_workspace(registered_workspace["user_id"])
    try:
        common = dict(
            user_id=other["user_id"],
            machine=other["machine"],
        )
        texts = [
            "We use JWT tokens for stateless authentication across all services",
            "We use JWT tokens for stateless authentication across every service",
            "We use JWT tokens for stateless authentication in all our services",
        ]
        for i, text in enumerate(texts):
            obs_id = json.loads(
                save(
                    path=f"{other['root']}/{project_name}",
                    **common,
                    title=f"Other workspace auth note {i}",
                    content=text,
                    type="decision",
                )
            )["id"]
            _backdate(obs_id, 40)

        clusters = repository.find_consolidation_clusters(
            registered_workspace["workspace"]["id"],
            min_age_days=30,
            min_cluster_size=3,
            similarity_threshold=0.75,
        )
        assert clusters == []
    finally:
        _cleanup_workspace(other["workspace"])


def test_read_only_creates_no_project(registered_workspace):
    unregistered_path = f"/never/registered/{uuid.uuid4()}"
    before = repository.list_projects(owner_user_id=registered_workspace["user_id"])

    result = json.loads(
        find_consolidation_clusters(
            path=unregistered_path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["type"] == "not_started"
    after = repository.list_projects(owner_user_id=registered_workspace["user_id"])
    assert len(after) == len(before)


def test_read_only_registered_subpath_creates_no_project(registered_workspace):
    subpath = f"{registered_workspace['root']}/fresh-subdir-{uuid.uuid4()}"
    before = repository.list_projects(owner_user_id=registered_workspace["user_id"])

    result = json.loads(
        find_consolidation_clusters(
            path=subpath,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result == {"clusters": []}
    after = repository.list_projects(owner_user_id=registered_workspace["user_id"])
    assert len(after) == len(before)


def test_theme_narrows_to_matching_cluster(registered_workspace, project_name):
    auth_ids = _save_auth_cluster(registered_workspace, project_name)
    indexing_ids = _save_indexing_cluster(registered_workspace, project_name)

    auth_clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
        theme="authentication",
    )
    assert len(auth_clusters) == 1
    assert {str(m["id"]) for m in auth_clusters[0]["members"]} == set(auth_ids)

    indexing_clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
        theme="index",
    )
    assert len(indexing_clusters) == 1
    assert {str(m["id"]) for m in indexing_clusters[0]["members"]} == set(indexing_ids)

    all_clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )
    assert len(all_clusters) == 2


def test_theme_all_stopwords_returns_empty(registered_workspace, project_name):
    _save_auth_cluster(registered_workspace, project_name)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
        theme="the and for",
    )
    assert clusters == []


def test_cluster_carries_confidence_reason_and_size(registered_workspace, project_name):
    _save_auth_cluster(registered_workspace, project_name)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert len(clusters) == 1
    cluster = clusters[0]
    assert 0 <= cluster["confidence"] <= 1
    assert isinstance(cluster["reason"], list)
    assert cluster["reason"]
    assert cluster["member_count"] == 3
    assert cluster["total_chars"] > 0
    assert 0 <= cluster["estimated_gain"] <= 1


def test_recent_similar_activity_flag(registered_workspace, project_name):
    _save_auth_cluster(registered_workspace, project_name)

    fresh_id = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Auth note, brand new",
            content="We use JWT tokens for stateless authentication in our services",
            type="decision",
        )
    )["id"]

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert len(clusters) == 1
    assert "recent_similar_activity" in clusters[0]["reason"]
    member_ids = {str(m["id"]) for m in clusters[0]["members"]}
    assert fresh_id not in member_ids


def test_recent_similar_activity_absent_without_fresh_note(
    registered_workspace, project_name
):
    _save_auth_cluster(registered_workspace, project_name)

    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )

    assert len(clusters) == 1
    assert "recent_similar_activity" not in clusters[0]["reason"]


def test_recent_similar_activity_ignores_other_workspace(
    registered_workspace, project_name
):
    _save_auth_cluster(registered_workspace, project_name)

    other = _extra_workspace(registered_workspace["user_id"])
    try:
        save(
            path=f"{other['root']}/{project_name}",
            user_id=other["user_id"],
            machine=other["machine"],
            title="Other workspace fresh auth note",
            content="We use JWT tokens for stateless authentication in our services",
            type="decision",
        )

        clusters = repository.find_consolidation_clusters(
            registered_workspace["workspace"]["id"],
            min_age_days=30,
            min_cluster_size=3,
            similarity_threshold=0.75,
        )

        assert len(clusters) == 1
        assert "recent_similar_activity" not in clusters[0]["reason"]
    finally:
        _cleanup_workspace(other["workspace"])


def test_empty_when_nothing_qualifies(registered_workspace):
    clusters = repository.find_consolidation_clusters(
        registered_workspace["workspace"]["id"],
        min_age_days=30,
        min_cluster_size=3,
        similarity_threshold=0.75,
    )
    assert clusters == []


def test_find_consolidation_clusters_tool_returns_serialized_clusters(
    registered_workspace, project_name
):
    auth_ids = _save_auth_cluster(registered_workspace, project_name)

    result = json.loads(
        find_consolidation_clusters(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    member_ids = {m["id"] for m in cluster["members"]}
    assert member_ids == set(auth_ids)
    for member in cluster["members"]:
        assert set(member.keys()) <= {"id", "title", "topic_key", "type", "created_at"}
        assert "content" not in member
        assert "embedding" not in member
        assert "chars" not in member
