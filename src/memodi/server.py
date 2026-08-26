import json

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import Tool as MCPTool

from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema
from memodi.tools import graph, memory, session, workflow
from memodi.tools.context import client_context
from memodi.tools.errors import NotAuthenticatedError
from memodi.tools.scope import require_user
from memodi.tools.system import ping, status, version
from memodi.web.hooks import (
    post_capture,
    post_digest,
    post_prompt_search,
    post_session_close,
    post_session_start,
)
from memodi.web.login import get_login, get_oauth_callback

# Tools always loaded into Claude's context. All others are deferred
# and loaded on-demand via ToolSearch to save tokens.
CORE_TOOLS: set[str] = {
    "memodi_save",
    "memodi_search_hybrid",
    "memodi_context",
    "memodi_workspace_start",
    "memodi_ping",
    "memodi_relate",
}

mcp = FastMCP(
    "memodi",
    host=settings.host,
    port=8787,
    instructions=(
        "memodi provides 6 core tools (always available) and "
        "deferred tools (load via ToolSearch). Core: "
        "memodi_save, memodi_search_hybrid, memodi_context, "
        "memodi_workspace_start, memodi_ping, memodi_relate. "
        "Every project-scoped tool requires path (the caller's cwd) — "
        "an unregistered path returns a not_started error naming the fix."
    ),
)


# --- Web (public, no MCP auth by design) ---

mcp.custom_route("/login", methods=["GET"])(get_login)
mcp.custom_route("/oauth/callback", methods=["GET"])(get_oauth_callback)

# --- Web (hooks — plain HTTP counterpart to MCP, for Claude Code shell hooks) ---

mcp.custom_route("/hooks/session-start", methods=["POST"])(post_session_start)
mcp.custom_route("/hooks/session-close", methods=["POST"])(post_session_close)
mcp.custom_route("/hooks/capture", methods=["POST"])(post_capture)
mcp.custom_route("/hooks/prompt-search", methods=["POST"])(post_prompt_search)
mcp.custom_route("/hooks/digest", methods=["POST"])(post_digest)


