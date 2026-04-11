import json
import uuid

import pytest

from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import backfill_embeddings, save, search_hybrid, search_similar


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-vector-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(project_name):
    yield
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        "DELETE FROM observations WHERE project_id IN "
        "(SELECT id FROM projects WHERE name = %s)",
        (project_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.commit()


def test_save_generates_embedding(project_name):
    result = json.loads(
        save(
            project=project_name,
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


def test_search_similar_finds_by_meaning(project_name):
    save(
        project=project_name,
        title="Auth decision",
        content="We use JWT tokens for stateless authentication",
        type="decision",
    )
    save(
        project=project_name,
        title="DB indexing",
        content="Added B-tree index on users table for performance",
        type="config",
    )

    results = json.loads(
        search_similar(project=project_name, query="JWT authentication tokens")
    )
    assert len(results) >= 1
    # Auth decision should rank higher than DB indexing
    assert results[0]["title"] == "Auth decision"


def test_search_hybrid_combines_both(project_name):
    save(
        project=project_name,
        title="JWT authentication",
        content="Chose JWT for API auth",
        type="decision",
    )
    save(
        project=project_name,
        title="Session management",
        content="Redis for session storage",
        type="architecture",
    )

    results = json.loads(search_hybrid(project=project_name, query="authentication"))
    assert len(results) >= 1
    titles = [r["title"] for r in results]
    assert "JWT authentication" in titles


def test_backfill_embeddings(project_name):
    # Save without embedding by inserting directly
    from memodi.database import repository

    proj = repository.get_or_create_project(project_name)
    conn = get_connection()
    conn.execute(
        "INSERT INTO observations (project_id, type, title, content)"
        " VALUES (%s, %s, %s, %s)",
        (proj["id"], "decision", "Test obs", "Some content without embedding"),
    )
    conn.commit()

    result = json.loads(backfill_embeddings(project=project_name))
    assert result["backfilled"] == 1

    # Verify embedding was generated
    row = conn.execute(
        "SELECT embedding FROM observations"
        " WHERE project_id = %s AND title = 'Test obs'",
        (proj["id"],),
    ).fetchone()
    assert row["embedding"] is not None
