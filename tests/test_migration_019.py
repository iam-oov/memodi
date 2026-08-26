"""The 019 fold is the risky half of case normalization: it rewrites rows that
already exist in production. These run the migration SQL against deliberately
cased rows, since the suite's own database is already folded."""

import uuid
from pathlib import Path

import pytest

from memodi.database import auth_repository, repository
from memodi.database.connection import ensure_schema, get_connection
from tests.conftest import cleanup_rows

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "memodi"
    / "migrations"
    / "019_lowercase_names.sql"
)


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def owner():
    user = auth_repository.create_user(f"test-019-{uuid.uuid4()}@example.com")
    yield user
    cleanup_rows("DELETE FROM api_keys WHERE user_id = %s", (user["id"],))
    cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))


def _run_migration() -> None:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(MIGRATION.read_text())
    conn.commit()


def _insert_workspace(name: str, owner_id: str) -> str:
    """Straight INSERT — normalize_name() would defeat the point."""
    conn = get_connection()
    row = conn.execute(
        "INSERT INTO workspaces (name, owner_user_id) VALUES (%s, %s) RETURNING id",
        (name, owner_id),
    ).fetchone()
    conn.commit()
    return str(row["id"])


def _insert_project(name: str, workspace_id: str) -> str:
    conn = get_connection()
    row = conn.execute(
        "INSERT INTO projects (name, workspace_id) VALUES (%s, %s) RETURNING id",
        (name, workspace_id),
    ).fetchone()
    conn.commit()
    return str(row["id"])


def _name_of(table: str, row_id: str) -> str:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    return conn.execute(
        f"SELECT name FROM {table} WHERE id = %s", (row_id,)
    ).fetchone()["name"]


def _drop_workspace(ws_id: str) -> None:
    cleanup_rows(
        "DELETE FROM observations WHERE project_id IN "
        "(SELECT id FROM projects WHERE workspace_id = %s)",
        (ws_id,),
    )
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws_id,))


def test_folds_cased_workspace_and_project_names(owner):
    ws_id = _insert_workspace(f"Tiriel-{uuid.uuid4()}", owner["id"])
    try:
        proj_id = _insert_project(f"TirielInc-{uuid.uuid4()}", ws_id)
        _run_migration()
        assert _name_of("workspaces", ws_id) == _name_of("workspaces", ws_id).lower()
        assert _name_of("projects", proj_id) == _name_of("projects", proj_id).lower()
    finally:
        _drop_workspace(ws_id)


def test_a_folded_name_is_findable_by_the_normalizing_lookup(owner):
    """The whole point: after the fold, get_or_create_project must FIND the
    existing row rather than create a second one beside it."""
    suffix = uuid.uuid4()
    ws_id = _insert_workspace(f"Tiriel-{suffix}", owner["id"])
    try:
        proj_id = _insert_project(f"TirielInc-{suffix}", ws_id)
        _run_migration()

        found = repository.get_or_create_project(f"TirielInc-{suffix}", ws_id)
        assert str(found["id"]) == proj_id

        also = repository.get_or_create_project(f"tirielinc-{suffix}", ws_id)
        assert str(also["id"]) == proj_id
    finally:
        _drop_workspace(ws_id)


def test_leaves_colliding_projects_alone(owner):
    """Two projects differing only in case are a pre-existing duplicate. Folding
    one onto the other would move observations nobody reviewed, and would break
    the (name, workspace_id) unique constraint on the way."""
    suffix = uuid.uuid4()
    ws_id = _insert_workspace(f"collide-ws-{suffix}", owner["id"])
    try:
        cased = _insert_project(f"Repo-{suffix}", ws_id)
        lower = _insert_project(f"repo-{suffix}", ws_id)

        _run_migration()

        assert _name_of("projects", cased) == f"Repo-{suffix}"
        assert _name_of("projects", lower) == f"repo-{suffix}"
    finally:
        _drop_workspace(ws_id)


def test_folds_affects_names_inside_observation_metadata(owner):
    """affects holds project NAMES matched with `?` against folded values —
    leaving them cased drops every cross-repo observation out of scope."""
    suffix = uuid.uuid4()
    ws_id = _insert_workspace(f"affects-ws-{suffix}", owner["id"])
    try:
        proj_id = _insert_project(f"home-{suffix}", ws_id)
        conn = get_connection()
        row = conn.execute(
            """
            INSERT INTO observations (project_id, type, title, content, metadata)
            VALUES (%s, 'decision', 'Cased affects', 'body', %s::jsonb)
            RETURNING id
            """,
            (proj_id, '{"affects": ["  Repo-One  ", "repo-one", "Other-Repo"]}'),
        ).fetchone()
        conn.commit()

        _run_migration()

        if conn.info.transaction_status != 0:
            conn.rollback()
        stored = conn.execute(
            "SELECT metadata FROM observations WHERE id = %s", (row["id"],)
        ).fetchone()["metadata"]
        assert sorted(stored["affects"]) == ["other-repo", "repo-one"]
    finally:
        _drop_workspace(ws_id)


def test_is_idempotent(owner):
    suffix = uuid.uuid4()
    ws_id = _insert_workspace(f"Tiriel-{suffix}", owner["id"])
    try:
        proj_id = _insert_project(f"TirielInc-{suffix}", ws_id)
        _run_migration()
        first = _name_of("projects", proj_id)
        _run_migration()
        assert _name_of("projects", proj_id) == first
    finally:
        _drop_workspace(ws_id)
