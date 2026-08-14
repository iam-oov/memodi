import base64
import json
import urllib.parse
import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from starlette.testclient import TestClient

from memodi import server
from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.web import login as login_module
from tests.conftest import cleanup_rows

GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
VALID_NONCE = "a" * 32
VALID_PORT = 51774


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def client():
    return TestClient(server.mcp.streamable_http_app())


@pytest.fixture
def google_settings(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", GOOGLE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(
        settings, "google_redirect_uri", "http://localhost:8787/oauth/callback"
    )


@pytest.fixture
def email():
    addr = f"test-login-{uuid.uuid4()}@example.com"
    yield addr
    cleanup_rows("DELETE FROM users WHERE email = %s", (addr,))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _fake_id_token(**claims) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.fakesignature"


def _valid_claims(email: str) -> dict:
    return {
        "aud": GOOGLE_CLIENT_ID,
        "iss": "https://accounts.google.com",
        "email": email,
        "email_verified": True,
    }


def _mock_exchange(monkeypatch, id_token: str | None):
    result = None if id_token is None else {"id_token": id_token}
    monkeypatch.setattr(
        login_module, "_exchange_code", AsyncMock(return_value=result)
    )


def _mock_token_endpoint(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def build_client(*args, **kwargs):
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(login_module.httpx, "AsyncClient", build_client)


def _complete_login(client, state: str):
    return client.get(
        "/oauth/callback", params={"state": state, "code": "test-code"}
    )


def _complete_login_no_follow(client, state: str, **extra_params):
    params = {"state": state, "code": "test-code", **extra_params}
    return client.get("/oauth/callback", params=params, follow_redirects=False)


def _login_with_loopback(client, port=VALID_PORT, nonce=VALID_NONCE):
    return client.get(
        "/login", params={"port": str(port), "nonce": nonce}, follow_redirects=False
    )


def _bare_state_from_login(login_response) -> str:
    location = login_response.headers["location"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return query["state"][0]


def _user_count() -> int:
    return get_connection().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def test_get_login_redirects_with_expected_params(client, google_settings):
    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)

    assert query["client_id"] == [GOOGLE_CLIENT_ID]
    assert query["redirect_uri"] == ["http://localhost:8787/oauth/callback"]
    assert query["scope"] == ["openid email"]
    assert "state" in query
    cookie_state = response.cookies.get(login_module.STATE_COOKIE)
    assert cookie_state == query["state"][0]


def test_get_login_cookie_is_httponly_and_lax(client, google_settings):
    response = client.get("/login", follow_redirects=False)

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_get_login_cookie_is_secure_when_redirect_uri_is_https(
    client, google_settings, monkeypatch
):
    monkeypatch.setattr(
        settings, "google_redirect_uri", "https://memodi.example.com/oauth/callback"
    )

    response = client.get("/login", follow_redirects=False)

    assert "secure" in response.headers["set-cookie"].lower()


def test_get_login_cookie_is_not_secure_when_redirect_uri_is_http(
    client, google_settings
):
    response = client.get("/login", follow_redirects=False)

    assert "secure" not in response.headers["set-cookie"].lower()


def test_signup_route_is_gone(client):
    response = client.get("/signup")

    assert response.status_code == 404


def test_get_login_disabled_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "google_redirect_uri", None)

    response = client.get("/login")

    assert response.status_code == 503
    assert "disabled" in response.text.lower()


def test_get_oauth_callback_disabled_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "google_redirect_uri", None)

    response = client.get(
        "/oauth/callback", params={"state": "x", "code": "y"}
    )

    assert response.status_code == 503


def test_callback_creates_user_and_shows_key_once(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)

    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login(client, state)

    assert response.status_code == 200
    assert response.text.count("mmd_") == 1
    assert "key_hash" not in response.text
    assert response.headers["cache-control"] == "no-store"

    conn = get_connection()
    row = conn.execute(
        """
        SELECT api_keys.key_hash
        FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        WHERE users.email = %s
        """,
        (email,),
    ).fetchone()
    assert row is not None
    assert len(row["key_hash"]) == 64