def _caller(ctx: Context) -> dict | str:
    """Resolve (user_id, machine) from request headers.

    Returns a JSON error string on auth failure, otherwise a dict with
    user_id and machine.
    """
    cc = client_context(ctx)
    try:
        user = require_user(cc["api_key"])
    except NotAuthenticatedError as e:
        return json.dumps({"error": str(e), "type": "not_authenticated"})
    return {"user_id": user["id"], "machine": cc["machine"]}


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
    ctx: Context,
    path: str,
    title: str,
    content: str,
    type: str,
    project: str | None = None,
    topic_key: str | None = None,
    metadata: dict | None = None,
    occurred_at: str | None = None,
    supersedes: str | list[str] | None = None,
    affects: list[str] | None = None,
) -> str:
    """Persist an observation to memory.

    type must be one of: decision, discovery, bugfix, pattern,
    config, preference, architecture, or session (an
    end-of-session summary).

    Call PROACTIVELY after any significant event — don't wait
    to be asked. path is the caller's cwd — memodi resolves it to a
    workspace registered via memodi_workspace_start. Use topic_key to
    update evolving topics (same key = upsert).

    Session attribution is best-effort: the observation attaches to
    whichever session for this project is currently newest and active.
    Concurrent active sessions per project are legal (e.g. two Claude Code
    windows in the same folder, each tagged with its own client_session_id)
    — a save from one window can attach to the other window's session if
    that one started more recently. This never affects search, context, or
    the saved content itself; only the session_id metadata (currently
    write-only — no query reads it) can point at the wrong window.

    Pass occurred_at (ISO 8601, e.g. '2025-08-12T14:00:00Z') ONLY
    when importing historical content that happened in the past —
    e.g. migrating notes from legacy .md files. Ordering by
    recency uses COALESCE(occurred_at, created_at), so omitting
    it means "this happened now".

    Pass supersedes=<old-observation-id> when this observation
    replaces one whose topic_key you don't know — the old one
    stops surfacing in context/search but stays readable via
    memodi_get_observation (audit trail). A bad id never fails the
    save; for a single id, check supersedes_applied plus
    supersedes_reason and supersedes_error in the response. Reasons
    are discriminated (invalid_id, self, not_found, already_deleted,
    already_superseded, failed) so you can tell whether retrying
    would help — 'self' means a topic_key upsert or duplicate
    merge already corrected that same row, so do NOT retry.

    supersedes also accepts a list of string ids, to distill several
    scattered same-theme observations into this one — the audit chain
    then shows all of them via memodi_get_observation. The list form
    acks differently: supersedes_applied is true only when every id
    applied, and when one did not, supersedes_results maps each id
    string you sent to "applied" or its reason (no supersedes_reason,
    no supersedes_error). Duplicates are deduped, equivalent spellings
    of one uuid included. Over 20 ids the whole list is refused —
    NOTHING is applied, there is no supersedes_results, and
    supersedes_reason is "too_many", so split the consolidation into
    several saves. Every element must be a string; a non-string element
    is rejected at this boundary before anything is saved. The save
    itself always persists.

    Pass affects=["repo-a", "repo-b"] when the work spans several
    repos in this workspace: ONE observation that searching from any
    of those projects will find, instead of a cross-repo contract
    filed only under whichever repo happened to be the cwd. Use the
    directory names of the repos you actually touched — do not invent
    names. Names with no project yet are created and listed back as
    projects_created, so a typo shows up immediately instead of
    silently swallowing the memory. On a topic_key upsert, omitting
    affects keeps the stored list while affects=[] clears it. Over 20
    names the list is refused with affects_reason "too_many" and the
    observation still saves. Primary ownership always stays with path
    — affects widens who can find an observation, it never moves it,
    and it never makes another project's topic_key upsertable from
    here.

    The response may carry a `related` list — up to 3 existing
    observations from anywhere in the workspace very similar to
    this one (id, title, topic_key, project, similarity, never
    content); absence means nothing surfaced or the lookup was
    unavailable. Read an entry with memodi_get_observation before
    correcting it, and reuse its topic_key only if its project is
    yours — upsert is project-scoped, so a reused key forks the
    knowledge instead of correcting it.

    Write [[other-topic-key]] anywhere in content to link this
    observation to another one in the knowledge graph — no separate
    tool call needed. Only takes effect when THIS save also has its own
    topic_key: the link is stored as topic_key -> other-topic-key, so an
    observation with no topic_key has nothing to attach it to. A key may
    contain letters, digits, `.`, `_`, `/`, `-` (no spaces or quotes) and
    up to 128 characters; anything else is silently skipped rather than
    failing the save. Up to 20 links per save; a link to yourself is
    dropped. The response may carry a `links` object: `{"created":
    [...], "invalidated": [...], "skipped_invalid": N}` when something
    was found or changed, `{"skipped": "no_topic_key"}` /
    `{"skipped": "invalid_topic_key"}` when [[...]] syntax is present
    but unusable, or the key absent entirely when there was nothing to
    report. Use memodi_dependencies or memodi_impact with path to query
    these edges later.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.save(
        path,
        caller["user_id"],
        caller["machine"],
        title,
        content,
        type,
        project,
        topic_key,
        metadata,
        occurred_at,
        supersedes,
        affects=affects,
    )


@mcp.tool()
def memodi_search(
    ctx: Context,
    path: str,
    query: str,
    project: str | None = None,
    type: str | None = None,
    limit: int = 10,
) -> str:
    """Keyword search across saved observations.

    Use when you need exact term matches. For meaning-based
    search, prefer memodi_search_hybrid.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.search(
        path, caller["user_id"], caller["machine"], query, project, type, limit
    )


