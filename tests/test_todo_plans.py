import json
import time
import urllib.parse
import uuid
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from memodi import server
from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import (
    claim_todo,
    context,
    digest_for_session_start,
    get_observation,
    reopen_todo,
    resolve_todo,
    save,
    search,
    search_hybrid,
    todo_progress,
)
from memodi.web import login as login_module
from memodi.web import session
from tests.conftest import cleanup_rows

SESSION_A = "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION_B = "22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECRET = "test-session-secret"
GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
PLAN = "# Plan\n\n1. Diagnose the wombat ledger\n2. Fix it"


@pytest.fixture(autouse=True)
def _schema():
    ensure_schema()


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", SECRET)
    monkeypatch.setattr(settings, "google_client_id", GOOGLE_CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(
        settings, "google_redirect_uri", "http://localhost:8787/oauth/callback"
    )
    return TestClient(server.mcp.streamable_http_app())


@pytest.fixture
def repo(registered_workspace):
    return {
        **registered_workspace,
        "path": f"{registered_workspace['root']}/test-plans-{uuid.uuid4()}",
    }


@pytest.fixture
def stranger():
    email = f"test-stranger-{uuid.uuid4()}@example.com"
    user = auth_repository.create_user(email)
    yield user
    cleanup_rows("DELETE FROM users WHERE id = %s", (user["id"],))


def _caller(ws: dict) -> dict:
    return {"user_id": ws["user_id"], "machine": ws["machine"]}


def _note(ws: dict, content: str = "Plain body", horizon: str | None = None) -> str:
    extra = {"horizon": horizon} if horizon else {}
    ack = json.loads(
        save(
            path=ws["path"],
            title=f"Rotate the wombat key {uuid.uuid4()}",
            content=content,
            type="decision",
            **_caller(ws),
            **extra,
        )
    )
    return ack["id"]


def _planned_todo(ws: dict, plan: str = PLAN) -> str:
    todo_id = _note(ws, horizon="todo")
    claim_todo(
        path=ws["path"],
        observation_id=todo_id,
        client_session_id=SESSION_A,
        **_caller(ws),
    )
    ack = json.loads(
        todo_progress(
            path=ws["path"],
            observation_id=todo_id,
            client_session_id=SESSION_A,
            plan_md=plan,
            **_caller(ws),
        )
    )
    assert ack["updated"] is True, ack
    return todo_id


def _read(ws: dict, obs_id: str) -> dict:
    return json.loads(
        get_observation(path=ws["path"], observation_id=obs_id, **_caller(ws))
    )


def _resolve(ws: dict, obs_id: str, **extra) -> dict:
    return json.loads(
        resolve_todo(path=ws["path"], observation_id=obs_id, **_caller(ws), **extra)
    )


def _sql(statement: str, params: tuple) -> list[dict]:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    cursor = conn.execute(statement, params)
    rows = cursor.fetchall() if cursor.description else []
    conn.commit()
    return rows


def _age_resolution(obs_id: str, days: int) -> None:
    _sql(
        "UPDATE observations SET resolved_at = now() - make_interval(days => %s)"
        " WHERE id = %s",
        (days, obs_id),
    )


def _stored_plan(obs_id: str) -> str | None:
    return _sql("SELECT plan_md FROM observations WHERE id = %s", (obs_id,))[0][
        "plan_md"
    ]


def _login_as(client: TestClient, user_id: str) -> None:
    client.cookies.set(session.SESSION_COOKIE, session.encode(str(user_id)))


def test_holder_stores_the_plan_and_any_session_reads_it(repo):
    todo_id = _planned_todo(repo)

    assert _read(repo, todo_id)["plan_md"] == PLAN.strip()


def test_progress_ack_points_to_the_plan(repo, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://memodi.example")
    todo_id = _planned_todo(repo)

    ack = json.loads(
        todo_progress(
            path=repo["path"],
            observation_id=todo_id,
            client_session_id=SESSION_A,
            phase_current=1,
            **_caller(repo),
        )
    )

    assert ack["plan_url"] == f"https://memodi.example/o/{todo_id}"


def test_another_session_cannot_write_the_plan(repo):
    todo_id = _planned_todo(repo)

    ack = json.loads(
        todo_progress(
            path=repo["path"],
            observation_id=todo_id,
            client_session_id=SESSION_B,
            plan_md="# Hijacked",
            **_caller(repo),
        )
    )

    assert ack["updated"] is False
    assert _read(repo, todo_id)["plan_md"] == PLAN.strip()


def test_plan_over_the_cap_is_rejected(repo):
    todo_id = _planned_todo(repo)

    ack = json.loads(
        todo_progress(
            path=repo["path"],
            observation_id=todo_id,
            client_session_id=SESSION_A,
            plan_md="x" * (64 * 1024 + 1),
            **_caller(repo),
        )
    )

    assert ack["type"] == "validation"


def test_the_plan_never_reaches_context_or_search(repo):
    # Keyword search proves the plan text is not indexed; hybrid always
    # returns the nearest notes, so it only proves the field never leaks.
    todo_id = _planned_todo(repo, plan="# Plan\n\nquokkaplanmarker steps")

    rows = json.loads(context(path=repo["path"], **_caller(repo)))["observations"]
    keyword = json.loads(
        search(path=repo["path"], query="quokkaplanmarker", **_caller(repo))
    )
    hybrid = json.loads(
        search_hybrid(path=repo["path"], query="quokkaplanmarker", **_caller(repo))
    )

    assert all("plan_md" not in r for r in rows + hybrid)
    assert all(r["id"] != todo_id for r in keyword)


def test_resolve_keeps_the_plan_for_the_grace_period(repo):
    todo_id = _planned_todo(repo)

    ack = _resolve(repo, todo_id, outcome="Rotated; see [[ops/wombat-key]]")

    assert ack["resolved"] is True
    assert ack["plan_deleted_after"]
    assert "warning" not in ack
    assert _read(repo, todo_id)["plan_md"] == PLAN.strip()


def test_resolving_a_planned_todo_without_outcome_warns(repo):
    ack = _resolve(repo, _planned_todo(repo))

    assert "conclusions" in ack["warning"]


def test_resolving_a_todo_without_plan_has_no_plan_fields(repo):
    ack = _resolve(repo, _note(repo, horizon="todo"))

    assert "plan_deleted_after" not in ack
    assert "warning" not in ack


def test_an_expired_plan_is_hidden_even_before_the_purge(repo):
    todo_id = _planned_todo(repo)
    _resolve(repo, todo_id, outcome="Done")
    _age_resolution(todo_id, days=8)

    assert "plan_md" not in _read(repo, todo_id)


def test_any_resolve_purges_every_expired_plan_on_the_server(repo):
    expired = _planned_todo(repo)
    _resolve(repo, expired, outcome="Done")
    _age_resolution(expired, days=8)
    fresh = _planned_todo(repo)
    _resolve(repo, fresh, outcome="Done")

    _resolve(repo, _note(repo, horizon="todo"), outcome="Unrelated")

    assert _stored_plan(expired) is None
    assert _stored_plan(fresh) == PLAN.strip()


def test_a_claim_also_purges_expired_plans(repo):
    expired = _planned_todo(repo)
    _resolve(repo, expired, outcome="Done")
    _age_resolution(expired, days=8)

    claim_todo(
        path=repo["path"],
        observation_id=_note(repo, horizon="todo"),
        client_session_id=SESSION_B,
        **_caller(repo),
    )

    assert _stored_plan(expired) is None


def test_reopen_within_the_grace_period_brings_the_plan_back(repo):
    todo_id = _planned_todo(repo)
    _resolve(repo, todo_id, outcome="Done")
    _age_resolution(todo_id, days=3)

    reopen_todo(path=repo["path"], observation_id=todo_id, **_caller(repo))

    assert _read(repo, todo_id)["plan_md"] == PLAN.strip()


def test_digest_points_to_the_plan(repo, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://memodi.example/")
    todo_id = _planned_todo(repo)

    digest = json.loads(digest_for_session_start(path=repo["path"], **_caller(repo)))

    assert f"plan: https://memodi.example/o/{todo_id}" in digest["digest"]


def test_digest_without_public_url_says_how_to_read_the_plan(repo, monkeypatch):
    monkeypatch.setattr(settings, "public_url", None)
    todo_id = _planned_todo(repo)

    digest = json.loads(digest_for_session_start(path=repo["path"], **_caller(repo)))

    assert f"plan: memodi_get_observation {todo_id}" in digest["digest"]


def test_session_cookie_round_trips(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", SECRET)
    user_id = str(uuid.uuid4())

    assert session.decode(session.encode(user_id)) == user_id


def test_tampered_session_cookie_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", SECRET)
    payload, signature = session.encode(str(uuid.uuid4())).split(".")
    forged = session.encode(str(uuid.uuid4())).split(".")[0]

    assert session.decode(f"{forged}.{signature}") is None
    assert session.decode(f"{payload}.{'0' * len(signature)}") is None


def test_expired_session_cookie_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", SECRET)
    old = session.encode(str(uuid.uuid4()), now=time.time() - 30 * 86400)

    assert session.decode(old) is None


def test_session_cookie_signed_with_another_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "other-secret")
    cookie = session.encode(str(uuid.uuid4()))
    monkeypatch.setattr(settings, "session_secret", SECRET)

    assert session.decode(cookie) is None


def test_page_without_a_session_redirects_to_login(web):
    obs_id = str(uuid.uuid4())

    response = web.get(f"/o/{obs_id}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == f"/login?next=/o/{obs_id}"


def test_page_is_disabled_without_a_session_secret(web, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", None)

    assert web.get(f"/o/{uuid.uuid4()}").status_code == 503


def test_owner_sees_the_note_and_its_plan(web, repo):
    todo_id = _planned_todo(repo)
    _login_as(web, repo["user_id"])

    response = web.get(f"/o/{todo_id}")

    assert response.status_code == 200
    assert "<h1>Plan</h1>" in response.text
    assert "<li>Diagnose the wombat ledger</li>" in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"


def test_another_users_note_is_not_found(web, repo, stranger):
    obs_id = _note(repo)
    _login_as(web, stranger["id"])

    assert web.get(f"/o/{obs_id}").status_code == 404


def test_a_bad_id_is_not_found(web, repo):
    _login_as(web, repo["user_id"])

    assert web.get("/o/not-a-uuid").status_code == 404


def test_markdown_cannot_inject_html_or_script_links(web, repo):
    obs_id = _note(
        repo,
        content=(
            "<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n"
            '<img src=x onerror="alert(1)">'
        ),
    )
    _login_as(web, repo["user_id"])

    body = web.get(f"/o/{obs_id}").text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert 'href="javascript:' not in body
    assert "<img src=x" not in body


def test_an_expired_plan_is_not_rendered(web, repo):
    todo_id = _planned_todo(repo)
    _resolve(repo, todo_id, outcome="Done")
    _age_resolution(todo_id, days=8)
    _login_as(web, repo["user_id"])

    body = web.get(f"/o/{todo_id}").text

    assert "Diagnose the wombat ledger" not in body
    assert "Done" in body


def test_logout_clears_the_session(web, repo):
    _login_as(web, repo["user_id"])

    response = web.get("/web/logout")

    assert response.status_code == 200
    assert session.SESSION_COOKIE in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"] or "expires=" in (
        response.headers["set-cookie"].lower()
    )


def _state_from(login_response) -> str:
    location = login_response.headers["location"]
    return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["state"][0]


def _fake_id_token(email: str) -> str:
    import base64

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {
        "aud": GOOGLE_CLIENT_ID,
        "iss": "https://accounts.google.com",
        "email": email,
        "email_verified": True,
    }
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def _api_key_count(user_id: str) -> int:
    return _sql("SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s", (user_id,))[0][
        "c"
    ]


def test_web_login_sets_a_session_and_mints_no_api_key(web, stranger, monkeypatch):
    target = f"/o/{uuid.uuid4()}"
    monkeypatch.setattr(
        login_module,
        "_exchange_code",
        AsyncMock(return_value={"id_token": _fake_id_token(stranger["email"])}),
    )
    keys_before = _api_key_count(stranger["id"])

    login = web.get("/login", params={"next": target}, follow_redirects=False)
    callback = web.get(
        "/oauth/callback",
        params={"state": _state_from(login), "code": "c"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == target
    assert session.decode(callback.cookies[session.SESSION_COOKIE]) == str(
        stranger["id"]
    )
    assert _api_key_count(stranger["id"]) == keys_before


def test_web_login_for_an_unknown_email_is_refused(web, monkeypatch):
    monkeypatch.setattr(
        login_module,
        "_exchange_code",
        AsyncMock(
            return_value={"id_token": _fake_id_token(f"nobody-{uuid.uuid4()}@x.com")}
        ),
    )
    login = web.get(
        "/login", params={"next": f"/o/{uuid.uuid4()}"}, follow_redirects=False
    )

    callback = web.get(
        "/oauth/callback",
        params={"state": _state_from(login), "code": "c"},
        follow_redirects=False,
    )

    assert callback.status_code == 403
    assert session.SESSION_COOKIE not in callback.cookies


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/o/x",
        "//evil.example",
        "/o/not-a-uuid",
        "/login",
        f"/o/{uuid.uuid4()}/../../admin",
    ],
)
def test_login_refuses_a_next_outside_the_viewer(web, target):
    response = web.get("/login", params={"next": target}, follow_redirects=False)

    assert response.status_code == 400


def test_login_refuses_next_together_with_a_loopback_port(web):
    response = web.get(
        "/login",
        params={"next": f"/o/{uuid.uuid4()}", "port": "51774", "nonce": "a" * 32},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_login_refuses_next_when_the_web_view_is_disabled(web, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", None)

    response = web.get(
        "/login", params={"next": f"/o/{uuid.uuid4()}"}, follow_redirects=False
    )

    assert response.status_code == 400
