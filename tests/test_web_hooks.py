import uuid

import pytest
from starlette.testclient import TestClient

from memodi import server
from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from tests.conftest import _path

API_KEY_HEADER = "X-Memodi-Api-Key"
MACHINE_HEADER = "X-Memodi-Machine"


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def client():
    return TestClient(server.mcp.streamable_http_app())


def _headers(registered_workspace):
    return {
        API_KEY_HEADER: registered_workspace["api_key"],
        MACHINE_HEADER: registered_workspace["machine"],
    }


def _project(registered_workspace, project_name):
    return repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )


def test_session_close_matching_client_id_closes_with_null_summary(
    client, registered_workspace
):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    client_session_id = str(uuid.uuid4())
    headers = _headers(registered_workspace)

    start = client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": client_session_id},
        headers=headers,
    )
    assert start.json()["started"] is True

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": client_session_id},
        headers=headers,
    )

    assert close.status_code == 200
    body = close.json()
    assert body["closed"] is True

    proj = _project(registered_workspace, project_name)
    assert repository.get_active_session(proj["id"]) is None
    row = get_connection().execute(
        "SELECT summary FROM sessions WHERE id = %s", (body["session_id"],)
    ).fetchone()
    assert row["summary"] is None


def test_session_close_non_matching_id_is_noop(client, registered_workspace):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    headers = _headers(registered_workspace)

    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": str(uuid.uuid4())},
        headers=headers,
    )

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": str(uuid.uuid4())},
        headers=headers,
    )

    assert close.status_code == 200
    body = close.json()
    assert body["closed"] is False
    assert "reason" in body

    proj = _project(registered_workspace, project_name)
    assert repository.get_active_session(proj["id"]) is not None


def test_session_close_absent_id_is_noop(client, registered_workspace):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    headers = _headers(registered_workspace)

    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": str(uuid.uuid4())},
        headers=headers,
    )

    close = client.post("/hooks/session-close", json={"path": path}, headers=headers)

    assert close.status_code == 200
    assert close.json()["closed"] is False

    proj = _project(registered_workspace, project_name)
    assert repository.get_active_session(proj["id"]) is not None


def test_session_close_never_creates_a_project_row(client, registered_workspace):
    project_name = f"test-hooks-nonexistent-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    conn = get_connection()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE workspace_id = %s",
        (registered_workspace["workspace"]["id"],),
    ).fetchone()["c"]

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": str(uuid.uuid4())},
        headers=_headers(registered_workspace),
    )

    assert close.status_code == 200
    assert close.json()["closed"] is False

    after = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE workspace_id = %s",
        (registered_workspace["workspace"]["id"],),
    ).fetchone()["c"]
    assert after == before


def test_session_close_never_writes_a_summary(client, registered_workspace):
    from memodi.tools.session import session_end, session_start

    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)
    session_end(**kwargs, summary="The real recap")

    client_session_id = str(uuid.uuid4())
    headers = _headers(registered_workspace)
    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": client_session_id},
        headers=headers,
    )
    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": client_session_id},
        headers=headers,
    )
    assert close.json()["closed"] is True

    proj = _project(registered_workspace, project_name)
    latest = repository.get_latest_session_summary(proj["id"])
    assert latest["summary"] == "The real recap"


def test_session_close_two_windows_only_matching_one_closes(
    client, registered_workspace
):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    headers = _headers(registered_workspace)

    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": id_a},
        headers=headers,
    )

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": id_b},
        headers=headers,
    )

    assert close.json()["closed"] is False

    proj = _project(registered_workspace, project_name)
    active = repository.get_active_session(proj["id"])
    assert active is not None
    assert active["client_session_id"] == id_a


def test_session_close_two_concurrent_sessions_only_closes_its_own(
    client, registered_workspace
):
    """Two windows in the same folder: starting the second must not close
    the first (the root regression), and closing one must not touch the
    other — both stay legally active until each closes itself."""
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    headers = _headers(registered_workspace)

    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": id_a},
        headers=headers,
    )
    client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": id_b},
        headers=headers,
    )

    proj = _project(registered_workspace, project_name)
    conn = get_connection()
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE project_id = %s AND ended_at IS NULL",
        (proj["id"],),
    ).fetchone()["c"]
    assert active_count == 2

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": id_a},
        headers=headers,
    )
    assert close.json()["closed"] is True

    row_a = conn.execute(
        "SELECT ended_at FROM sessions WHERE client_session_id = %s", (id_a,)
    ).fetchone()
    row_b = conn.execute(
        "SELECT ended_at FROM sessions WHERE client_session_id = %s", (id_b,)
    ).fetchone()
    assert row_a["ended_at"] is not None
    assert row_b["ended_at"] is None


