import json

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors
from memodi.tools.scope import require_workspace, resolve_project


def _ensure() -> None:
    ensure_schema()


@handle_errors
def session_start(
    path: str,
    user_id: str,
    machine: str,
    project: str | None = None,
    client_session_id: str | None = None,
) -> str:
    """Start a new session for a project. Closes any existing active session.

    client_session_id tags the session with the caller's own session id
    (e.g. Claude Code's) so it can later be closed by that exact id — see
    close_by_client_id. Optional: the MCP tool never passes it, only the
    /hooks/session-start route does. A blank or whitespace-only id is stored
    as NULL: it is a tag no close could ever match, so persisting it would
    leave a session that can never be closed by id.
    """
    _ensure()
    proj = resolve_project(user_id, machine, path, project)

    # Close any existing active session first
    active = repository.get_active_session(proj["id"])
    if active:
        repository.end_session(active["id"], summary=None)

    tag = client_session_id.strip() if client_session_id else ""
    session = repository.create_session(proj["id"], client_session_id=tag or None)
    return json.dumps(
        {"session_id": str(session["id"]), "project": proj["name"], "started": True},
        default=str,
    )


@handle_errors
def session_end(
    path: str,
    user_id: str,
    machine: str,
    summary: str,
    project: str | None = None,
) -> str:
    """Close the active session for a project with a structured summary.

    summary is required and rejected if empty or whitespace-only — an
    empty string still satisfies `summary IS NOT NULL`, which would let it
    outrank the real recap in get_latest_session_summary. If no session is
    active, one is created and closed on the spot so the summary is never
    lost (auto_started: true in that case).
    """
    if not summary or not summary.strip():
        raise ValueError("summary must not be empty or whitespace-only")

    _ensure()
    proj = resolve_project(user_id, machine, path, project)
    active = repository.get_active_session(proj["id"])

    auto_started = active is None
    if auto_started:
        active = repository.create_session(proj["id"])

    session = repository.end_session(active["id"], summary=summary)
    return json.dumps(
        {
            "ended": True,
            "auto_started": auto_started,
            "session_id": str(session["id"]),
            "project": proj["name"],
            "summary": summary,
        },
        default=str,
    )


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

    tag = client_session_id.strip() if client_session_id else ""
    if not tag:
        return json.dumps({"closed": False, "reason": "missing_client_session_id"})

    session = repository.close_session_by_client_id(workspace["id"], tag)
    if session is None:
        return json.dumps({"closed": False, "reason": "no_match"})

    return json.dumps({"closed": True, "session_id": str(session["id"])}, default=str)
