import json

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors


def _ensure() -> None:
    ensure_schema()


@handle_errors
def save(
    project: str,
    title: str,
    content: str,
    type: str,
    topic_key: str | None = None,
    metadata: dict | None = None,
) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    obs = repository.save_observation(
        project_id=proj["id"],
        title=title,
        content=content,
        type=type,
        topic_key=topic_key,
        metadata=metadata,
    )
    result = json.loads(json.dumps(obs, default=str))
    if proj.get("workspace_id") is None:
        result["_warning"] = (
            "Project has no workspace. Use memodi_link_project to link it."
        )
    return json.dumps(result, default=str)


@handle_errors
def search(
    project: str,
    query: str,
    type: str | None = None,
    limit: int = 10,
) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    results = repository.search_observations(
        project_id=proj["id"],
        query=query,
        type=type,
        limit=limit,
        workspace_id=proj.get("workspace_id"),
    )
    return json.dumps(results, default=str)


@handle_errors
def context(project: str, limit: int = 20) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    results = repository.get_recent_observations(
        project_id=proj["id"],
        limit=limit,
        workspace_id=proj.get("workspace_id"),
    )
    return json.dumps(results, default=str)


@handle_errors
def list_projects() -> str:
    _ensure()
    results = repository.list_projects()
    return json.dumps(results, default=str)


@handle_errors
def search_global(query: str, type: str | None = None, limit: int = 10) -> str:
    _ensure()
    results = repository.search_observations_global(query=query, type=type, limit=limit)
    return json.dumps(results, default=str)


@handle_errors
def list_workspaces() -> str:
    _ensure()
    results = repository.list_workspaces()
    return json.dumps(results, default=str)


@handle_errors
def link_project(project: str, workspace: str) -> str:
    _ensure()
    result = repository.link_project_to_workspace(project, workspace)
    return json.dumps(result, default=str)


@handle_errors
def check_workspace(project: str) -> str:
    _ensure()
    ws = repository.get_project_workspace(project)
    if ws:
        return json.dumps({"linked": True, "workspace": ws}, default=str)
    workspaces = repository.list_workspaces()
    return json.dumps(
        {"linked": False, "available_workspaces": workspaces},
        default=str,
    )