def test_session_close_unregistered_path_returns_not_started(
    client, registered_workspace
):
    response = client.post(
        "/hooks/session-close",
        json={
            "path": f"/tmp/unregistered-hooks-{uuid.uuid4()}",
            "client_session_id": "x",
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 404
    assert response.json()["type"] == "not_started"


def test_session_close_missing_api_key_returns_not_authenticated(
    client, registered_workspace
):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)

    response = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": "x"},
        headers={MACHINE_HEADER: registered_workspace["machine"]},
    )

    assert response.status_code == 401
    assert response.json()["type"] == "not_authenticated"


def test_capture_saves_an_observation(client, registered_workspace):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)

    response = client.post(
        "/hooks/capture",
        json={
            "path": path,
            "title": "Subagent findings",
            "content": "Discovered X causes Y",
            "type": "discovery",
            "topic_key": "subagent/test/capture",
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 200
    body = response.json()
    assert "id" in body

    proj = _project(registered_workspace, project_name)
    row = get_connection().execute(
        "SELECT title FROM observations WHERE project_id = %s", (proj["id"],)
    ).fetchone()
    assert row["title"] == "Subagent findings"


def test_capture_unregistered_path_is_not_started_inert(client, registered_workspace):
    response = client.post(
        "/hooks/capture",
        json={
            "path": f"/tmp/unregistered-hooks-{uuid.uuid4()}",
            "title": "x",
            "content": "y",
            "type": "discovery",
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 404
    assert response.json()["type"] == "not_started"


def test_capture_ignores_a_client_supplied_type(client, registered_workspace):
    """The route pins discovery: automation never writes a session summary."""
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)

    response = client.post(
        "/hooks/capture",
        json={
            "path": path,
            "title": "x",
            "content": "y",
            "type": "session",
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 200
    assert response.json()["type"] == "discovery"


def test_two_captures_with_different_content_create_two_observations(
    client, registered_workspace
):
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    headers = _headers(registered_workspace)

    first = client.post(
        "/hooks/capture",
        json={"path": path, "title": "Subagent (Explore) findings", "content": "One"},
        headers=headers,
    )
    second = client.post(
        "/hooks/capture",
        json={"path": path, "title": "Subagent (Plan) findings", "content": "Two"},
        headers=headers,
    )

    assert first.json()["id"] != second.json()["id"]
    proj = _project(registered_workspace, project_name)
    count = (
        get_connection()
        .execute(
            "SELECT COUNT(*) AS c FROM observations WHERE project_id = %s",
            (proj["id"],),
        )
        .fetchone()["c"]
    )
    assert count == 2


# --- Malformed bodies: nothing reaches the database unvalidated ---

_SQL_LEAKS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "psycopg",
    "LINE ",
    "rstrip",
    "cannot adapt",
    "ProgrammingError",
    "DataError",
)

_ROUTES = ("/hooks/session-start", "/hooks/session-close", "/hooks/capture")


def _assert_clean_validation(response):
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["type"] == "validation"
    for leak in _SQL_LEAKS:
        assert leak not in body["error"], body["error"]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("payload", [None, [], "x", 123, True])
def test_non_object_json_body_never_500s(client, registered_workspace, route, payload):
    response = client.post(route, json=payload, headers=_headers(registered_workspace))

    assert response.status_code in (400, 404), response.text
    assert "type" in response.json()


@pytest.mark.parametrize("route", _ROUTES)
def test_non_string_path_returns_clean_validation(client, registered_workspace, route):
    response = client.post(
        route,
        json={"path": 5, "client_session_id": "x", "title": "t", "content": "c"},
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)


@pytest.mark.parametrize("route", ("/hooks/session-start", "/hooks/session-close"))
def test_non_string_client_session_id_returns_clean_validation(
    client, registered_workspace, route
):
    path = _path(registered_workspace, f"test-hooks-{uuid.uuid4()}")

    response = client.post(
        route,
        json={"path": path, "client_session_id": 123},
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)


def test_dict_body_field_returns_clean_validation(client, registered_workspace):
    path = _path(registered_workspace, f"test-hooks-{uuid.uuid4()}")

    response = client.post(
        "/hooks/capture",
        json={"path": path, "title": {"a": 1}, "content": "c"},
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)


def test_nul_byte_in_a_field_returns_clean_validation(client, registered_workspace):
    path = _path(registered_workspace, f"test-hooks-{uuid.uuid4()}")

    response = client.post(
        "/hooks/capture",
        json={"path": path, "title": "t", "content": "before\x00after"},
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)


def test_over_long_field_returns_clean_validation(client, registered_workspace):
    response = client.post(
        "/hooks/session-close",
        json={
            "path": _path(registered_workspace, f"test-hooks-{uuid.uuid4()}"),
            "client_session_id": "x" * 1024,
        },
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)


# --- Body size caps (signup already caps at 8KB; these routes must too) ---


@pytest.mark.parametrize("route", ("/hooks/session-start", "/hooks/session-close"))
def test_session_routes_reject_oversized_bodies(client, registered_workspace, route):
    response = client.post(
        route,
        json={"path": "/tmp/x", "client_session_id": "y" * 8192},
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 413
    assert response.json()["type"] == "payload_too_large"


def test_capture_rejects_oversized_bodies(client, registered_workspace):
    response = client.post(
        "/hooks/capture",
        json={
            "path": _path(registered_workspace, f"test-hooks-{uuid.uuid4()}"),
            "title": "t",
            "content": "y" * (70 * 1024),
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 413
    assert response.json()["type"] == "payload_too_large"


def test_capture_accepts_a_realistic_subagent_payload(client, registered_workspace):
    """The hook truncates at ~32KB; the cap must leave room for that."""
    project_name = f"test-hooks-{uuid.uuid4()}"

    response = client.post(
        "/hooks/capture",
        json={
            "path": _path(registered_workspace, project_name),
            "title": "Subagent (Explore) findings",
            "content": "y" * (32 * 1024),
        },
        headers=_headers(registered_workspace),
    )

    assert response.status_code == 200


# --- Capture validates before touching the project or the embedder (H3) ---


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "content": "c"},
        {"content": "c"},
        {"title": "t", "content": ""},
        {"title": "t"},
        {"title": "   ", "content": "   "},
    ],
)
def test_capture_without_title_and_content_creates_no_project(
    client, registered_workspace, body
):
    project_name = f"test-hooks-empty-{uuid.uuid4()}"
    conn = get_connection()
    workspace_id = registered_workspace["workspace"]["id"]
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE workspace_id = %s", (workspace_id,)
    ).fetchone()["c"]

    response = client.post(
        "/hooks/capture",
        json={"path": _path(registered_workspace, project_name), **body},
        headers=_headers(registered_workspace),
    )

    _assert_clean_validation(response)
    after = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE workspace_id = %s", (workspace_id,)
    ).fetchone()["c"]
    assert after == before


# --- Typeless error payloads (M1) ---


def test_response_defaults_to_500_for_a_typeless_error_payload():
    import json as _json

    from memodi.web.hooks import _response

    response = _response(_json.dumps({"error": "Observation 'x' not found"}))

    assert response.status_code == 500


# --- Blank client_session_id round trip (M2) ---


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_client_session_id_is_never_persisted_as_a_string(
    client, registered_workspace, blank
):
    """A blank tag can never be matched on close — store NULL, not ''."""
    project_name = f"test-hooks-{uuid.uuid4()}"
    path = _path(registered_workspace, project_name)
    headers = _headers(registered_workspace)

    start = client.post(
        "/hooks/session-start",
        json={"path": path, "client_session_id": blank},
        headers=headers,
    )
    assert start.status_code == 200

    row = (
        get_connection()
        .execute(
            "SELECT client_session_id FROM sessions WHERE id = %s",
            (start.json()["session_id"],),
        )
        .fetchone()
    )
    assert row["client_session_id"] is None

    close = client.post(
        "/hooks/session-close",
        json={"path": path, "client_session_id": blank},
        headers=headers,
    )
    assert close.json() == {"closed": False, "reason": "missing_client_session_id"}
