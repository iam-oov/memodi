import json

from memodi.database import graph_repository, repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors
from memodi.tools.scope import resolve_project
from memodi.tools.serialization import (
    serialize_observation_save,
    serialize_observations,
    serialize_session_summary,
)


def _ensure() -> None:
    ensure_schema()


@handle_errors
def save(
    path: str,
    user_id: str,
    machine: str,
    title: str,
    content: str,
    type: str,
    project: str | None = None,
    topic_key: str | None = None,
    metadata: dict | None = None,
    occurred_at: str | None = None,
) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = resolve_project(user_id, machine, path, project)
    embedding = generate_embedding(f"{title} {content}")
    # Auto-attach active session if one exists
    active_session = repository.get_active_session(proj["id"])
    session_id = str(active_session["id"]) if active_session else None
    obs = repository.save_observation(
        project_id=proj["id"],
        title=title,
        content=content,
        type=type,
        topic_key=topic_key,
        session_id=session_id,
        metadata=metadata,
        embedding=embedding,
        occurred_at=occurred_at,
    )
    return json.dumps(serialize_observation_save(obs), default=str)


@handle_errors
def search(
    path: str,
    user_id: str,
    machine: str,
    query: str,
    project: str | None = None,
    type: str | None = None,
    limit: int = 10,
) -> str:
    _ensure()
    proj = resolve_project(user_id, machine, path, project)
    results = repository.search_observations(
        project_id=proj["id"],
        query=query,
        type=type,
        limit=limit,
        workspace_id=proj["workspace_id"],
    )
    return json.dumps(serialize_observations(results), default=str)


@handle_errors
def context(
    path: str,
    user_id: str,
    machine: str,
    project: str | None = None,
    limit: int = 20,
) -> str:
    _ensure()
    proj = resolve_project(user_id, machine, path, project)
    last_session = repository.get_latest_session_summary(
        proj["id"], workspace_id=proj["workspace_id"]
    )
    observations = repository.get_recent_observations(
        project_id=proj["id"],
        limit=limit,
        workspace_id=proj["workspace_id"],
    )
    return json.dumps(
        {
            "last_session": serialize_session_summary(last_session)
            if last_session
            else None,
            "observations": serialize_observations(observations),
        },
        default=str,
    )


@handle_errors
def list_projects(user_id: str) -> str:
    _ensure()
    results = repository.list_projects(owner_user_id=user_id)
    return json.dumps(results, default=str)


@handle_errors
def search_global(
    user_id: str, query: str, type: str | None = None, limit: int = 10
) -> str:
    _ensure()
    results = repository.search_observations_global(
        query=query, owner_user_id=user_id, type=type, limit=limit
    )
    return json.dumps(serialize_observations(results), default=str)


@handle_errors
def list_workspaces(user_id: str) -> str:
    _ensure()
    results = repository.list_workspaces(owner_user_id=user_id)
    return json.dumps(results, default=str)


@handle_errors
def workspace_start(path: str, workspace: str, user_id: str, machine: str) -> str:
    _ensure()
    result = repository.workspace_start(user_id, machine, path, workspace)
    return json.dumps(result, default=str)


@handle_errors
def merge_projects(
    source_project_id: str,
    target_project_id: str,
    user_id: str,
    dry_run: bool = True,
) -> str:
    _ensure()

    source_owner = repository.get_project_owner(source_project_id)
    if source_owner is None or str(source_owner) != str(user_id):
        raise ValueError(
            f"Source project '{source_project_id}' not found or not owned by caller"
        )
    target_owner = repository.get_project_owner(target_project_id)
    if target_owner is None or str(target_owner) != str(user_id):
        raise ValueError(
            f"Target project '{target_project_id}' not found or not owned by caller"
        )

    if dry_run:
        would_move = repository.count_project_resources(source_project_id)
        return json.dumps(
            {
                "dry_run": True,
                "source_project_id": source_project_id,
                "target_project_id": target_project_id,
                "would_move": would_move,
            },
            default=str,
        )

    result = repository.merge_projects(source_project_id, target_project_id)
    result["dry_run"] = False
    return json.dumps(result, default=str)


@handle_errors
def delete_workspace(workspace: str, user_id: str) -> str:
    _ensure()
    deleted = repository.delete_workspace(workspace, user_id)
    if deleted:
        return json.dumps({"deleted": True, "workspace": workspace})
    return json.dumps({"deleted": False, "error": f"Workspace '{workspace}' not found"})


