import json

from starlette.requests import Request
from starlette.responses import JSONResponse

from memodi.tools import memory, session
from memodi.tools.context import API_KEY_HEADER, MACHINE_HEADER
from memodi.tools.errors import NotAuthenticatedError
from memodi.tools.scope import require_user

# Narrow, plain-HTTP counterpart to the MCP tools, for Claude Code hooks —
# a shell script cannot speak the MCP protocol reliably (no `mcp` package
# outside the project venv), so these routes exist for curl instead. Same
# auth headers as MCP, same self-describing error shapes as tools/errors.py,
# but real HTTP status codes: MCP carries errors in a 200 envelope, plain
# HTTP callers and edge tooling read the status line.
#
# MCP validates tool arguments through pydantic before a tool runs; these
# routes have no such layer, so every field is validated here, at the
# boundary, BEFORE any tool call. Otherwise a JSON body flows straight into
# SQL and the failure surfaces as driver text in the response body.

MAX_SESSION_BODY = 4 * 1024
MAX_CAPTURE_BODY = 64 * 1024

MAX_PATH = 1024
MAX_CLIENT_SESSION_ID = 256
MAX_TITLE = 512
MAX_CONTENT = 48 * 1024
MAX_TOPIC_KEY = 512

CAPTURE_TYPE = "discovery"

_ERROR_STATUS = {
    "not_authenticated": 401,
    "not_started": 404,
    "validation": 400,
    "payload_too_large": 413,
    "internal": 500,
}


class _InvalidFieldError(Exception):
    """A body field that must never reach a tool call."""


def _error(message: str, type_: str) -> JSONResponse:
    return JSONResponse(
        {"error": message, "type": type_}, status_code=_ERROR_STATUS[type_]
    )


def _field(body: dict, name: str, max_length: int, required: bool = False) -> str:
    """Read one body field as a plain, bounded, NUL-free string.

    Anything else is rejected by name: a number, list, or dict reaching
    psycopg produces driver text (adaptation errors, SQL fragments with
    column names and line numbers) that would then be echoed to the caller.
    """
    value = body.get(name)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _InvalidFieldError(f"{name} must be a string")
    if required and not value.strip():
        raise _InvalidFieldError(f"{name} is required and must not be empty")
    if len(value) > max_length:
        raise _InvalidFieldError(f"{name} must be at most {max_length} characters")
    if "\x00" in value:
        raise _InvalidFieldError(f"{name} must not contain NUL characters")
    return value


def _response(result: str) -> JSONResponse:
    """Map a tool result to an HTTP status.

    Only payloads carrying an "error" key are failures: a successful save ack
    also has a "type" field (the observation type), and a no-op close reports
    itself with "closed": false, which is a 200.
    """
    payload = json.loads(result)
    if "error" in payload:
        status = _ERROR_STATUS.get(payload.get("type"), 500)
        return JSONResponse(payload, status_code=status)
    return JSONResponse(payload)


def _caller(request: Request) -> dict | JSONResponse:
    """Resolve (user_id, machine) from request headers.

    Returns a JSONResponse on auth failure, otherwise a dict with user_id
    and machine — mirrors server.py's _caller, but reads headers straight
    off the Starlette Request since these are plain HTTP routes, not MCP
    tool calls carrying a Context.
    """
    try:
        user = require_user(request.headers.get(API_KEY_HEADER))
    except NotAuthenticatedError as e:
        return JSONResponse(
            {"error": str(e), "type": "not_authenticated"}, status_code=401
        )
    return {"user_id": user["id"], "machine": request.headers.get(MACHINE_HEADER)}


async def _body(request: Request, max_bytes: int) -> dict | JSONResponse:
    """Read a bounded JSON object body — same cap pattern as /signup.

    Unparseable or non-object bodies (null, a list, a bare scalar) become an
    empty dict so the route's own field validation reports the problem
    instead of an AttributeError on body.get.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                return _error("request body too large", "payload_too_large")
        except ValueError:
            return _error("invalid Content-Length header", "validation")

    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > max_bytes:
            return _error("request body too large", "payload_too_large")

    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def post_session_start(request: Request) -> JSONResponse:
    """Open a session tagged with the caller's client_session_id."""
    caller = _caller(request)
    if isinstance(caller, JSONResponse):
        return caller
    body = await _body(request, MAX_SESSION_BODY)
    if isinstance(body, JSONResponse):
        return body
    try:
        path = _field(body, "path", MAX_PATH)
        client_session_id = _field(body, "client_session_id", MAX_CLIENT_SESSION_ID)
    except _InvalidFieldError as e:
        return _error(str(e), "validation")

    result = session.session_start(
        path,
        caller["user_id"],
        caller["machine"],
        client_session_id=client_session_id,
    )
    return _response(result)


async def post_session_close(request: Request) -> JSONResponse:
    """Close only the session carrying this exact client_session_id."""
    caller = _caller(request)
    if isinstance(caller, JSONResponse):
        return caller
    body = await _body(request, MAX_SESSION_BODY)
    if isinstance(body, JSONResponse):
        return body
    try:
        path = _field(body, "path", MAX_PATH)
        client_session_id = _field(body, "client_session_id", MAX_CLIENT_SESSION_ID)
    except _InvalidFieldError as e:
        return _error(str(e), "validation")

    result = session.close_by_client_id(
        path,
        caller["user_id"],
        caller["machine"],
        client_session_id,
    )
    return _response(result)


async def post_capture(request: Request) -> JSONResponse:
    """Save an observation — the route that revives SubagentStop capture.

    The type is pinned to discovery rather than validated: the only caller
    is the SubagentStop hook, and an automation route must never be able to
    write a session summary.
    """
    caller = _caller(request)
    if isinstance(caller, JSONResponse):
        return caller
    body = await _body(request, MAX_CAPTURE_BODY)
    if isinstance(body, JSONResponse):
        return body
    try:
        path = _field(body, "path", MAX_PATH)
        title = _field(body, "title", MAX_TITLE, required=True)
        content = _field(body, "content", MAX_CONTENT, required=True)
        topic_key = _field(body, "topic_key", MAX_TOPIC_KEY)
    except _InvalidFieldError as e:
        return _error(str(e), "validation")

    result = memory.save(
        path,
        caller["user_id"],
        caller["machine"],
        title,
        content,
        CAPTURE_TYPE,
        topic_key=topic_key or None,
    )
    return _response(result)
