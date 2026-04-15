from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as MCPTool

from memodi.tools import graph, memory, session, workflow
from memodi.tools.system import ping, status, version

# Tools always loaded into Claude's context. All others are deferred
# and loaded on-demand via ToolSearch to save tokens.
CORE_TOOLS: set[str] = {
    "memodi_save",
    "memodi_search_hybrid",
    "memodi_context",
    "memodi_check_workspace",
    "memodi_resolve_path",
    "memodi_link_project",
    "memodi_ping",
    "memodi_relate",
}

mcp = FastMCP(
    "memodi",
    host="0.0.0.0",
    port=8787,
    instructions=(
        "memodi provides 8 core tools (always available) and "
        "27 deferred tools (load via ToolSearch). Core: "
        "memodi_save, memodi_search_hybrid, memodi_context, "
        "memodi_check_workspace, memodi_resolve_path, "
        "memodi_link_project, memodi_ping, memodi_relate."
    ),
)


# --- System ---


@mcp.tool()
def memodi_ping() -> str:
    """Quick connectivity check — returns 'pong'.

    Use at session start or when memory tools seem unresponsive.
    """
    return ping()


@mcp.tool()
def memodi_status() -> str:
    """Server health check — reports database, pgvector, and AGE status.

    Use when diagnosing connection issues or verifying the stack
    is operational.
    """
    return status()


@mcp.tool()
def memodi_version() -> str:
    """Returns the deployed server version.

    Use to verify production matches expected release.
    """
    return version()


# --- Memory (core) ---


@mcp.tool()
def memodi_save(
    project: str,
    title: str,
    content: str,
    type: str,
    topic_key: str | None = None,
    metadata: dict | None = None,
    occurred_at: str | None = None,
) -> str:
    """Persist a decision, discovery, bugfix, pattern, config,
    preference, architecture, or session summary.

    Call PROACTIVELY after any significant event — don't wait
    to be asked. Use topic_key to update evolving topics
    (same key = upsert).

    Pass occurred_at (ISO 8601, e.g. '2025-08-12T14:00:00Z') ONLY
    when importing historical content that happened in the past —
    e.g. migrating notes from legacy .md files. Ordering by
    recency uses COALESCE(occurred_at, created_at), so omitting
    it means "this happened now".
    """
    return memory.save(
        project, title, content, type, topic_key, metadata, occurred_at
    )


@mcp.tool()
def memodi_search(
    project: str,
    query: str,
    type: str | None = None,
    limit: int = 10,
) -> str:
    """Keyword search across saved observations.

    Use when you need exact term matches. For meaning-based
    search, prefer memodi_search_hybrid.
    """
    return memory.search(project, query, type, limit)


@mcp.tool()
def memodi_context(project: str, limit: int = 20) -> str:
    """Load recent decisions, discoveries, patterns, and session
    summaries for a project.

    This is the primary orientation tool — your FIRST call when
    joining a project, before git log, TODO files, or READMEs.
    memodi holds cross-session context that doesn't exist anywhere
    else: what was decided, what was learned, what was tried.
    """
    return memory.context(project, limit)


@mcp.tool()
def memodi_list_projects() -> str:
    """List all known projects with their workspace assignments.

    Use to see what memodi is tracking or to help the user
    pick a project.
    """
    return memory.list_projects()


@mcp.tool()
def memodi_search_global(
    query: str, type: str | None = None, limit: int = 10
) -> str:
    """Cross-workspace search — find decisions, patterns, or
    discoveries from ANY project.

    Use when the user asks 'have we solved this before?' or you
    suspect prior art in another repo.
    """
    return memory.search_global(query, type, limit)


# --- Workspace management ---


@mcp.tool()
def memodi_list_workspaces() -> str:
    """List all workspaces and how many projects each contains.

    Use during workspace onboarding or when the user asks what
    workspaces exist.
    """
    return memory.list_workspaces()


@mcp.tool()
def memodi_link_project(project: str, workspace: str) -> str:
    """Link a project to a workspace (creates it if new).

    Use after resolve_path returns unlinked, or when the user
    confirms which workspace a project belongs to.
    """
    return memory.link_project(project, workspace)


@mcp.tool()
def memodi_register_path(path: str, workspace: str) -> str:
    """Map a filesystem path to a workspace so future sessions
    auto-detect it.

    Use after linking a project to register its directory for
    resolve_path lookups.
    """
    return memory.register_path(path, workspace)