def test_second_login_same_email_yields_one_user_two_keys_both_resolve(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))
    first = _complete_login(client, state)
    first_key = _extract_key(first.text)

    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))
    second = _complete_login(client, state)
    second_key = _extract_key(second.text)

    assert first_key != second_key

    conn = get_connection()
    user_count = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE email = %s", (email,)
    ).fetchone()["c"]
    assert user_count == 1

    assert auth_repository.get_user_by_api_key(first_key) is not None
    assert auth_repository.get_user_by_api_key(second_key) is not None


def _extract_key(body: str) -> str:
    start = body.index('id="api-key">') + len('id="api-key">')
    end = body.index("</pre>", start)
    return body[start:end]


def test_callback_missing_state_returns_400_no_user_created(client, google_settings):
    before = _user_count()

    response = client.get("/oauth/callback", params={"code": "test-code"})

    assert response.status_code == 400
    assert _user_count() == before


def test_callback_state_mismatch_returns_400_no_user_created(client, google_settings):
    login_response = client.get("/login", follow_redirects=False)
    assert login_response.cookies.get(login_module.STATE_COOKIE)
    before = _user_count()

    response = client.get(
        "/oauth/callback", params={"state": "not-the-cookie-value", "code": "x"}
    )

    assert response.status_code == 400
    assert _user_count() == before


def test_callback_state_cookie_without_query_state_returns_400(
    client, google_settings
):
    login_response = client.get("/login", follow_redirects=False)
    assert login_response.cookies.get(login_module.STATE_COOKIE)
    before = _user_count()

    response = client.get("/oauth/callback", params={"code": "test-code"})

    assert response.status_code == 400
    assert _user_count() == before


def test_callback_non_ascii_state_returns_400(client, google_settings):
    login_response = client.get("/login", follow_redirects=False)
    assert login_response.cookies.get(login_module.STATE_COOKIE)

    response = client.get(
        "/oauth/callback", params={"state": "café", "code": "test-code"}
    )

    assert response.status_code == 400


def test_callback_missing_code_returns_400(client, google_settings):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)

    response = client.get("/oauth/callback", params={"state": state})

    assert response.status_code == 400


def test_callback_provider_error_returns_400(client, google_settings):
    response = client.get(
        "/oauth/callback", params={"error": "access_denied"}
    )

    assert response.status_code == 400


