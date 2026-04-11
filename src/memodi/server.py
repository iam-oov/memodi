from mcp.server.fastmcp import FastMCP

from memodi.tools import memory, workflow
from memodi.tools.system import ping, status

mcp = FastMCP("memodi", host="0.0.0.0", port=8787)


@mcp.tool()
def memodi_ping() -> str:
    """Check if memodi is alive. Returns 'pong'."""
    return ping()


@mcp.tool()
def memodi_status() -> str:
    """Check memodi health: server, database, and loaded extensions."""
    return status()


@mcp.tool()
def memodi_save(
    project: str,
    title: str,
    content: str,
    type: str,
    topic_key: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Save an observation. If topic_key exists for the project, upserts it."""
    return memory.save(project, title, content, type, topic_key, metadata)


@mcp.tool()
def memodi_search(
    project: str,
    query: str,
    type: str | None = None,
    limit: int = 10,
) -> str:
    """Full-text search observations for a project."""
    return memory.search(project, query, type, limit)


@mcp.tool()
def memodi_context(project: str, limit: int = 20) -> str:
    """Return the most recent observations for a project."""
    return memory.context(project, limit)


@mcp.tool()
def memodi_list_projects() -> str:
    """List all known projects."""
    return memory.list_projects()


@mcp.tool()
def memodi_search_global(query: str, type: str | None = None, limit: int = 10) -> str:
    """Search across ALL workspaces for similar decisions or patterns."""
    return memory.search_global(query, type, limit)


@mcp.tool()
def memodi_list_workspaces() -> str:
    """List all workspaces with their project count."""
    return memory.list_workspaces()


@mcp.tool()
def memodi_link_project(project: str, workspace: str) -> str:
    """Link a project to a workspace. Creates the workspace if it doesn't exist."""
    return memory.link_project(project, workspace)


@mcp.tool()
def memodi_check_workspace(project: str) -> str:
    """Check if a project has a workspace. Lists available ones if not."""
    return memory.check_workspace(project)


@mcp.tool()
def memodi_search_similar(project: str, query: str, limit: int = 10) -> str:
    """Semantic search — find observations by meaning, not just keywords."""
    return memory.search_similar(project, query, limit)


@mcp.tool()
def memodi_search_hybrid(project: str, query: str, limit: int = 10) -> str:
    """Hybrid search — combines keyword (BM25) and semantic (vector) via RRF."""
    return memory.search_hybrid(project, query, limit)


@mcp.tool()
def memodi_backfill(project: str) -> str:
    """Generate embeddings for observations that don't have one yet."""
    return memory.backfill_embeddings(project)


@mcp.tool()
def memodi_plan(project: str, name: str, objective: str) -> str:
    """Start a new workflow plan. Returns active workflow if one exists."""
    return workflow.plan(project, name, objective)


@mcp.tool()
def memodi_update_plan(
    workflow_id: str,
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> str:
    """Update acceptance criteria and tasks for a workflow in 'plan' phase."""
    return workflow.update_plan(workflow_id, acceptance_criteria, tasks)


@mcp.tool()
def memodi_approve_plan(workflow_id: str, notes: str | None = None) -> str:
    """Approve the plan and move the workflow to 'apply' phase."""
    return workflow.approve_plan(workflow_id, notes)


@mcp.tool()
def memodi_apply_done(workflow_id: str, notes: str | None = None) -> str:
    """Mark implementation as done, move to 'verify' phase."""
    return workflow.apply_done(workflow_id, notes)


@mcp.tool()
def memodi_verify(
    workflow_id: str,
    result: dict,
    passed: bool,
    notes: str | None = None,
) -> str:
    """Record verification result. 'unify' if passed, 'apply' if failed."""
    return workflow.verify(workflow_id, result, passed, notes)


@mcp.tool()
def memodi_unify(workflow_id: str, summary: str, notes: str | None = None) -> str:
    """Close the loop: record summary and mark workflow as completed."""
    return workflow.unify(workflow_id, summary, notes)


@mcp.tool()
def memodi_progress(project: str) -> str:
    """Show the active workflow state for a project."""
    return workflow.progress(project)


@mcp.tool()
def memodi_task_update(
    workflow_id: str,
    task_index: int,
    status: str,
    notes: str | None = None,
) -> str:
    """Update the status of a specific task in the workflow."""
    return workflow.task_update(workflow_id, task_index, status, notes)


def main():
    import sys

    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
