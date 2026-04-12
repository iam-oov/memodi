import json

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors


def _ensure() -> None:
    ensure_schema()


@handle_errors
def session_start(project: str) -> str:
    """Start a new session for a project. Closes any existing active session."""
    _ensure()
    proj = repository.get_or_create_project(project)

    # Close any existing active session first
    active = repository.get_active_session(proj["id"])
    if active:
        repository.end_session(active["id"], summary=None)

    session = repository.create_session(proj["id"])
    return json.dumps(
        {"session_id": str(session["id"]), "project": project, "started": True},
        default=str,
    )


@handle_errors
def session_end(project: str, summary: str) -> str:
    """Close the active session for a project with a summary."""
    _ensure()
    proj = repository.get_or_create_project(project)
    active = repository.get_active_session(proj["id"])

    if not active:
        return json.dumps({"ended": False, "error": "No active session found"})

    session = repository.end_session(active["id"], summary=summary)
    return json.dumps(
        {
            "ended": True,
            "session_id": str(session["id"]),
            "project": project,
            "summary": summary,
        },
        default=str,
    )