@mcp.tool()
def memodi_context(
    ctx: Context, path: str, project: str | None = None, limit: int = 20
) -> str:
    """Load the last session summary plus pointers to recent
    observations (id, type, title, topic_key, project, dates — no
    content) for a project.

    This is the primary orientation tool — your FIRST call when
    joining a project, before git log, TODO files, or READMEs.
    memodi holds cross-session context that doesn't exist anywhere
    else: what was decided, what was learned, what was tried.

    Observations arrive as pointers to keep the once-per-session
    load cheap. Read any that matters with memodi_get_observation(id),
    or search with memodi_search_hybrid.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.context(path, caller["user_id"], caller["machine"], project, limit)


@mcp.tool()
def memodi_list_projects(ctx: Context) -> str:
    """List all of the caller's projects with their workspace assignments.

    Use to see what memodi is tracking or to help the user
    pick a project.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.list_projects(caller["user_id"])


@mcp.tool()
def memodi_logout(ctx: Context) -> str:
    """Revoke this machine's api key server-side, ending its access
    immediately.

    Use when the user wants to log out, switch to a different account
    on this machine, or test the login flow from scratch. The calling
    key dies immediately — any further MCP calls this session makes
    afterward will return not_authenticated.
    """
    cc = client_context(ctx)
    try:
        user = require_user(cc["api_key"])
    except NotAuthenticatedError as e:
        return json.dumps({"error": str(e), "type": "not_authenticated"})
    auth_repository.revoke_api_key(cc["api_key"])
    return json.dumps({"revoked": True, "email": user["email"]})


@mcp.tool()
def memodi_search_global(
    ctx: Context, query: str, type: str | None = None, limit: int = 10
) -> str:
    """Cross-workspace search — find decisions, patterns, or
    discoveries from ANY of the caller's own projects.

    Use when the user asks 'have we solved this before?' or you
    suspect prior art in another repo.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.search_global(caller["user_id"], query, type, limit)


@mcp.tool()
def memodi_get_observation(ctx: Context, path: str, observation_id: str) -> str:
    """Read one observation by id — the audit path for corrections.

    An observation replaced via memodi_save(supersedes=...) stops surfacing
    in context and search but stays readable here, with superseded_by
    pointing at its replacement — that's how the 'why did we change this?'
    chain is followed. The chain also walks the other way: reading the
    replacement shows supersedes, the list of ids it replaced, most-recent
    first. That list is absent when the observation replaced nothing or
    every predecessor was deleted. Deleted observations are hidden.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.get_observation(
        path, caller["user_id"], caller["machine"], observation_id
    )


@mcp.tool()
def memodi_delete(ctx: Context, path: str, observation_id: str) -> str:
    """Soft-delete a junk, test, or wrong observation — sets deleted_at
    but keeps the row.

    Prefer memodi_save with a matching topic_key, or its supersedes
    parameter, when the goal is to correct an observation — this tool
    is for cleanup, not corrections. Reversible at the DB level.
    Idempotent: deleting an already-deleted observation still acks
    success.

    Deleting an observation that superseded another one is the undo of
    that correction: the observation it replaced surfaces again (the
    ack reports how many via resurfaced).
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.delete(path, caller["user_id"], caller["machine"], observation_id)


# --- Workspace management ---


@mcp.tool()
def memodi_list_workspaces(ctx: Context) -> str:
    """List all of the caller's workspaces and how many projects each contains.

    Use during workspace onboarding or when the user asks what
    workspaces exist.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.list_workspaces(caller["user_id"])


@mcp.tool()
def memodi_workspace_start(ctx: Context, path: str, workspace: str) -> str:
    """Register a folder as a workspace boundary on this machine.

    This is the ONLY onboarding gate — memodi is inert for unregistered
    paths. Register the folder the user is standing in; every path under it
    resolves here by longest-prefix, and each repo below becomes its own
    project. A deeper registration wins over a broader one without touching
    it, which is how a sub-tree gets carved out into its own workspace.

    Several paths may point at ONE workspace — more than one on this machine,
    more on others. That is how memory is shared: pass the name EXACTLY as
    returned by memodi_list_workspaces to attach instead of creating a new
    one. Case and surrounding whitespace are folded, nothing else is.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.workspace_start(path, workspace, caller["user_id"], caller["machine"])


@mcp.tool()
def memodi_list_paths(ctx: Context) -> str:
    """List every path the caller has registered, on every machine.

    Shows machine, path, and the workspace each resolves to — the inventory
    memodi_list_workspaces and memodi_list_projects do not carry. Use it
    before repointing or registering, to see what a new path would collide
    with or shadow (longest prefix wins).
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.list_paths(caller["user_id"])


