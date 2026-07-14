import json

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors
from memodi.tools.scope import resolve_project


def _ensure() -> None:
    ensure_schema()


@handle_errors
def session_start(
    path: str, user_id: str, machine: str, project: str | None = None
) -> str:
    """Start a new session for a project. Closes any existing active session."""
    _ensure()
    proj = resolve_project(user_id, machine, path, project)

    # Close any existing active session first
    active = repository.get_active_session(proj["id"])
    if active:
        repository.end_session(active["id"], summary=None)

    session = repository.create_session(proj["id"])
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
    """Close the active session for a project with a summary.

    If no session is active, one is created and closed on the
    spot so the summary is never lost.
    """
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
