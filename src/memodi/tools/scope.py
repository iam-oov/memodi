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
            "Run memodi_workspace_start(path=<this folder>, workspace=<name>) "
            "after confirming the workspace name with the user."
        )
    return workspace


def workspace_root_project(workspace: dict) -> str | None:
    """Project name a session opened AT a registered root resolves to — the
    shared layer every child folder in the workspace inherits.

    It is the WORKSPACE's name, deliberately not the folder's. A workspace may
    be rooted at many paths — several on one machine, more on others — and
    those folders rarely share a name (`TirielInc` here, `TirielInc_Automatice`
    there). Deriving it from the basename gave each root its own container
    project and split the inherited layer per machine, which is the exact
    fragmentation multi-path workspaces exist to prevent.
    """
    return repository.normalize_name(workspace.get("name")) or None


def scoped_project_names(workspace: dict, path: str) -> dict:
    """Read scope for a path, as project NAMES — ready to splat into
    repository.search_observations_by_workspace.

    All-None at the registered root, where every project in the workspace is
    in scope. Name-only on purpose: the per-prompt hook reads on every message
    and must never write, so it can never take resolve_project's
    get-or-create path.
    """
    normalized = path.rstrip("/")
    own = repository.normalize_name(os.path.basename(normalized)) or None
    if own is None or normalized == workspace.get("matched_path"):
        return {"project_names": None, "affects_name": None, "inherited_names": None}
    root = workspace_root_project(workspace)
    return {
        "project_names": [own],
        "affects_name": own,
        "inherited_names": [root] if root and root != own else None,
    }


def scoped_project_ids(workspace: dict, path: str) -> list[str] | None:
    """Existing project ids a path reads from — its own and the workspace
    root's — resolved WITHOUT creating anything.

    None means no narrowing at all (the caller is AT a registered root).
    An empty list means neither project exists yet: nothing is in scope, which
    is emphatically not the same as everything. Callers must handle the two
    apart, because the SQL scope predicate treats an empty id list as
    "no filter".
    """
    names = scoped_project_names(workspace, path)
    if names["project_names"] is None:
        return None
    wanted = [*names["project_names"], *(names["inherited_names"] or [])]
    ids = []
    for name in wanted:
        project = repository.get_project_by_name(name, workspace_id=workspace["id"])
        if project is not None:
            ids.append(str(project["id"]))
    return ids


def _inherited_ids(
    resolved: dict, workspace: dict, project: str | None
) -> list[str] | None:
    """The workspace root's project id, whose untargeted memory a child folder
    inherits. None when there is nothing to inherit.

    An explicit project name is an explicit request to narrow, so it inherits
    nothing. The root project is looked up, never created — asking a question
    must not conjure a project row.
    """
    if project is not None:
        return None
    root_name = workspace_root_project(workspace)
    if root_name is None or root_name == resolved["name"]:
        return None
    root = repository.get_project_by_name(root_name, workspace_id=workspace["id"])
    return [root["id"]] if root else None


def resolve_project(user_id: str, machine: str, path: str, project: str | None) -> dict:
    workspace = require_workspace(user_id, machine, path)
    normalized = path.rstrip("/")
    # An explicit project name is an explicit request to narrow, so it never
    # counts as sitting at the root even when the cwd is the registered path.
    at_root = project is None and normalized == workspace.get("matched_path")
    name = project or (
        workspace_root_project(workspace)
        if at_root
        else os.path.basename(normalized)
    )
    if not name:
        raise ValueError(
            f"cannot derive project name from path '{path}'; "
            "pass an explicit project name"
        )
    resolved = repository.get_or_create_project(name, workspace_id=workspace["id"])
    return {
        **resolved,
        "at_workspace_root": at_root,
        "inherited_ids": None
        if at_root
        else _inherited_ids(resolved, workspace, project),
    }