@mcp.tool()
def memodi_workspace_repoint(ctx: Context, path: str, workspace: str) -> str:
    """Move this machine's registration of `path` to a different workspace.

    The repair tool for a path registered to the wrong workspace — the only
    way to change one registration without deleting a whole workspace.

    Only the ADDRESS moves. Projects and their observations stay where they
    are, so nothing is lost and nothing is silently carried over; use
    memodi_merge_projects afterwards to move the data itself.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.workspace_repoint(
        path, workspace, caller["user_id"], caller["machine"]
    )


@mcp.tool()
def memodi_workspace_forget(ctx: Context, path: str) -> str:
    """Drop this machine's registration of `path`, making it dormant again.

    Workspaces, projects and observations are all left intact — this only
    removes the address, so the path answers not_started until someone runs
    memodi_workspace_start on it again.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.workspace_forget(path, caller["user_id"], caller["machine"])


@mcp.tool()
def memodi_merge_projects(
    ctx: Context,
    source_project_id: str,
    target_project_id: str,
    dry_run: bool = True,
) -> str:
    """Merge one project into another — moves observations, sessions,
    and workflows, then deletes the source project.

    Repair tool for accidental duplicates (e.g. two projects that
    should have been the same one). Both projects must belong to
    workspaces owned by the caller.

    HIGHLY destructive — defaults to dry_run=True. Inspect the output
    first, then pass dry_run=False to execute.

    The dry run reports topic_key_collisions and would_hide: every topic
    key both projects hold, with the row on each side. A colliding source
    observation is moved AND soft-deleted, so the TARGET's version wins and
    the source's stops surfacing anywhere. Read would_hide before executing
    — `hidden_side: "source (newer)"` means the merge would bury the fresher
    of the two, and the merge probably wants to run the other way round.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.merge_projects(
        source_project_id, target_project_id, caller["user_id"], dry_run
    )


@mcp.tool()
def memodi_delete_workspace(ctx: Context, workspace: str) -> str:
    """Delete a workspace and all its projects.

    Destructive — ask the user for confirmation first.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.delete_workspace(workspace, caller["user_id"])


