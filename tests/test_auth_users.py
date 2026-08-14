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
        "SELECT key_hash FROM api_keys WHERE user_id = %s", (result["id"],)
    ).fetchone()
    assert row is not None
    assert row["key_hash"] != result["api_key"]
    assert len(row["key_hash"]) == 64


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

        conn = get_connection()
        orphan = conn.execute(
            "SELECT id FROM users WHERE email = %s", (second_email,)
        ).fetchone()
        assert orphan is None
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (second_email,))


def test_create_user_normalizes_email_case_and_whitespace(email):
    result = auth_repository.create_user(f"  {email.upper()}  ")

    assert result["email"] == email

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE email = %s", (email,)
    ).fetchone()["c"]
    assert count == 1


def test_create_api_key_mints_an_additional_key_for_an_existing_user(email):
    created = auth_repository.create_user(email)

    second_key = auth_repository.create_api_key(created["id"])

    assert second_key.startswith("mmd_")
    assert second_key != created["api_key"]
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (created["id"],)
    ).fetchone()["c"]
    assert count == 2


def test_login_with_email_creates_a_new_user_on_first_call(email):
    result = auth_repository.login_with_email(email)

    assert result["email"] == email
    assert result["api_key"].startswith("mmd_")

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE email = %s", (email,)
    ).fetchone()["c"]
    assert count == 1


def test_login_with_email_second_call_reuses_the_user_and_mints_a_second_key(email):
    first = auth_repository.login_with_email(email)
    second = auth_repository.login_with_email(email)

    assert first["id"] == second["id"]
    assert first["api_key"] != second["api_key"]

    conn = get_connection()
    user_count = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE email = %s", (email,)
    ).fetchone()["c"]
    key_count = conn.execute(
        "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (first["id"],)
    ).fetchone()["c"]
    assert user_count == 1
    assert key_count == 2

    assert auth_repository.get_user_by_api_key(first["api_key"])["id"] == first["id"]
    assert auth_repository.get_user_by_api_key(second["api_key"])["id"] == first["id"]


def test_login_with_email_normalizes_case(email):
    first = auth_repository.login_with_email(email.upper())
    second = auth_repository.login_with_email(email)

    assert first["id"] == second["id"]
    assert first["email"] == email


def test_revoke_api_key_removes_only_the_targeted_key(email):
    created = auth_repository.create_user(email)
    second_key = auth_repository.create_api_key(created["id"])

    revoked = auth_repository.revoke_api_key(created["api_key"])

    assert revoked is True
    assert auth_repository.get_user_by_api_key(created["api_key"]) is None
    assert auth_repository.get_user_by_api_key(second_key)["id"] == created["id"]

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (created["id"],)
    ).fetchone()["c"]
    assert count == 1


def test_revoke_api_key_unknown_key_returns_false():
    assert auth_repository.revoke_api_key("mmd_does-not-exist") is False


def test_revoke_api_key_twice_is_idempotent(email):
    created = auth_repository.create_user(email)

    first = auth_repository.revoke_api_key(created["api_key"])
    second = auth_repository.revoke_api_key(created["api_key"])

    assert first is True
    assert second is False
