import uuid

import pytest
from psycopg.errors import UniqueViolation

from memodi.database import auth_repository
from memodi.database.connection import ensure_schema, get_connection
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def email():
    return f"test-{uuid.uuid4()}@example.com"


@pytest.fixture(autouse=True)
def cleanup(email):
    yield
    cleanup_rows("DELETE FROM users WHERE email = %s", (email,))


def test_create_user_returns_key_once_and_only_hash_persisted(email):
    result = auth_repository.create_user(email)

    assert result["email"] == email
    assert result["api_key"].startswith("mmd_")
    assert "id" in result
    assert "api_key_hash" not in result

    conn = get_connection()
    row = conn.execute(
        "SELECT api_key_hash FROM users WHERE email = %s", (email,)
    ).fetchone()
    assert row is not None
    assert row["api_key_hash"] != result["api_key"]
    assert len(row["api_key_hash"]) == 64


def test_get_user_by_api_key_round_trips(email):
    created = auth_repository.create_user(email)

    found = auth_repository.get_user_by_api_key(created["api_key"])

    assert found is not None
    assert found["id"] == created["id"]
    assert found["email"] == email
    assert "api_key_hash" not in found


def test_get_user_by_api_key_unknown_returns_none():
    assert auth_repository.get_user_by_api_key("mmd_does-not-exist") is None


def test_create_user_duplicate_email_raises(email):
    auth_repository.create_user(email)

    with pytest.raises(ValueError):
        auth_repository.create_user(email)


def test_create_user_duplicate_api_key_hash_reraises(monkeypatch, email):
    monkeypatch.setattr(
        auth_repository, "_generate_api_key", lambda: "mmd_fixed-collision-key"
    )
    second_email = f"test-{uuid.uuid4()}@example.com"

    auth_repository.create_user(email)
    try:
        with pytest.raises(UniqueViolation):
            auth_repository.create_user(second_email)
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (second_email,))
