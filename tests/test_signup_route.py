import uuid
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from memodi import server
from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.web import signup as signup_module
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def client():
    return TestClient(server.mcp.streamable_http_app())


@pytest.fixture
def signup_code(monkeypatch):
    code = f"invite-{uuid.uuid4()}"
    monkeypatch.setattr(settings, "signup_code", code)
    return code


@pytest.fixture
def email():
    addr = f"test-signup-{uuid.uuid4()}@example.com"
    yield addr
    cleanup_rows("DELETE FROM users WHERE email = %s", (addr,))


def test_get_signup_renders_form(client, signup_code):
    response = client.get("/signup")

    assert response.status_code == 200
    assert "<form" in response.text
    assert 'name="email"' in response.text
    assert 'name="invite_code"' in response.text


def test_get_signup_disabled_when_no_code(client, monkeypatch):
    monkeypatch.setattr(settings, "signup_code", None)

    response = client.get("/signup")

    assert response.status_code == 503
    assert "disabled" in response.text.lower()


def test_post_signup_creates_user_and_shows_key_once(client, signup_code, email):
    response = client.post("/signup", data={"email": email, "invite_code": signup_code})

    assert response.status_code == 200
    assert response.text.count("mmd_") == 1
    assert "api_key_hash" not in response.text

    conn = get_connection()
    row = conn.execute(
        "SELECT api_key_hash FROM users WHERE email = %s", (email,)
    ).fetchone()
    assert row is not None
    assert "mmd_" not in row["api_key_hash"]
    assert len(row["api_key_hash"]) == 64


def test_post_signup_wrong_code_rejected_and_no_user_created(
    client, signup_code, email
):
    response = client.post(
        "/signup", data={"email": email, "invite_code": "wrong-code"}
    )

    assert response.status_code == 403

    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    assert row is None


def test_post_signup_duplicate_email_returns_409(client, signup_code, email):
    client.post("/signup", data={"email": email, "invite_code": signup_code})

    response = client.post("/signup", data={"email": email, "invite_code": signup_code})

    assert response.status_code == 409


def test_post_signup_invalid_email_returns_400(client, signup_code):
    response = client.post(
        "/signup", data={"email": "not-an-email", "invite_code": signup_code}
    )

    assert response.status_code == 400


def test_post_signup_missing_email_returns_400(client, signup_code):
    response = client.post("/signup", data={"invite_code": signup_code})

    assert response.status_code == 400


def test_post_signup_disabled_when_no_code(client, monkeypatch, email):
    monkeypatch.setattr(settings, "signup_code", None)

    response = client.post("/signup", data={"email": email, "invite_code": "anything"})

    assert response.status_code == 503


def test_post_signup_ensures_schema_before_create_user(
    client, signup_code, email, monkeypatch
):
    manager = Mock()
    manager.attach_mock(Mock(wraps=ensure_schema), "ensure_schema")
    manager.attach_mock(Mock(wraps=auth_repository.create_user), "create_user")
    monkeypatch.setattr(
        signup_module, "ensure_schema", manager.ensure_schema, raising=False
    )
    monkeypatch.setattr(
        signup_module.auth_repository, "create_user", manager.create_user
    )

    response = client.post(
        "/signup", data={"email": email, "invite_code": signup_code}
    )

    assert response.status_code == 200
    call_names = [c[0] for c in manager.mock_calls]
    assert "ensure_schema" in call_names
    assert call_names.index("ensure_schema") < call_names.index("create_user")


def test_post_signup_non_ascii_code_rejected_not_crash(client, signup_code, email):
    response = client.post(
        "/signup", data={"email": email, "invite_code": "héllo"}
    )

    assert response.status_code == 403

    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    assert row is None


def test_post_signup_missing_code_rejected_not_crash(client, signup_code, email):
    response = client.post("/signup", data={"email": email})

    assert response.status_code == 403

    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    assert row is None


def test_post_signup_oversized_body_rejected_before_create_user(client, signup_code):
    big_email = "big-" + ("x" * 20000) + "@example.com"
    try:
        response = client.post(
            "/signup", data={"email": big_email, "invite_code": signup_code}
        )

        assert response.status_code == 413

        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM users WHERE email = %s", (big_email,)
        ).fetchone()
        assert row is None
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (big_email,))


def test_post_signup_escapes_email_on_success_page(client, signup_code):
    xss_email = "<script>alert(1)</script>@x.com"
    try:
        response = client.post(
            "/signup", data={"email": xss_email, "invite_code": signup_code}
        )

        assert response.status_code == 200
        assert "&lt;script&gt;" in response.text
        assert "<script>alert(1)</script>" not in response.text
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (xss_email,))


def test_post_signup_escapes_email_on_duplicate_page(client, signup_code):
    xss_email = "<script>alert(2)</script>@x.com"
    try:
        client.post(
            "/signup", data={"email": xss_email, "invite_code": signup_code}
        )

        response = client.post(
            "/signup", data={"email": xss_email, "invite_code": signup_code}
        )

        assert response.status_code == 409
        assert "&lt;script&gt;" in response.text
        assert "<script>alert(2)</script>" not in response.text
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (xss_email,))
