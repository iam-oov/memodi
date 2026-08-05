import os

from memodi.database import auth_repository, repository
from memodi.tools.errors import NotAuthenticatedError, NotStartedError


def require_user(api_key: str | None) -> dict:
    if api_key:
        user = auth_repository.get_user_by_api_key(api_key)
        if user is not None:
            return user
    raise NotAuthenticatedError("Missing or invalid api key.")


def require_workspace(user_id: str, machine: str, path: str) -> dict:
    workspace = repository.resolve_workspace(user_id, machine, path)
    if workspace is None:
        raise NotStartedError(
            f"memodi is not started for {path} on machine {machine}. "
            "Run memodi_workspace_start(path=<parent folder>, workspace=<name>) "
            "after confirming the workspace name with the user."
        )
    return workspace


def resolve_project(user_id: str, machine: str, path: str, project: str | None) -> dict:
    workspace = require_workspace(user_id, machine, path)
    name = project or os.path.basename(path.rstrip("/"))
    if not name:
        raise ValueError(
            f"cannot derive project name from path '{path}'; "
            "pass an explicit project name"
        )
    resolved = repository.get_or_create_project(name, workspace_id=workspace["id"])
    # An explicit project name is an explicit request to narrow, so it never
    # counts as sitting at the root even when the cwd is the registered path.
    at_root = project is None and path.rstrip("/") == workspace.get("matched_path")
    return {**resolved, "at_workspace_root": at_root}