def test_callback_exchange_non_200_returns_502_no_secret_leaked(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        return httpx.Response(400, json={"error": "invalid_grant"})

    _mock_token_endpoint(monkeypatch, handler)

    response = _complete_login(client, state)

    assert response.status_code == 502
    assert "test-client-secret" in sent[0]
    assert "test-client-secret" not in response.text


def test_callback_exchange_non_json_body_returns_502_no_secret_leaked(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_token_endpoint(
        monkeypatch,
        lambda request: httpx.Response(200, text="<html>gateway timeout</html>"),
    )

    response = _complete_login(client, state)

    assert response.status_code == 502
    assert "test-client-secret" not in response.text


def test_callback_exchange_transport_error_returns_502_no_secret_leaked(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)

    def handler(request):
        raise httpx.ConnectError("connection refused")

    _mock_token_endpoint(monkeypatch, handler)

    response = _complete_login(client, state)

    assert response.status_code == 502
    assert "test-client-secret" not in response.text


def test_callback_token_response_without_id_token_returns_502(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_token_endpoint(
        monkeypatch,
        lambda request: httpx.Response(200, json={"access_token": "ya29.token"}),
    )

    response = _complete_login(client, state)

    assert response.status_code == 502


def test_callback_wrong_audience_returns_403(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    claims = _valid_claims(email)
    claims["aud"] = "someone-elses-client-id"
    _mock_exchange(monkeypatch, _fake_id_token(**claims))

    response = _complete_login(client, state)

    assert response.status_code == 403


def test_callback_wrong_issuer_returns_403(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    claims = _valid_claims(email)
    claims["iss"] = "https://accounts.evil.example.com"
    _mock_exchange(monkeypatch, _fake_id_token(**claims))

    response = _complete_login(client, state)

    assert response.status_code == 403


def test_callback_id_token_without_three_segments_returns_400(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, "header.payload")

    response = _complete_login(client, state)

    assert response.status_code == 400


def test_callback_id_token_payload_not_decodable_base64_returns_400(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, "header.a.signature")

    response = _complete_login(client, state)

    assert response.status_code == 400


def test_callback_id_token_payload_not_an_object_returns_400(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(["not", "an", "object"]).encode())
    _mock_exchange(monkeypatch, f"{header}.{payload}.fakesignature")

    response = _complete_login(client, state)

    assert response.status_code == 400
    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie or "01-Jan-1970" in set_cookie


def test_callback_unverified_email_returns_403(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    claims = _valid_claims(email)
    claims["email_verified"] = False
    _mock_exchange(monkeypatch, _fake_id_token(**claims))

    response = _complete_login(client, state)

    assert response.status_code == 403


def test_callback_missing_email_claim_returns_400(
    client, google_settings, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    claims = _valid_claims("placeholder@example.com")
    del claims["email"]
    _mock_exchange(monkeypatch, _fake_id_token(**claims))

    response = _complete_login(client, state)

    assert response.status_code == 400


def test_callback_escapes_email_on_success_page(
    client, google_settings, monkeypatch
):
    xss_email = "<script>alert(1)</script>@x.com"
    try:
        login_response = client.get("/login", follow_redirects=False)
        state = login_response.cookies.get(login_module.STATE_COOKIE)
        _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(xss_email)))

        response = _complete_login(client, state)

        assert response.status_code == 200
        assert "&lt;script&gt;" in response.text
        assert "<script>alert(1)</script>" not in response.text
    finally:
        cleanup_rows("DELETE FROM users WHERE email = %s", (xss_email,))


def test_callback_ensures_schema_before_login_with_email(
    client, google_settings, email, monkeypatch
):
    manager = Mock()
    manager.attach_mock(Mock(wraps=ensure_schema), "ensure_schema")
    manager.attach_mock(
        Mock(wraps=auth_repository.login_with_email), "login_with_email"
    )
    monkeypatch.setattr(
        login_module, "ensure_schema", manager.ensure_schema, raising=False
    )
    monkeypatch.setattr(
        login_module.auth_repository, "login_with_email", manager.login_with_email
    )

    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login(client, state)

    assert response.status_code == 200
    call_names = [c[0] for c in manager.mock_calls]
    assert "ensure_schema" in call_names
    assert call_names.index("ensure_schema") < call_names.index("login_with_email")


def test_callback_clears_state_cookie_on_success(
    client, google_settings, email, monkeypatch
):
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.cookies.get(login_module.STATE_COOKIE)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login(client, state)

    set_cookie = response.headers["set-cookie"]
    assert f"{login_module.STATE_COOKIE}=" in set_cookie
    assert "Max-Age=0" in set_cookie or "01-Jan-1970" in set_cookie


def test_callback_clears_state_cookie_on_error(client, google_settings):
    response = client.get("/oauth/callback", params={"error": "access_denied"})

    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie or "01-Jan-1970" in set_cookie


def test_get_login_with_loopback_sets_extended_cookie_and_bare_state(
    client, google_settings
):
    response = _login_with_loopback(client)

    assert response.status_code == 302
    bare_state = _bare_state_from_login(response)
    assert "|" not in bare_state

    set_cookie = response.headers["set-cookie"]
    name_value = set_cookie.split(";", 1)[0]
    expected = f"{login_module.STATE_COOKIE}={bare_state}|{VALID_PORT}|{VALID_NONCE}"
    assert name_value == expected

    cookie_state = response.cookies.get(login_module.STATE_COOKIE)
    assert cookie_state == f"{bare_state}|{VALID_PORT}|{VALID_NONCE}"


def test_get_login_with_loopback_preserves_cookie_flags(client, google_settings):
    plain_response = client.get("/login", follow_redirects=False)
    loopback_response = _login_with_loopback(client)

    def _flags(set_cookie: str) -> str:
        return set_cookie.split(";", 1)[1].lower()

    assert _flags(plain_response.headers["set-cookie"]) == _flags(
        loopback_response.headers["set-cookie"]
    )


@pytest.mark.parametrize(
    "port,nonce",
    [
        ("80", VALID_NONCE),
        ("70000", VALID_NONCE),
        ("abc", VALID_NONCE),
        (str(VALID_PORT), None),
        (None, VALID_NONCE),
        (str(VALID_PORT), "bad nonce!"),
        (str(VALID_PORT), "a" * 15),
        (str(VALID_PORT), "a" * 65),
    ],
)
def test_get_login_rejects_invalid_loopback_params(
    client, google_settings, port, nonce
):
    params = {}
    if port is not None:
        params["port"] = port
    if nonce is not None:
        params["nonce"] = nonce

    response = client.get("/login", params=params, follow_redirects=False)

    assert response.status_code == 400


def test_callback_with_loopback_redirects_to_127_0_0_1_with_key_nonce_email(
    client, google_settings, email, monkeypatch
):
    login_response = _login_with_loopback(client)
    bare_state = _bare_state_from_login(login_response)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login_no_follow(client, bare_state)

    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urllib.parse.urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == VALID_PORT
    query = urllib.parse.parse_qs(parsed.query)
    assert query["key"][0].startswith("mmd_")
    assert query["nonce"] == [VALID_NONCE]
    assert query["email"] == [email]


def test_callback_loopback_key_not_in_response_body(
    client, google_settings, email, monkeypatch
):
    login_response = _login_with_loopback(client)
    bare_state = _bare_state_from_login(login_response)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login_no_follow(client, bare_state)

    location = response.headers["location"]
    key = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["key"][0]
    assert key not in response.text


def test_callback_loopback_response_has_no_store_and_clears_cookie(
    client, google_settings, email, monkeypatch
):
    login_response = _login_with_loopback(client)
    bare_state = _bare_state_from_login(login_response)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login_no_follow(client, bare_state)

    assert response.headers["cache-control"] == "no-store"
    set_cookie = response.headers["set-cookie"]
    assert f"{login_module.STATE_COOKIE}=" in set_cookie
    assert "Max-Age=0" in set_cookie or "01-Jan-1970" in set_cookie


def test_callback_loopback_state_equal_to_whole_cookie_value_returns_400(
    client, google_settings
):
    login_response = _login_with_loopback(client)
    cookie_value = login_response.cookies.get(login_module.STATE_COOKIE)
    before = _user_count()

    response = client.get(
        "/oauth/callback",
        params={"state": cookie_value, "code": "test-code"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _user_count() == before


def test_callback_loopback_state_mismatch_returns_400(client, google_settings):
    _login_with_loopback(client)
    before = _user_count()

    response = client.get(
        "/oauth/callback",
        params={"state": "not-the-token-value-1234", "code": "test-code"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _user_count() == before


def test_callback_malformed_state_cookie_degrades_to_success_page(
    client, google_settings, email, monkeypatch
):
    garbage = "not-well-formed|missing-parts"
    client.cookies.set(login_module.STATE_COOKIE, garbage)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = client.get(
        "/oauth/callback", params={"state": garbage, "code": "test-code"}
    )

    assert response.status_code == 200
    assert response.text.count("mmd_") == 1


def test_callback_loopback_ignores_host_override_query_param(
    client, google_settings, email, monkeypatch
):
    login_response = _login_with_loopback(client)
    bare_state = _bare_state_from_login(login_response)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login_no_follow(client, bare_state, host="evil.com")

    assert response.status_code == 302
    location = response.headers["location"]
    assert urllib.parse.urlparse(location).hostname == "127.0.0.1"
    assert "evil.com" not in location


def test_callback_loopback_creates_user_and_persists_key(
    client, google_settings, email, monkeypatch
):
    login_response = _login_with_loopback(client)
    bare_state = _bare_state_from_login(login_response)
    _mock_exchange(monkeypatch, _fake_id_token(**_valid_claims(email)))

    response = _complete_login_no_follow(client, bare_state)

    assert response.status_code == 302
    conn = get_connection()
    row = conn.execute(
        """
        SELECT api_keys.key_hash
        FROM api_keys
        JOIN users ON users.id = api_keys.user_id
        WHERE users.email = %s
        """,
        (email,),
    ).fetchone()
    assert row is not None
    assert len(row["key_hash"]) == 64