@mcp.tool()
def memodi_resolve_path(path: str) -> str:
    """Resolve a filesystem path (e.g. cwd) to its workspace.

    Use at session start to detect which workspace the user is
    in. Returns resolved: true/false.
    """
    return memory.resolve_path(path)


@mcp.tool()
def memodi_check_workspace(project: str) -> str:
    """Check if a project is linked to a workspace.

    If not, returns available workspaces for the user to choose
    from. Use for workspace onboarding.
    """
    return memory.check_workspace(project)


@mcp.tool()
def memodi_delete_workspace(workspace: str) -> str:
    """Delete a workspace and unlink all its projects.

    Destructive — ask the user for confirmation first.
    """
    return memory.delete_workspace(workspace)


@mcp.tool()
def memodi_purge_workspace(
    workspace: str,
    mode: str = "medium",
    purge_graph: bool = False,
    dry_run: bool = True,
) -> str:
    """Wipe workspace data for dev loops (e.g. re-importing .md files).

    HIGHLY destructive — defaults to dry_run=True. Inspect the output
    first, then pass dry_run=False to execute.

    mode='medium': deletes observations, workflows, workflow_transitions,
    sessions. Preserves projects, workspace, and workspace_paths so you
    can re-import into the same structure.
    mode='hard': also deletes projects, workspace, and workspace_paths.

    purge_graph=True ALSO wipes the entire knowledge graph (global, not
    scoped to this workspace). Only enable if the graph exclusively
    contains data for this workspace.

    ALWAYS confirm with the user before passing dry_run=False.
    """
    return memory.purge_workspace(
        workspace, mode, purge_graph, dry_run
    )


@mcp.tool()
def memodi_rename_workspace(
    old_name: str, new_name: str
) -> str:
    """Rename a workspace.

    All linked projects and paths stay connected under the
    new name.
    """
    return memory.rename_workspace(old_name, new_name)


# --- Search (deferred) ---


@mcp.tool()
def memodi_search_similar(
    project: str, query: str, limit: int = 10
) -> str:
    """Semantic (vector) search — finds observations by meaning
    even when exact words differ.

    Use when keyword search returns nothing or the user's query
    is conceptual ('how did we handle auth?').
    """
    return memory.search_similar(project, query, limit)


@mcp.tool()
def memodi_search_hybrid(
    project: str, query: str, limit: int = 10
) -> str:
    """Best-of-both search — combines keyword (BM25) and
    semantic (vector) via Reciprocal Rank Fusion.

    DEFAULT search tool: use this unless you specifically need
    only keyword or only semantic results.
    """
    return memory.search_hybrid(project, query, limit)


@mcp.tool()
def memodi_backfill(project: str) -> str:
    """Generate vector embeddings for old observations that
    lack them.

    Use after importing data or if semantic search returns
    incomplete results.
    """
    return memory.backfill_embeddings(project)


# --- Workflow ---


@mcp.tool()
def memodi_plan(
    project: str, name: str, objective: str
) -> str:
    """Start a structured plan to track multi-step work — test
    suites, refactors, feature implementations, checklists.

    Use when the user wants to break work into trackable tasks
    with a plan→apply→verify→unify cycle. Returns the active
    workflow if one already exists.
    """
    return workflow.plan(project, name, objective)


