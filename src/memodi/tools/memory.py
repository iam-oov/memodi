import json

from memodi.database import graph_repository, repository
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
    occurred_at: str | None = None,
) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = repository.get_or_create_project(project)
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
    last_session = repository.get_latest_session_summary(proj["id"])
    observations = repository.get_recent_observations(
        project_id=proj["id"],
        limit=limit,
        workspace_id=proj.get("workspace_id"),
    )
    return json.dumps(
        {"last_session": last_session, "observations": observations},
        default=str,
    )


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
def register_path(path: str, workspace: str) -> str:
    _ensure()
    result = repository.register_path(path, workspace)
    return json.dumps(result, default=str)


@handle_errors
def resolve_path(path: str) -> str:
    _ensure()
    ws = repository.resolve_path(path)
    if ws:
        return json.dumps(
            {"resolved": True, "workspace": ws}, default=str
        )
    return json.dumps({"resolved": False, "path": path})


@handle_errors
def delete_workspace(workspace: str) -> str:
    _ensure()
    deleted = repository.delete_workspace(workspace)
    if deleted:
        return json.dumps({"deleted": True, "workspace": workspace})
    return json.dumps({"deleted": False, "error": f"Workspace '{workspace}' not found"})


@handle_errors
def rename_workspace(old_name: str, new_name: str) -> str:
    _ensure()
    result = repository.rename_workspace(old_name, new_name)
    if result:
        return json.dumps(result, default=str)
    return json.dumps({"error": f"Workspace '{old_name}' not found"})


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


@handle_errors
def search_similar(project: str, query: str, limit: int = 10) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = repository.get_or_create_project(project)
    embedding = generate_embedding(query)
    results = repository.search_similar(
        project_id=proj["id"],
        embedding=embedding,
        limit=limit,
        workspace_id=proj.get("workspace_id"),
    )
    return json.dumps(results, default=str)


@handle_errors
def search_hybrid(project: str, query: str, limit: int = 10) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = repository.get_or_create_project(project)
    embedding = generate_embedding(query)
    results = repository.search_hybrid(
        project_id=proj["id"],
        query=query,
        embedding=embedding,
        limit=limit,
        workspace_id=proj.get("workspace_id"),
    )
    return json.dumps(results, default=str)


@handle_errors
def purge_workspace(
    workspace: str,
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

    counts = repository.count_workspace_resources(workspace)
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

    result = repository.purge_workspace_data(workspace, mode=mode)

    if purge_graph:
        graph_result = graph_repository.purge_all_graph()
        result["graph_nodes_deleted"] = graph_result["nodes_deleted"]
        result["graph_edges_deleted"] = graph_result["edges_deleted"]
    result["dry_run"] = False
    return json.dumps(result, default=str)


@handle_errors
def backfill_embeddings(project: str) -> str:
    _ensure()
    from memodi.embeddings import generate_embedding

    proj = repository.get_or_create_project(project)
    observations = repository.get_observations_without_embedding(proj["id"])
    count = 0
    for obs in observations:
        embedding = generate_embedding(f"{obs['title']} {obs['content']}")
        repository.update_observation_embedding(obs["id"], embedding)
        count += 1
    return json.dumps({"backfilled": count, "project": project})
