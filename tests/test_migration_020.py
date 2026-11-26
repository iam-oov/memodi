import uuid
from pathlib import Path

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "memodi"
    / "migrations"
    / "020_observation_horizon.sql"
)

EXPECTED = {
    "preference": "long",
    "pattern": "long",
    "decision": "medium",
    "architecture": "medium",
    "discovery": "short",
    "config": "short",
    "bugfix": "short",
    "session": "short",
}


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


def _execute(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall() if cursor.description else []
    conn.commit()
    return [dict(r) for r in rows]


def _seed_one_per_type(registered_workspace) -> dict[str, str]:
    project = repository.get_or_create_project(
        f"test-020-{uuid.uuid4()}", registered_workspace["workspace"]["id"]
    )
    return {
        type_: str(
            repository.save_observation(
                project_id=project["id"],
                title=f"Legacy {type_}",
                content=f"Saved before horizons existed {uuid.uuid4()}",
                type=type_,
            )["id"]
        )
        for type_ in EXPECTED
    }


def _simulate_pre_migration(ids: list[str]) -> None:
    # Production rows had no horizon column at all; dropping NOT NULL and
    # blanking the column is the closest the suite's migrated DB can get.
    _execute("ALTER TABLE observations ALTER COLUMN horizon DROP NOT NULL")
    _execute(
        "UPDATE observations SET horizon = NULL WHERE id = ANY(%s::uuid[])", (ids,)
    )


def _horizons(ids: list[str]) -> dict[str, str]:
    rows = _execute(
        "SELECT id, horizon FROM observations WHERE id = ANY(%s::uuid[])", (ids,)
    )
    return {str(r["id"]): r["horizon"] for r in rows}


def test_backfills_horizon_from_type(registered_workspace):
    ids = _seed_one_per_type(registered_workspace)
    _simulate_pre_migration(list(ids.values()))

    _execute(MIGRATION.read_text())

    horizons = _horizons(list(ids.values()))
    assert {t: horizons[i] for t, i in ids.items()} == EXPECTED


def test_restores_not_null_and_is_idempotent(registered_workspace):
    ids = _seed_one_per_type(registered_workspace)
    _simulate_pre_migration(list(ids.values()))

    _execute(MIGRATION.read_text())
    _execute(MIGRATION.read_text())

    column = _execute(
        """
        SELECT is_nullable, column_default FROM information_schema.columns
        WHERE table_name = 'observations' AND column_name = 'horizon'
        """
    )[0]
    assert column["is_nullable"] == "NO"
    assert "medium" in column["column_default"]
    assert set(_horizons(list(ids.values())).values()) <= {"long", "medium", "short"}


def test_does_not_overwrite_an_explicit_horizon(registered_workspace):
    project = repository.get_or_create_project(
        f"test-020-{uuid.uuid4()}", registered_workspace["workspace"]["id"]
    )
    obs = repository.save_observation(
        project_id=project["id"],
        title="Pending work",
        content=f"Recorded as todo {uuid.uuid4()}",
        type="decision",
        horizon="todo",
    )

    _execute(MIGRATION.read_text())

    assert _horizons([str(obs["id"])])[str(obs["id"])] == "todo"