@handle_errors
def rename_workspace(old_name: str, new_name: str, user_id: str) -> str:
    _ensure()
    result = repository.rename_workspace(old_name, new_name, user_id)
    if result:
        return json.dumps(result, default=str)
    return json.dumps({"error": f"Workspace '{old_name}' not found"})


@handle_errors
def search_similar(
    path: str,
    user_id: str,
    machine: str,
    query: str,
    project: str | None = None,
    limit: int = 10,
) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = resolve_project(user_id, machine, path, project)
    embedding = generate_embedding(query)
    results = repository.search_similar(
        project_id=proj["id"],
        embedding=embedding,
        limit=limit,
        workspace_id=proj["workspace_id"],
    )
    return json.dumps(serialize_observations(results), default=str)


@handle_errors
def search_hybrid(
    path: str,
    user_id: str,
    machine: str,
    query: str,
    project: str | None = None,
    limit: int = 10,
) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = resolve_project(user_id, machine, path, project)
    embedding = generate_embedding(query)
    results = repository.search_hybrid(
        project_id=proj["id"],
        query=query,
        embedding=embedding,
        limit=limit,
        workspace_id=proj["workspace_id"],
    )
    return json.dumps(serialize_observations(results), default=str)


@handle_errors
def purge_workspace(
    workspace: str,
    user_id: str,
    mode: str = "medium",
    purge_graph: bool = False,
    dry_run: bool = True,
) -> str:
    """Wipe workspace data for dev loops (e.g. re-importing .md files).

    mode='medium': observations, workflows, workflow_transitions, sessions.
        Projects, workspace, and workspace_paths are preserved — you can
        re-import into the same structure.
    mode='hard': medium + projects + workspace + workspace_paths. The
        workspace ceases to exist.

    purge_graph: if True, ALSO wipes the ENTIRE knowledge graph (global,
        not scoped to this workspace). Only enable if you know the graph
        only holds data for this workspace, or if you are performing a
        total reset.

    dry_run (default True): returns counts of what WOULD be deleted
        without touching anything. Set False to execute.
    """
    _ensure()

    if mode not in ("medium", "hard"):
        return json.dumps(
            {"error": "mode must be 'medium' or 'hard'"},
        )

    counts = repository.count_workspace_resources(workspace, user_id)
    if counts is None:
        return json.dumps(
            {"error": f"Workspace '{workspace}' not found"},
        )

    graph_counts = None
    if purge_graph:
        from memodi.database.graph import ensure_graph

        ensure_graph()
        graph_counts = graph_repository.count_all_graph_resources()

    if dry_run:
        would_delete = {
            "observations": counts["observations"],
            "workflows": counts["workflows"],
            "workflow_transitions": counts["workflow_transitions"],
            "sessions": counts["sessions"],
        }
        would_preserve = {
            "workspace": workspace,
            "projects": counts["project_names"],
            "workspace_paths": counts["workspace_paths"],
        }
        if mode == "hard":
            would_delete["projects"] = counts["projects"]
            would_delete["workspace_paths"] = counts["workspace_paths"]
            would_delete["workspace"] = True
            would_preserve = {}
        if graph_counts is not None:
            would_delete["graph_nodes"] = graph_counts["nodes"]
            would_delete["graph_edges"] = graph_counts["edges"]
        return json.dumps(
            {
                "dry_run": True,
                "mode": mode,
                "purge_graph": purge_graph,
                "workspace": workspace,
                "would_delete": would_delete,
                "would_preserve": would_preserve,
            }
        )

    result = repository.purge_workspace_data(workspace, user_id, mode=mode)

    if purge_graph:
        graph_result = graph_repository.purge_all_graph()
        result["graph_nodes_deleted"] = graph_result["nodes_deleted"]
        result["graph_edges_deleted"] = graph_result["edges_deleted"]
    result["dry_run"] = False
    return json.dumps(result, default=str)


@handle_errors
def backfill_embeddings(
    path: str, user_id: str, machine: str, project: str | None = None
) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = resolve_project(user_id, machine, path, project)
    observations = repository.get_observations_without_embedding(proj["id"])
    count = 0
    for obs in observations:
        embedding = generate_embedding(f"{obs['title']} {obs['content']}")
        repository.update_observation_embedding(obs["id"], embedding)
        count += 1
    return json.dumps({"backfilled": count, "project": proj["name"]})