@mcp.tool()
def memodi_update_plan(
    workflow_id: str,
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> str:
    """Define what 'done' looks like — set acceptance criteria
    and task list for a plan.

    Call after memodi_plan to flesh out the work before
    approving it.
    """
    return workflow.update_plan(
        workflow_id, acceptance_criteria, tasks
    )


@mcp.tool()
def memodi_approve_plan(
    workflow_id: str, notes: str | None = None
) -> str:
    """Lock in the plan and start implementation.

    Moves workflow from 'plan' to 'apply' phase. Call after the
    user has reviewed and agreed with the plan.
    """
    return workflow.approve_plan(workflow_id, notes)


@mcp.tool()
def memodi_apply_done(
    workflow_id: str, notes: str | None = None
) -> str:
    """Signal that implementation is complete, ready for
    verification.

    Moves workflow from 'apply' to 'verify' phase. Call after
    all tasks are done.
    """
    return workflow.apply_done(workflow_id, notes)


@mcp.tool()
def memodi_verify(
    workflow_id: str,
    result: dict,
    passed: bool,
    notes: str | None = None,
) -> str:
    """Record whether the implementation passes verification.

    If passed → moves to 'unify'. If failed → returns to
    'apply' for fixes. Include what was checked and what failed.
    """
    return workflow.verify(workflow_id, result, passed, notes)


@mcp.tool()
def memodi_unify(
    workflow_id: str, summary: str, notes: str | None = None
) -> str:
    """Close the loop — record what was accomplished and mark
    the workflow as done.

    The final step in plan→apply→verify→unify. Include a
    summary of outcomes and learnings.
    """
    return workflow.unify(workflow_id, summary, notes)


@mcp.tool()
def memodi_progress(project: str) -> str:
    """Show active workflow — current phase, pending tasks, and
    acceptance criteria progress.

    This is the single source of truth for tracked work. Check
    this BEFORE git log, TODO files, or issue trackers when the
    question involves pending, remaining, or in-progress work —
    memodi tracks structured plans with phases and tasks that
    don't exist anywhere else.
    """
    return workflow.progress(project)


@mcp.tool()
def memodi_task_update(
    workflow_id: str,
    task_index: int,
    status: str,
    notes: str | None = None,
) -> str:
    """Mark a task as in_progress, done, or blocked.

    Use during the 'apply' phase to track progress through
    individual tasks in the plan.
    """
    return workflow.task_update(
        workflow_id, task_index, status, notes
    )


# --- Knowledge graph ---


@mcp.tool()
def memodi_relate(
    from_type: str,
    from_name: str,
    to_type: str,
    to_name: str,
    relation: str,
    properties: dict | None = None,
    valid_at: str | None = None,
) -> str:
    """Create a relationship in the knowledge graph
    (e.g. repo-a DEPENDS_ON repo-b).

    Relationships are temporal: valid_at is set automatically
    (or pass ISO 8601). Re-creating the same relationship
    invalidates the old one and creates a new version.
    """
    return graph.relate(
        from_type,
        from_name,
        to_type,
        to_name,
        relation,
        properties,
        valid_at,
    )


@mcp.tool()
def memodi_dependencies(name: str) -> str:
    """Show upstream and downstream dependencies for a node.

    Use when the user asks 'what does X depend on?' or 'what
    uses X?' to understand coupling.
    """
    return graph.dependencies(name)


@mcp.tool()
def memodi_impact(name: str, max_depth: int = 5) -> str:
    """Transitive impact analysis — 'what breaks if I change X?'

    Walks the dependency graph up to max_depth. Use before
    refactors, breaking changes, or to assess blast radius.
    """
    return graph.impact_analysis(name, max_depth)


@mcp.tool()
def memodi_graph_overview() -> str:
    """Full map of the knowledge graph — all nodes and
    relationships.

    Use to understand the system topology or when the user asks
    'how is everything connected?'
    """
    return graph.graph_overview()


@mcp.tool()
def memodi_remove_relation(
    from_name: str, to_name: str, relation: str
) -> str:
    """Soft-delete a relationship — marks it with invalid_at
    but keeps history.

    Use when a dependency is removed but you want to preserve
    the audit trail.
    """
    return graph.remove_relation(from_name, to_name, relation)


@mcp.tool()
def memodi_delete_relation(
    from_name: str, to_name: str, relation: str
) -> str:
    """Permanently remove a relationship from the graph — no
    history kept.

    Use only to fix mistakes. Prefer memodi_remove_relation for
    normal cleanup.
    """
    return graph.delete_relation(from_name, to_name, relation)


# --- Sessions ---


@mcp.tool()
def memodi_session_start(project: str) -> str:
    """Begin tracking a work session — all subsequent
    memodi_save calls auto-attach to this session.

    Closes any previous active session. Use at the start of
    every conversation.
    """
    return session.session_start(project)


@mcp.tool()
def memodi_session_end(project: str, summary: str) -> str:
    """End the current session with a structured summary
    (Goal / Accomplished / Next Steps).

    Call before the conversation ends so the next session can
    pick up where this one left off.
    """
    return session.session_end(project, summary)


# --- Deferred tool loading ---


async def _list_tools_with_deferred() -> list[MCPTool]:
    """Override list_tools to mark non-core tools with
    defer_loading=True.

    Claude Code reads this field and excludes deferred tools
    from the initial context, making them discoverable via
    ToolSearch on demand.
    """
    tools = mcp._tool_manager.list_tools()
    result = []
    for info in tools:
        tool = MCPTool(
            name=info.name,
            title=info.title,
            description=info.description,
            inputSchema=info.parameters,
            outputSchema=info.output_schema,
            annotations=info.annotations,
            icons=info.icons,
            _meta=info.meta,
        )
        if info.name not in CORE_TOOLS:
            tool.defer_loading = True
        result.append(tool)
    return result


mcp._mcp_server.list_tools()(_list_tools_with_deferred)


def main():
    import sys

    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