@mcp.tool()
def memodi_purge_workspace(
    ctx: Context,
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
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.purge_workspace(
        workspace, caller["user_id"], mode, purge_graph, dry_run
    )


@mcp.tool()
def memodi_rename_workspace(ctx: Context, old_name: str, new_name: str) -> str:
    """Rename one of the caller's workspaces.

    All linked projects and paths stay connected under the
    new name.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.rename_workspace(old_name, new_name, caller["user_id"])


# --- Search (deferred) ---


@mcp.tool()
def memodi_search_similar(
    ctx: Context, path: str, query: str, project: str | None = None, limit: int = 10
) -> str:
    """Semantic (vector) search — finds observations by meaning
    even when exact words differ.

    Use when keyword search returns nothing or the user's query
    is conceptual ('how did we handle auth?').
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.search_similar(
        path, caller["user_id"], caller["machine"], query, project, limit
    )


@mcp.tool()
def memodi_search_hybrid(
    ctx: Context, path: str, query: str, project: str | None = None, limit: int = 10
) -> str:
    """Best-of-both search — combines keyword (BM25) and
    semantic (vector) via Reciprocal Rank Fusion.

    DEFAULT search tool: use this unless you specifically need
    only keyword or only semantic results.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.search_hybrid(
        path, caller["user_id"], caller["machine"], query, project, limit
    )


@mcp.tool()
def memodi_backfill(ctx: Context, path: str, project: str | None = None) -> str:
    """Generate vector embeddings for old observations that
    lack them.

    Use after importing data or if semantic search returns
    incomplete results.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.backfill_embeddings(
        path, caller["user_id"], caller["machine"], project
    )


@mcp.tool()
def memodi_backfill_links(ctx: Context, path: str, project: str | None = None) -> str:
    """Catch up LINKS_TO edges for observations saved before the
    [[topic-key]] auto-linking feature existed.

    Scans this project for topic_key'd observations whose content
    contains [[topic-key]] wiki-links and reconciles their edges in the
    knowledge graph — the same sync memodi_save runs on every save.
    Idempotent: re-running reports edges_created: 0 once everything is
    caught up.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.backfill_links(path, caller["user_id"], caller["machine"], project)


@mcp.tool()
def memodi_find_consolidation_clusters(
    ctx: Context,
    path: str,
    min_age_days: int = 30,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.75,
    theme: str | None = None,
) -> str:
    """Mechanically detect clusters of similar, aged, live observations
    ripe for a compressed-logbook rollup ("breadcrumbs").

    Read-only and deterministic: reuses the stored embeddings and the
    idx_obs_embedding HNSW index, never re-embeds. Returns evidence
    (members, confidence, reason codes) for the agent to vet before
    writing a compressed observation and superseding the members — it
    never writes anything itself. theme narrows the eligible set via
    keyword search; omit it to see every workspace-wide cluster.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return memory.find_consolidation_clusters(
        path,
        caller["user_id"],
        caller["machine"],
        min_age_days,
        min_cluster_size,
        similarity_threshold,
        theme,
    )


# --- Workflow ---


@mcp.tool()
def memodi_plan(
    ctx: Context, path: str, name: str, objective: str, project: str | None = None
) -> str:
    """Start a structured plan to track multi-step work — test
    suites, refactors, feature implementations, checklists.

    Use when the user wants to break work into trackable tasks
    with a plan→apply→verify→unify cycle. Returns the active
    workflow if one already exists.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return workflow.plan(
        path, caller["user_id"], caller["machine"], name, objective, project
    )


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
    return workflow.update_plan(workflow_id, acceptance_criteria, tasks)


@mcp.tool()
def memodi_approve_plan(workflow_id: str, notes: str | None = None) -> str:
    """Lock in the plan and start implementation.

    Moves workflow from 'plan' to 'apply' phase. Call after the
    user has reviewed and agreed with the plan.
    """
    return workflow.approve_plan(workflow_id, notes)


@mcp.tool()
def memodi_apply_done(workflow_id: str, notes: str | None = None) -> str:
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
def memodi_unify(workflow_id: str, summary: str, notes: str | None = None) -> str:
    """Close the loop — record what was accomplished and mark
    the workflow as done.

    The final step in plan→apply→verify→unify. Include a
    summary of outcomes and learnings.
    """
    return workflow.unify(workflow_id, summary, notes)


@mcp.tool()
def memodi_progress(ctx: Context, path: str, project: str | None = None) -> str:
    """Show active workflow — current phase, pending tasks, and
    acceptance criteria progress.

    This is the single source of truth for tracked work. Check
    this BEFORE git log, TODO files, or issue trackers when the
    question involves pending, remaining, or in-progress work —
    memodi tracks structured plans with phases and tasks that
    don't exist anywhere else.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return workflow.progress(path, caller["user_id"], caller["machine"], project)


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
    return workflow.task_update(workflow_id, task_index, status, notes)


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
def memodi_dependencies(ctx: Context, name: str, path: str | None = None) -> str:
    """Show upstream and downstream dependencies for a node.

    Use when the user asks 'what does X depend on?' or 'what
    uses X?' to understand coupling.

    Pass path (the caller's cwd) to also get links_to/linked_from — LINKS_TO
    edges auto-created from [[topic-key]] wiki-links in saved content,
    scoped to that workspace. Omit path for the exact payload this tool
    always returned (DEPENDS_ON only).
    """
    if path is None:
        return graph.dependencies(name)
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return graph.dependencies(name, caller["user_id"], caller["machine"], path)


@mcp.tool()
def memodi_impact(
    ctx: Context, name: str, max_depth: int = 5, path: str | None = None
) -> str:
    """Transitive impact analysis — 'what breaks if I change X?'

    Walks the dependency graph up to max_depth. Use before
    refactors, breaking changes, or to assess blast radius.

    Pass path (the caller's cwd) to also traverse LINKS_TO edges
    (workspace-scoped Topic nodes auto-created from [[topic-key]]
    wiki-links) alongside DEPENDS_ON. Omit path for the exact behavior
    this tool always had.
    """
    if path is None:
        return graph.impact_analysis(name, max_depth)
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return graph.impact_analysis(
        name, max_depth, caller["user_id"], caller["machine"], path
    )


@mcp.tool()
def memodi_graph_overview() -> str:
    """Full map of the knowledge graph — all nodes and
    relationships.

    Use to understand the system topology or when the user asks
    'how is everything connected?'
    """
    return graph.graph_overview()


@mcp.tool()
def memodi_remove_relation(from_name: str, to_name: str, relation: str) -> str:
    """Soft-delete a relationship — marks it with invalid_at
    but keeps history.

    Use when a dependency is removed but you want to preserve
    the audit trail.
    """
    return graph.remove_relation(from_name, to_name, relation)


@mcp.tool()
def memodi_delete_relation(from_name: str, to_name: str, relation: str) -> str:
    """Permanently remove a relationship from the graph — no
    history kept.

    Use only to fix mistakes. Prefer memodi_remove_relation for
    normal cleanup.
    """
    return graph.delete_relation(from_name, to_name, relation)


# --- Sessions ---


@mcp.tool()
def memodi_session_start(ctx: Context, path: str, project: str | None = None) -> str:
    """Begin tracking a work session — all subsequent
    memodi_save calls auto-attach to this session.

    Closes only the caller's own previous session for this project (an
    untagged one, since this tool never passes client_session_id).
    Concurrent active sessions per project are legal: a hook-opened,
    tagged session for the same project is untouched by this call.

    Under the Claude Code plugin, do NOT call this: its SessionStart hook
    already opens the session over plain HTTP, tagged with the Claude Code
    session id so the SessionEnd hook can close that exact row. Calling
    this tool instead opens an untagged session that no hook can ever
    close by id — a harmless but useless extra row (a later memodi_save
    would then attach to whichever of the two sessions is newest). This
    tool is for MCP clients that ship no such hook.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return session.session_start(path, caller["user_id"], caller["machine"], project)


@mcp.tool()
def memodi_session_end(
    ctx: Context,
    path: str,
    summary: str,
    project: str | None = None,
    client_session_id: str | None = None,
) -> str:
    """End a session with a structured summary
    (Goal / Accomplished / Next Steps).

    Call before the conversation ends so the next session can
    pick up where this one left off. Never fails for lack of a
    session: if no matching session is active, one is created and closed
    on the spot (auto_started: true in the response) so the
    summary is always persisted.

    Pass client_session_id when the SessionStart protocol provided one —
    it targets THIS window's own session instead of whichever session for
    the project happens to be newest, which matters now that concurrent
    sessions per project are legal (multiple Claude Code windows in the
    same folder). OMIT it entirely when you have no id: an empty string is
    not "no id", it means the untagged session, and a value the server
    cannot use (over 256 characters, or carrying NUL) is ignored with
    client_session_id_ignored on the response. Omitted, the behavior is
    unchanged: the newest active session for the project, or an
    auto-started one if none is active. A bad id never costs the summary.

    summary is required: an empty or whitespace-only one is
    rejected with a validation error, because it would still
    satisfy `summary IS NOT NULL` and outrank the last real
    recap the next session reads.
    """
    caller = _caller(ctx)
    if isinstance(caller, str):
        return caller
    return session.session_end(
        path, caller["user_id"], caller["machine"], summary, project, client_session_id
    )


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

    # Fail fast at startup: verify the DB is reachable and migrations apply
    # before serving. Otherwise ensure_schema() would run lazily on the first
    # tool call and a broken deploy would still pass the health check.
    ensure_schema()

    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
