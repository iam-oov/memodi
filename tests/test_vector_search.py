import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import backfill_embeddings, save, search_hybrid, search_similar
from tests.conftest import _path


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-vector-{uuid.uuid4()}"


def test_save_generates_embedding(registered_workspace, project_name):
    result = json.loads(
        save(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Auth decision",
            content="JWT for stateless auth",
            type="decision",
        )
    )
    conn = get_connection()
    row = conn.execute(
        "SELECT embedding FROM observations WHERE id = %s", (result["id"],)
    ).fetchone()
    assert row["embedding"] is not None


def test_search_similar_finds_by_meaning(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Auth decision",
        content="We use JWT tokens for stateless authentication",
        type="decision",
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="DB indexing",
        content="Added B-tree index on users table for performance",
        type="config",
    )

    results = json.loads(
        search_similar(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="JWT authentication tokens",
        )
    )
    assert len(results) >= 1
    # Auth decision should rank higher than DB indexing
    assert results[0]["title"] == "Auth decision"


def test_search_hybrid_combines_both(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="JWT authentication",
        content="Chose JWT for API auth",
        type="decision",
    )
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Session management",
        content="Redis for session storage",
        type="architecture",
    )

    results = json.loads(
        search_hybrid(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="authentication",
        )
    )
    assert len(results) >= 1
    titles = [r["title"] for r in results]
    assert "JWT authentication" in titles


def test_search_hybrid_repo_filters_by_workspace(registered_workspace, project_name):
    from memodi.database import repository
    from memodi.embeddings import generate_embedding

    path = _path(registered_workspace, project_name)
    save(
        path=path,
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Workspace-scoped hybrid",
        content="JWT authentication decision",
        type="decision",
    )
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    embedding = generate_embedding("authentication")

    same_ws = repository.search_hybrid(
        project_id=proj["id"],
        query="authentication",
        embedding=embedding,
        workspace_id=registered_workspace["workspace"]["id"],
    )
    assert any(r["title"] == "Workspace-scoped hybrid" for r in same_ws)

    mismatched = repository.search_hybrid(
        project_id=proj["id"],
        query="authentication",
        embedding=embedding,
        workspace_id=str(uuid.uuid4()),
    )
    assert mismatched == []


def test_backfill_embeddings(registered_workspace, project_name):
    # Save without embedding by inserting directly
    from memodi.database import repository

    path = _path(registered_workspace, project_name)
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    conn = get_connection()
    conn.execute(
        "INSERT INTO observations (project_id, type, title, content)"
        " VALUES (%s, %s, %s, %s)",
        (proj["id"], "decision", "Test obs", "Some content without embedding"),
    )
    conn.commit()

    result = json.loads(
        backfill_embeddings(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["backfilled"] == 1

    # Verify embedding was generated
    row = conn.execute(
        "SELECT embedding FROM observations"
        " WHERE project_id = %s AND title = 'Test obs'",
        (proj["id"],),
    ).fetchone()
    assert row["embedding"] is not None
