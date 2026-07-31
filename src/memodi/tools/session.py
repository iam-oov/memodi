import json

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors
from memodi.tools.scope import require_workspace, resolve_project

MAX_CLIENT_SESSION_ID = 256

_IGNORED_REASONS = {
    "nul": "client_session_id was ignored: it contains NUL characters",
    "too_long": (
        "client_session_id was ignored: it is longer than "
        f"{MAX_CLIENT_SESSION_ID} characters"
    ),
}


def _ensure() -> None:
    ensure_schema()


def _client_session_tag(value: str | None) -> tuple[str | None, str | None]:
    """Normalize a caller-supplied client_session_id into a storable tag.

    Returns (tag, ignored_reason). Validation happens here, BEFORE any SQL,
    because client_session_id lands in an indexed column: a NUL byte or a
    value past the btree limit aborts the statement, and the driver text
    (index name, relation, tuple pointer, HINT) would surface to the caller
    while the write it was carrying — a session summary — is lost. The
    /hooks routes guard the same field at the HTTP boundary with the same
    MAX_CLIENT_SESSION_ID, so both writers to that column agree.

    A blank, whitespace-only, or rejected id all normalize to None, which
    means the UNTAGGED identity: it matches only rows that carry no tag, so
    it can never target another window's session. None is never an error —
    an unusable id must never cost the summary.
    """
    if value is None:
        return None, None
    tag = value.strip()
    if not tag:
        return None, None
    if "\x00" in tag:
        return None, "nul"
    if len(tag) > MAX_CLIENT_SESSION_ID:
        return None, "too_long"
    return tag, None


@handle_errors
def session_start(
    path: str,
    user_id: str,
    machine: str,
    project: str | None = None,
    client_session_id: str | None = None,
) -> str:
    """Start a new session for a project. Closes only the caller's own
    previous session, matched by client_session_id.

    Concurrent active sessions per project are LEGAL: two Claude Code
    windows open in the same folder each get their own active session, one
    per client_session_id, and never close each other's. The match uses
    Postgres IS NOT DISTINCT FROM rather than =, so NULL matches NULL — an
    untagged caller (no client_session_id) still closes its own previous
    untagged row, exactly as before.

    client_session_id tags the session with the caller's own session id
    (e.g. Claude Code's) so it can later be closed by that exact id — see
    close_by_client_id. Optional: the MCP tool never passes it, only the
    /hooks/session-start route does. A blank, whitespace-only, or rejected
    id (see _client_session_tag) is stored as NULL: it is a tag no close
    could ever match, so persisting it would leave a session that can never
    be closed by id. The ack then carries client_session_id_ignored.
    """
    _ensure()
    proj = resolve_project(user_id, machine, path, project)

    tag, ignored = _client_session_tag(client_session_id)

    active = repository.get_active_session_by_client_id(proj["id"], tag)
    if active:
        repository.end_session(active["id"], summary=None)

    session = repository.create_session(proj["id"], client_session_id=tag)
    ack = {
        "session_id": str(session["id"]),
        "project": proj["name"],
        "started": True,
    }
    if ignored:
        ack["client_session_id_ignored"] = True
        ack["client_session_id_ignored_reason"] = _IGNORED_REASONS[ignored]
    return json.dumps(ack, default=str)


@handle_errors
def session_end(
    path: str,
    user_id: str,
    machine: str,
    summary: str,
    project: str | None = None,
    client_session_id: str | None = None,
) -> str:
    """Close a session for a project with a structured summary.

    client_session_id, when given, targets the session carrying that exact
    identity instead of guessing "the newest active session" — this is what
    lets concurrent windows (see session_start) each close their own session
    and write their own summary without clobbering another window's.

    A blank, whitespace-only, or rejected id means the same thing here as on
    session_start: the UNTAGGED identity, matching only rows that carry no
    tag. It never degrades to "whichever session is newest", which would let
    a caller with a broken id close another window's tagged session and
    write this summary there. A rejected id also sets
    client_session_id_ignored on the ack, with the reason.

    Omitting client_session_id entirely (None, not blank) keeps the
    behavior from before concurrent sessions existed, for MCP clients that
    ship no hook: the newest active session for the project whatever its
    tag, or an auto-started one if none is active.

    summary is required and rejected if empty or whitespace-only — an
    empty string still satisfies `summary IS NOT NULL`, which would let it
    outrank the real recap in get_latest_session_summary. The "never lose
    a summary" contract holds in every other case: if no session matches —
    none active at all, or none carrying this identity — one is created
    (tagged when the id is usable) and closed on the spot, so the summary is
    never lost (auto_started: true in that case).
    """
    if not summary or not summary.strip():
        raise ValueError("summary must not be empty or whitespace-only")

    _ensure()
    proj = resolve_project(user_id, machine, path, project)

    tag, ignored = _client_session_tag(client_session_id)

    if client_session_id is None:
        active = repository.get_active_session(proj["id"])
    else:
        active = repository.get_active_session_by_client_id(proj["id"], tag)

    auto_started = active is None
    if auto_started:
        active = repository.create_session(proj["id"], client_session_id=tag)

    session = repository.end_session(active["id"], summary=summary)
    if session is None:
        # The row closed between the read and the write (another window's
        # hygiene close). end_session refuses to overwrite an existing
        # summary, so persist this one on a row of our own instead.
        auto_started = True
        active = repository.create_session(proj["id"], client_session_id=tag)
        session = repository.end_session(active["id"], summary=summary)

    ack = {
        "ended": True,
        "auto_started": auto_started,
        "session_id": str(session["id"]),
        "project": proj["name"],
        "summary": summary,
    }
    if ignored:
        ack["client_session_id_ignored"] = True
        ack["client_session_id_ignored_reason"] = _IGNORED_REASONS[ignored]
    return json.dumps(ack, default=str)


@handle_errors
def close_by_client_id(
    path: str,
    user_id: str,
    machine: str,
    client_session_id: str,
) -> str:
    """Close the session carrying this exact client_session_id, scoped to
    the caller's workspace. Non-creating: resolves the workspace only
    (never a project) and never writes a summary.

    This is the automation-safe hygiene close used by the SessionEnd hook —
    a hook cannot prove which project's session is "the" active one (that
    guess is what closed other windows' sessions before), so it proves it
    instead by the id Claude Code assigned to its own conversation. No
    match — wrong id, absent id, or already closed — is a silent no-op,
    never touching a different session.
    """
    _ensure()
    workspace = require_workspace(user_id, machine, path)

    tag, ignored = _client_session_tag(client_session_id)
    if tag is None:
        reason = "missing_client_session_id" if ignored is None else ignored
        return json.dumps({"closed": False, "reason": reason})

    session = repository.close_session_by_client_id(workspace["id"], tag)
    if session is None:
        return json.dumps({"closed": False, "reason": "no_match"})

    return json.dumps({"closed": True, "session_id": str(session["id"])}, default=str)
