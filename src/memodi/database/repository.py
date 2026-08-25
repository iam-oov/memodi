import hashlib
import json
from datetime import UTC, datetime

from psycopg.errors import InvalidTextRepresentation, UniqueViolation

from memodi.database.connection import get_connection


def _content_hash(title: str, content: str) -> str:
    """SHA-256 hash of title+content for deduplication."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def _normalize_path(path: str) -> str:
    return path.rstrip("/")


def _with_affects(meta: dict, affects: list[str]) -> dict:
    out = dict(meta)
    if affects:
        out["affects"] = affects
    else:
        out.pop("affects", None)
    return out


ALLOWED_TYPES = {
    "decision",
    "bugfix",
    "discovery",
    "pattern",
    "config",
    "preference",
    "architecture",
    "session",
}


def get_or_create_workspace(name: str, owner_user_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM workspaces WHERE name = %s AND owner_user_id = %s",
        (name, owner_user_id),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "INSERT INTO workspaces (name, owner_user_id) VALUES (%s, %s) RETURNING *",
        (name, owner_user_id),
    ).fetchone()
    conn.commit()
    return dict(row)


def _id_list(project_ids: str | list[str] | None) -> list[str]:
    """One id or several, normalized to strings. Single-id callers stay
    untouched, and a psycopg UUID never reaches the array adapter as an
    iterable."""
    if not project_ids:
        return []
    if isinstance(project_ids, list | tuple):
        return [str(value) for value in project_ids]
    return [str(project_ids)]


def _project_scope(
    project_ids: str | list[str] | None,
    project_name: str | None,
    alias: str = "",
    inherited_ids: str | list[str] | None = None,
) -> tuple[str, list]:
    """Predicate narrowing observations to what one folder may read:

    - its own project
    - anything naming it in metadata.affects
    - the workspace root's UNTARGETED memory (inherited_ids), i.e. the
      container knowledge a child inherits. A root observation that DID
      declare affects is addressed to specific repos, so it reaches only
      those — otherwise affects could widen visibility but never narrow it.

    Empty when project_ids is None: the caller sits at the registered root,
    where every project in the workspace is in scope.
    """
    ids = _id_list(project_ids)
    if not ids:
        return "", []
    col = f"{alias}." if alias else ""
    # ponytail: unindexed JSONB scan. Add an index on ((metadata->'affects'))
    # USING GIN if a workspace ever outgrows a sequential filter.
    clauses = [f"{col}project_id = ANY(%s::uuid[])"]
    params: list = [ids]
    if project_name:
        clauses.append(f"{col}metadata->'affects' ? %s")
        params.append(project_name)
    inherited = _id_list(inherited_ids)
    if inherited:
        clauses.append(
            f"({col}project_id = ANY(%s::uuid[])"
            f" AND NOT ({col}metadata ? 'affects'))"
        )
        params.append(inherited)
    return "AND (" + " OR ".join(clauses) + ")", params


def _project_name_scope(
    project_names: list[str] | None,
    affects_name: str | None,
    inherited_names: list[str] | None = None,
) -> tuple[str, list]:
    """Same rule as _project_scope, keyed on project names instead of ids —
    for read paths that must not create the project row.

    Empty when project_names is None: the caller sits at the registered
    workspace root, where every project in the workspace is in scope.
    """
    if not project_names:
        return "", []
    clauses = ["p.name = ANY(%s::text[])"]
    params: list = [project_names]
    if affects_name:
        clauses.append("o.metadata->'affects' ? %s")
        params.append(affects_name)
    if inherited_names:
        clauses.append(
            "(p.name = ANY(%s::text[]) AND NOT (o.metadata ? 'affects'))"
        )
        params.append(inherited_names)
    return "AND (" + " OR ".join(clauses) + ")", params


def get_project_names(workspace_id: str) -> set[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM projects WHERE workspace_id = %s", (workspace_id,)
    ).fetchall()
    return {r["name"] for r in rows}


def get_project_by_name(name: str, workspace_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM projects WHERE name = %s AND workspace_id = %s",
        (name, workspace_id),
    ).fetchone()
    return dict(row) if row else None


def get_or_create_project(name: str, workspace_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM projects WHERE name = %s AND workspace_id = %s",
        (name, workspace_id),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "INSERT INTO projects (name, workspace_id) VALUES (%s, %s) RETURNING *",
        (name, workspace_id),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_project_owner(project_id: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT w.owner_user_id FROM projects p
        JOIN workspaces w ON w.id = p.workspace_id
        WHERE p.id = %s
        """,
        (project_id,),
    ).fetchone()
    return row["owner_user_id"] if row else None


def count_project_resources(project_id: str) -> dict | None:
    conn = get_connection()
    proj_row = conn.execute(
        "SELECT id, name FROM projects WHERE id = %s", (project_id,)
    ).fetchone()
    if not proj_row:
        return None
    observations = conn.execute(
        "SELECT COUNT(*) AS c FROM observations WHERE project_id = %s",
        (project_id,),
    ).fetchone()["c"]
    sessions = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE project_id = %s",
        (project_id,),
    ).fetchone()["c"]
    workflows = conn.execute(
        "SELECT COUNT(*) AS c FROM workflows WHERE project_id = %s",
        (project_id,),
    ).fetchone()["c"]
    return {
        "project": proj_row["name"],
        "observations": observations,
        "sessions": sessions,
        "workflows": workflows,
    }


def resolve_workspace(user_id: str, machine: str, path: str) -> dict | None:
    if machine == "legacy":
        raise ValueError("machine 'legacy' is reserved and cannot be used")
    conn = get_connection()
    normalized_path = _normalize_path(path)
    row = conn.execute(
        """
        SELECT w.*, wp.path AS matched_path FROM workspaces w
        JOIN workspace_paths wp ON wp.workspace_id = w.id
        WHERE w.owner_user_id = %s
          AND wp.machine = %s
          AND (%s = wp.path OR left(%s, length(wp.path) + 1) = wp.path || '/')
        ORDER BY length(wp.path) DESC
        LIMIT 1
        """,
        (user_id, machine, normalized_path, normalized_path),
    ).fetchone()
    return dict(row) if row else None


def workspace_start(user_id: str, machine: str, path: str, workspace_name: str) -> dict:
    if machine == "legacy":
        raise ValueError("machine 'legacy' is reserved and cannot be used")
    conn = get_connection()
    normalized_path = _normalize_path(path)

    existing_path = conn.execute(
        """
        SELECT w.id AS workspace_id, w.name AS workspace_name
        FROM workspace_paths wp
        JOIN workspaces w ON w.id = wp.workspace_id
        WHERE wp.machine = %s AND wp.path = %s AND wp.owner_user_id = %s
        """,
        (machine, normalized_path, user_id),
    ).fetchone()

    if existing_path:
        owned = conn.execute(
            "SELECT id FROM workspaces WHERE owner_user_id = %s AND name = %s",
            (user_id, workspace_name),
        ).fetchone()
        if owned and existing_path["workspace_id"] == owned["id"]:
            return get_or_create_workspace(workspace_name, owner_user_id=user_id)
        conn.rollback()
        raise ValueError(
            f"Path already registered to workspace "
            f"'{existing_path['workspace_name']}'"
        )

    workspace = get_or_create_workspace(workspace_name, owner_user_id=user_id)
    try:
        conn.execute(
            "INSERT INTO workspace_paths (workspace_id, machine, path, owner_user_id) "
            "VALUES (%s, %s, %s, %s)",
            (workspace["id"], machine, normalized_path, user_id),
        )
        conn.commit()
    except UniqueViolation as e:
        conn.rollback()
        raise ValueError("Path already registered on this machine") from e
    return workspace


def list_workspace_paths(user_id: str) -> list[dict]:
    """Every path this owner has registered, on every machine.

    Cross-machine on purpose: planning a move means seeing the registration
    you are about to collide with, including the one on the laptop you are
    not sitting at.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT wp.machine, wp.path, w.name AS workspace, wp.created_at
        FROM workspace_paths wp
        JOIN workspaces w ON w.id = wp.workspace_id
        WHERE wp.owner_user_id = %s
        ORDER BY wp.machine, wp.path
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def workspace_repoint(
    user_id: str, machine: str, path: str, workspace_name: str
) -> dict:
    """Move THIS machine's registration of `path` to another workspace.

    Only the address moves. Projects and observations stay in the workspace
    that owns them — use merge_projects to carry the data over, so an
    accidental repoint can never strand memory.
    """
    if machine == "legacy":
        raise ValueError("machine 'legacy' is reserved and cannot be used")
    conn = get_connection()
    normalized_path = _normalize_path(path)

    current = conn.execute(
        """
        SELECT wp.id, w.id AS workspace_id, w.name AS workspace_name
        FROM workspace_paths wp
        JOIN workspaces w ON w.id = wp.workspace_id
        WHERE wp.machine = %s AND wp.path = %s AND wp.owner_user_id = %s
        """,
        (machine, normalized_path, user_id),
    ).fetchone()

    if current is None:
        conn.rollback()
        raise ValueError(
            f"'{normalized_path}' is not registered on machine '{machine}' — "
            "nothing to repoint. Use memodi_workspace_start to register it."
        )

    target = get_or_create_workspace(workspace_name, owner_user_id=user_id)
    if str(target["id"]) == str(current["workspace_id"]):
        return {
            "path": normalized_path,
            "machine": machine,
            "workspace": workspace_name,
            "previous_workspace": current["workspace_name"],
            "changed": False,
        }

    conn.execute(
        "UPDATE workspace_paths SET workspace_id = %s WHERE id = %s",
        (target["id"], current["id"]),
    )
    conn.commit()
    return {
        "path": normalized_path,
        "machine": machine,
        "workspace": workspace_name,
        "previous_workspace": current["workspace_name"],
        "changed": True,
    }


def workspace_forget(user_id: str, machine: str, path: str) -> dict | None:
    """Drop THIS machine's registration of `path`, leaving every workspace and
    its memory intact — the path simply goes dormant (not_started) again."""
    if machine == "legacy":
        raise ValueError("machine 'legacy' is reserved and cannot be used")
    conn = get_connection()
    normalized_path = _normalize_path(path)
    row = conn.execute(
        """
        DELETE FROM workspace_paths wp
        USING workspaces w
        WHERE w.id = wp.workspace_id
          AND wp.machine = %s AND wp.path = %s AND wp.owner_user_id = %s
        RETURNING w.name AS workspace_name
        """,
        (machine, normalized_path, user_id),
    ).fetchone()
    conn.commit()
    if row is None:
        return None
    return {
        "path": normalized_path,
        "machine": machine,
        "workspace": row["workspace_name"],
    }


def merge_projects(source_project_id: str, target_project_id: str) -> dict:
    if source_project_id == target_project_id:
        raise ValueError("Cannot merge a project into itself")

    conn = get_connection()

    found = conn.execute(
        "SELECT id FROM projects WHERE id = ANY(%s::uuid[])",
        ([str(source_project_id), str(target_project_id)],),
    ).fetchall()
    found_ids = {str(r["id"]) for r in found}
    if str(source_project_id) not in found_ids:
        conn.rollback()
        raise ValueError(f"Source project '{source_project_id}' not found")
    if str(target_project_id) not in found_ids:
        conn.rollback()
        raise ValueError(f"Target project '{target_project_id}' not found")

    collisions = conn.execute(
        """
        SELECT DISTINCT s.topic_key
        FROM observations s
        JOIN observations t ON t.topic_key = s.topic_key
        WHERE s.project_id = %s
          AND t.project_id = %s
          AND s.topic_key IS NOT NULL
          AND s.deleted_at IS NULL
          AND t.deleted_at IS NULL
        """,
        (source_project_id, target_project_id),
    ).fetchall()
    topic_key_collisions = sorted(r["topic_key"] for r in collisions)

    superseded_observation_ids: list[str] = []
    if topic_key_collisions:
        superseded_rows = conn.execute(
            """
            UPDATE observations
            SET project_id = %s, deleted_at = now()
            WHERE project_id = %s
              AND deleted_at IS NULL
              AND topic_key = ANY(%s)
            RETURNING id
            """,
            (target_project_id, source_project_id, topic_key_collisions),
        ).fetchall()
        superseded_observation_ids = [str(r["id"]) for r in superseded_rows]

    remaining_moved = conn.execute(
        "UPDATE observations SET project_id = %s WHERE project_id = %s",
        (target_project_id, source_project_id),
    ).rowcount
    observations_moved = len(superseded_observation_ids) + remaining_moved

    sessions_moved = conn.execute(
        "UPDATE sessions SET project_id = %s WHERE project_id = %s",
        (target_project_id, source_project_id),
    ).rowcount

    workflows_moved = conn.execute(
        "UPDATE workflows SET project_id = %s WHERE project_id = %s",
        (target_project_id, source_project_id),
    ).rowcount

    conn.execute("DELETE FROM projects WHERE id = %s", (source_project_id,))
    conn.commit()

    return {
        "source_project_id": source_project_id,
        "target_project_id": target_project_id,
        "observations_moved": observations_moved,
        "sessions_moved": sessions_moved,
        "workflows_moved": workflows_moved,
        "topic_key_collisions": topic_key_collisions,
        "superseded_observation_ids": superseded_observation_ids,
    }


def create_session(project_id: str, client_session_id: str | None = None) -> dict:
    conn = get_connection()
    row = conn.execute(
        "INSERT INTO sessions (project_id, client_session_id) VALUES (%s, %s) "
        "RETURNING *",
        (project_id, client_session_id),
    ).fetchone()
    conn.commit()
    return dict(row)


def end_session(session_id: str, summary: str | None = None) -> dict | None:
    """Close a session that is still open, returning the closed row.

    Returns None when the row is already closed (or absent): without the
    `ended_at IS NULL` guard this is a lost update — a second close
    replaces a summary that is already there. Concurrent active sessions
    per project make that window reachable, so the guard is in the SQL
    rather than in the callers.
    """
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE sessions
        SET ended_at = now(), summary = %s
        WHERE id = %s AND ended_at IS NULL
        RETURNING *
        """,
        (summary, session_id),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def close_session_by_client_id(
    workspace_id: str, client_session_id: str
) -> dict | None:
    """Close the active session carrying this exact client_session_id, scoped
    to any project inside this workspace. Never creates a project or a
    session, and never touches a session with a different (or missing) id —
    the caller (a hook, or any other client) proves which session is theirs
    purely by this id, never by "the workspace's active session"."""
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE sessions s
        SET ended_at = now(), summary = NULL
        FROM projects p
        WHERE s.project_id = p.id
          AND p.workspace_id = %s
          AND s.client_session_id = %s
          AND s.ended_at IS NULL
        RETURNING s.*
        """,
        (workspace_id, client_session_id),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def get_active_session(project_id: str) -> dict | None:
    """Get the most recent unclosed session for a project."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM sessions
        WHERE project_id = %s AND ended_at IS NULL
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def get_active_session_by_client_id(
    project_id: str, client_session_id: str | None
) -> dict | None:
    """Get the most recent unclosed session for a project tagged with this
    exact client_session_id.

    Uses IS NOT DISTINCT FROM rather than =, so NULL matches NULL: an
    untagged caller (client_session_id=None) still finds its own previous
    untagged row, while two differently-tagged callers never match each
    other's session. This is what makes concurrent active sessions per
    project legal — each client_session_id owns its own row.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM sessions
        WHERE project_id = %s
          AND client_session_id IS NOT DISTINCT FROM %s
          AND ended_at IS NULL
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (project_id, client_session_id),
    ).fetchone()
    return dict(row) if row else None


def get_latest_session_summary(
    project_ids: str | list[str] | None, workspace_id: str | None = None
) -> dict | None:
    """Get the most recent completed session with a summary, fenced by
    workspace_id when given.

    project_id=None means the caller sits at the registered workspace root,
    where the last session of any project in the workspace is in scope.
    """
    conn = get_connection()
    if workspace_id:
        scope, scope_params = _project_scope(project_ids, None, alias="s")
        row = conn.execute(
            f"""
            SELECT s.*, p.name AS project FROM sessions s
            JOIN projects p ON p.id = s.project_id
            WHERE p.workspace_id = %s
              AND s.ended_at IS NOT NULL AND s.summary IS NOT NULL
              {scope}
            ORDER BY s.ended_at DESC
            LIMIT 1
            """,
            (workspace_id, *scope_params),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE project_id = ANY(%s::uuid[])
              AND ended_at IS NOT NULL AND summary IS NOT NULL
            ORDER BY ended_at DESC
            LIMIT 1
            """,
            (_id_list(project_ids),),
        ).fetchone()
    return dict(row) if row else None


def save_observation(
    project_id: str,
    title: str,
    content: str,
    type: str,
    topic_key: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    occurred_at: str | None = None,
    affects: list[str] | None = None,
) -> dict:
    if type not in ALLOWED_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(ALLOWED_TYPES))}")

    conn = get_connection()
    base_meta = dict(metadata or {})
    if affects is not None:
        base_meta = _with_affects(base_meta, affects)
    meta = json.dumps(base_meta)

    chash = _content_hash(title, content)

    if topic_key:
        existing = conn.execute(
            """
            SELECT id, metadata FROM observations
            WHERE project_id = %s AND topic_key = %s
              AND deleted_at IS NULL AND superseded_by IS NULL
            """,
            (project_id, topic_key),
        ).fetchone()
        if existing:
            # This branch replaces metadata wholesale, so an omitted affects
            # has to carry the stored list forward or a later revision would
            # silently strip the observation's cross-project visibility.
            if affects is None:
                stored = (existing["metadata"] or {}).get("affects")
                meta = json.dumps(_with_affects(base_meta, stored or []))
            if embedding is not None:
                row = conn.execute(
                    """
                    UPDATE observations
                    SET title = %s,
                        content = %s,
                        type = %s,
                        session_id = %s,
                        metadata = %s,
                        embedding = %s,
                        content_hash = %s,
                        occurred_at = COALESCE(%s, occurred_at),
                        revision_count = revision_count + 1,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        title,
                        content,
                        type,
                        session_id,
                        meta,
                        str(embedding),
                        chash,
                        occurred_at,
                        existing["id"],
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE observations
                    SET title = %s,
                        content = %s,
                        type = %s,
                        session_id = %s,
                        metadata = %s,
                        content_hash = %s,
                        occurred_at = COALESCE(%s, occurred_at),
                        revision_count = revision_count + 1,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        title,
                        content,
                        type,
                        session_id,
                        meta,
                        chash,
                        occurred_at,
                        existing["id"],
                    ),
                ).fetchone()
            conn.commit()
            return dict(row)

    # Dedup: check for identical content in same project within 15 min window.
    # Superseded rows are excluded — absorbing a save into one would ack
    # success while the content surfaced nowhere.
    existing_dup = conn.execute(
        """
        SELECT id, metadata FROM observations
        WHERE project_id = %s
          AND content_hash = %s
          AND deleted_at IS NULL
          AND superseded_by IS NULL
          AND created_at > now() - interval '15 minutes'
        LIMIT 1
        """,
        (project_id, chash),
    ).fetchone()
    if existing_dup:
        # content_hash covers title and content only, so the same content
        # reaching one more project lands here. Union rather than drop it —
        # otherwise the wider routing is lost with no signal.
        dup_meta = dict(existing_dup["metadata"] or {})
        stored = dup_meta.get("affects") or []
        merged = stored + [a for a in (affects or []) if a not in stored]
        row = conn.execute(
            """
            UPDATE observations
            SET duplicate_count = duplicate_count + 1,
                metadata = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (json.dumps(_with_affects(dup_meta, merged)), existing_dup["id"]),
        ).fetchone()
        conn.commit()
        result = dict(row)
        result["_deduplicated"] = True
        return result

    if embedding is not None:
        row = conn.execute(
            """
            INSERT INTO observations
                (project_id, session_id, type, title, content,
                 topic_key, metadata, embedding, content_hash, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                project_id,
                session_id,
                type,
                title,
                content,
                topic_key,
                meta,
                str(embedding),
                chash,
                occurred_at,
            ),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO observations
                (project_id, session_id, type, title, content,
                 topic_key, metadata, content_hash, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                project_id,
                session_id,
                type,
                title,
                content,
                topic_key,
                meta,
                chash,
                occurred_at,
            ),
        ).fetchone()
    conn.commit()
    return dict(row)


def search_observations(
    project_ids: str | list[str] | None,
    query: str,
    type: str | None = None,
    limit: int = 10,
    workspace_id: str | None = None,
    project_name: str | None = None,
    inherited_ids: str | list[str] | None = None,
) -> list[dict]:
    conn = get_connection()
    if workspace_id:
        scope, scope_params = _project_scope(
            project_ids, project_name, alias="o", inherited_ids=inherited_ids
        )
        base = f"""
            SELECT o.*, ts_rank(o.search_vector, q) AS rank
            FROM observations o
            JOIN projects p ON p.id = o.project_id,
            plainto_tsquery('simple', %s) q
            WHERE p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND o.search_vector @@ q
              {scope}
        """
        params: list = [query, workspace_id, *scope_params]
    else:
        scope, scope_params = _project_scope(
            project_ids, project_name, inherited_ids=inherited_ids
        )
        base = f"""
            SELECT *, ts_rank(search_vector, query) AS rank
            FROM observations, plainto_tsquery('simple', %s) query
            WHERE deleted_at IS NULL
              AND superseded_by IS NULL
              AND search_vector @@ query
              {scope}
        """
        params = [query, *scope_params]

    if type:
        base += " AND o.type = %s" if workspace_id else " AND type = %s"
        params.append(type)

    base += " ORDER BY rank DESC LIMIT %s"
    params.append(limit)

    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


def search_observations_global(
    query: str,
    owner_user_id: str,
    type: str | None = None,
    limit: int = 10,
) -> list[dict]:
    conn = get_connection()
    base = """
        SELECT o.*, p.name AS project_name, ts_rank(o.search_vector, q) AS rank
        FROM observations o
        JOIN projects p ON p.id = o.project_id
        JOIN workspaces w ON w.id = p.workspace_id,
        plainto_tsquery('simple', %s) q
        WHERE o.deleted_at IS NULL
          AND o.superseded_by IS NULL
          AND o.search_vector @@ q
          AND w.owner_user_id = %s
    """
    params: list = [query, owner_user_id]

    if type:
        base += " AND o.type = %s"
        params.append(type)

    base += " ORDER BY rank DESC LIMIT %s"
    params.append(limit)

    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


PROMPT_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "what",
        "about",
        "this",
        "from",
        "have",
        "your",
        "our",
        "but",
        "not",
        "all",
        "can",
        "how",
        "when",
        "where",
        "why",
        "who",
        "which",
        "there",
        "here",
        "than",
        "them",
        "they",
        "these",
        "those",
        "some",
        "more",
        "most",
        "very",
        "just",
        "also",
        "only",
        "same",
        "such",
        "both",
        "each",
        "que",
        "para",
        "con",
        "los",
        "las",
        "del",
        "sobre",
        "como",
        "cómo",
        "qué",
        "más",
        "mas",
        "por",
        "esto",
        "esta",
        "está",
        "esa",
        "ese",
        "eso",
        "otro",
        "otra",
        "otros",
        "otras",
        "una",
        "uno",
        "unos",
        "unas",
        "les",
        "sus",
        "muy",
        "todo",
        "toda",
        "todos",
        "todas",
        "hay",
        "fue",
        "ser",
        "son",
        "desde",
        "hasta",
        "entre",
        "sin",
        "porque",
        "pero",
        "cual",
        "cuales",
        "quien",
        "quienes",
        "cuando",
        "donde",
    }
)


def search_observations_by_workspace(
    workspace_id: str,
    query: str,
    limit: int = 5,
    project_names: list[str] | None = None,
    affects_name: str | None = None,
    inherited_names: list[str] | None = None,
) -> list[dict]:
    conn = get_connection()
    scope, scope_params = _project_name_scope(
        project_names, affects_name, inherited_names
    )
    rows = conn.execute(
        f"""
        WITH q AS (
            SELECT string_agg(lexeme, ' | ') AS tsq
            FROM unnest(tsvector_to_array(to_tsvector('simple', %s))) AS lexeme
            WHERE char_length(lexeme) >= 3 AND lexeme <> ALL(%s)
        )
        SELECT o.*, p.name AS project,
               ts_rank(o.search_vector, to_tsquery('simple', q.tsq)) AS rank
        FROM observations o
        JOIN projects p ON p.id = o.project_id
        CROSS JOIN q
        WHERE q.tsq IS NOT NULL AND q.tsq <> ''
          AND p.workspace_id = %s
          AND o.deleted_at IS NULL AND o.superseded_by IS NULL
          AND o.search_vector @@ to_tsquery('simple', q.tsq)
          {scope}
        ORDER BY rank DESC LIMIT %s
        """,
        [query, list(PROMPT_STOPWORDS), workspace_id, *scope_params, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_observations(
    project_ids: str | list[str] | None,
    limit: int = 20,
    workspace_id: str | None = None,
    project_name: str | None = None,
    inherited_ids: str | list[str] | None = None,
) -> list[dict]:
    """Recent observations for a project, always fenced by workspace_id when
    given. project_ids=None means the caller sits at the registered workspace
    root, where every project in the workspace is in scope.
    """
    conn = get_connection()
    if workspace_id:
        scope, scope_params = _project_scope(
            project_ids, project_name, alias="o", inherited_ids=inherited_ids
        )
        rows = conn.execute(
            f"""
            SELECT o.*, p.name AS project FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              {scope}
            ORDER BY COALESCE(o.occurred_at, o.created_at) DESC
            LIMIT %s
            """,
            (workspace_id, *scope_params, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE project_id = ANY(%s::uuid[])
              AND deleted_at IS NULL AND superseded_by IS NULL
            ORDER BY COALESCE(occurred_at, created_at) DESC
            LIMIT %s
            """,
            (_id_list(project_ids), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_observation(
    observation_id: str, workspace_id: str | None = None
) -> dict | None:
    """Read a single observation by id, superseded ones included.

    This is the audit path for corrections — a superseded row keeps its
    superseded_by pointer readable here even though every surfacing read
    path filters it out. Deleted rows stay hidden. When workspace_id is
    given, ids outside that workspace report the same shape as a
    nonexistent id.

    The chain is walkable both ways: superseded_by points forward to the
    replacement, supersedes back to the ids it replaced. supersedes is a
    list, most-recent-first — the topic_key upsert flow lets several
    corrections resolve to the same row. It is absent when the row replaced
    nothing, when every predecessor was deleted, and (like the row itself)
    when a predecessor lives outside workspace_id, since that id would read
    back as nonexistent. The reverse lookup shares the row's statement so
    both sides come from one snapshot.
    """
    conn = get_connection()
    if workspace_id:
        row = conn.execute(
            """
            SELECT o.*, (
                SELECT array_agg(pred.id ORDER BY pred.updated_at DESC)
                FROM observations pred
                JOIN projects pp ON pp.id = pred.project_id
                WHERE pred.superseded_by = o.id
                  AND pred.deleted_at IS NULL
                  AND pp.workspace_id = p.workspace_id
            ) AS supersedes
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE o.id = %s AND p.workspace_id = %s AND o.deleted_at IS NULL
            """,
            (observation_id, workspace_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT o.*, (
                SELECT array_agg(pred.id ORDER BY pred.updated_at DESC)
                FROM observations pred
                WHERE pred.superseded_by = o.id AND pred.deleted_at IS NULL
            ) AS supersedes
            FROM observations o
            WHERE o.id = %s AND o.deleted_at IS NULL
            """,
            (observation_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    if not result["supersedes"]:
        del result["supersedes"]
    return result


def delete_observation(observation_id: str, workspace_id: str) -> dict:
    """Soft-delete an observation, scoped to the caller's workspace.

    Cross-workspace ids never match the join, so they report the same
    shape as a nonexistent id. Deleting an already-deleted observation
    is idempotent — it acks success with already_deleted=True.

    Deleting an observation also clears every superseded_by pointing at
    it: superseded_by must never reference a deleted row, or its
    predecessor would stay hidden forever. Deleting a replacement is the
    natural undo — it resurfaces what it replaced.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT o.id, o.title, o.deleted_at FROM observations o
        JOIN projects p ON p.id = o.project_id
        WHERE o.id = %s AND p.workspace_id = %s
        """,
        (observation_id, workspace_id),
    ).fetchone()
    if not row:
        return {"found": False}
    if row["deleted_at"] is not None:
        return {
            "found": True,
            "already_deleted": True,
            "id": str(row["id"]),
            "title": row["title"],
            "resurfaced": 0,
        }
    updated = conn.execute(
        "UPDATE observations SET deleted_at = now() WHERE id = %s RETURNING id, title",
        (observation_id,),
    ).fetchone()
    resurfaced = conn.execute(
        "UPDATE observations SET superseded_by = NULL WHERE superseded_by = %s",
        (observation_id,),
    ).rowcount
    conn.commit()
    return {
        "found": True,
        "already_deleted": False,
        "id": str(updated["id"]),
        "title": updated["title"],
        "resurfaced": resurfaced,
    }


def supersede_observation(old_id: str, new_id: str, workspace_id: str) -> dict:
    """Mark old_id as superseded by new_id, scoped to the caller's workspace.

    Forgiving by design: a malformed, self-referential, nonexistent,
    cross-workspace, deleted, or already-superseded old_id never raises —
    it reports applied=False with a discriminated reason (invalid_id,
    self, not_found, already_deleted, already_superseded), so a bad
    supersedes value can never fail the save and the caller can tell a
    pointless retry from a harmful one.
    """
    if str(old_id) == str(new_id):
        return {"applied": False, "reason": "self"}
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT o.deleted_at, o.superseded_by FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE o.id = %s AND p.workspace_id = %s
            """,
            (old_id, workspace_id),
        ).fetchone()
    except InvalidTextRepresentation:
        conn.rollback()
        return {"applied": False, "reason": "invalid_id"}
    if not row:
        return {"applied": False, "reason": "not_found"}
    if row["deleted_at"] is not None:
        return {"applied": False, "reason": "already_deleted"}
    if row["superseded_by"] is not None:
        return {"applied": False, "reason": "already_superseded"}
    conn.execute(
        "UPDATE observations SET superseded_by = %s WHERE id = %s",
        (new_id, old_id),
    )
    conn.commit()
    return {"applied": True}


def list_projects(
    workspace_id: str | None = None, owner_user_id: str | None = None
) -> list[dict]:
    conn = get_connection()
    if workspace_id:
        rows = conn.execute(
            "SELECT * FROM projects WHERE workspace_id = %s ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    elif owner_user_id:
        rows = conn.execute(
            """
            SELECT p.* FROM projects p
            JOIN workspaces w ON w.id = p.workspace_id
            WHERE w.owner_user_id = %s
            ORDER BY p.created_at DESC
            """,
            (owner_user_id,),
        ).fetchall()
    else:
        raise ValueError("list_projects requires workspace_id or owner_user_id")
    return [dict(r) for r in rows]


def list_workspaces(owner_user_id: str | None = None) -> list[dict]:
    conn = get_connection()
    base = """
        SELECT w.*, COUNT(p.id) AS project_count
        FROM workspaces w
        LEFT JOIN projects p ON p.workspace_id = w.id
    """
    params: list = []
    if owner_user_id is not None:
        base += " WHERE w.owner_user_id = %s"
        params.append(owner_user_id)
    base += " GROUP BY w.id ORDER BY w.created_at DESC"
    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


def delete_workspace(workspace_name: str, owner_user_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM workspaces WHERE name = %s AND owner_user_id = %s",
        (workspace_name, owner_user_id),
    ).fetchone()
    if not row:
        return False
    purge_workspace_data(workspace_name, owner_user_id, mode="hard")
    return True


def rename_workspace(old_name: str, new_name: str, owner_user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE workspaces SET name = %s, updated_at = now()
        WHERE name = %s AND owner_user_id = %s RETURNING *
        """,
        (new_name, old_name, owner_user_id),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def search_similar(
    project_ids: str | list[str] | None,
    embedding: list[float],
    limit: int = 10,
    workspace_id: str | None = None,
    project_name: str | None = None,
    inherited_ids: str | list[str] | None = None,
) -> list[dict]:
    conn = get_connection()
    query_embedding = str(embedding)
    if workspace_id:
        scope, scope_params = _project_scope(
            project_ids, project_name, alias="o", inherited_ids=inherited_ids
        )
        rows = conn.execute(
            f"""
            SELECT o.*, 1 - (o.embedding <=> %s::vector) AS similarity
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND o.embedding IS NOT NULL
              {scope}
            ORDER BY o.embedding <=> %s::vector
            LIMIT %s
            """,
            (
                query_embedding,
                workspace_id,
                *scope_params,
                query_embedding,
                limit,
            ),
        ).fetchall()
    else:
        scope, scope_params = _project_scope(
            project_ids, project_name, inherited_ids=inherited_ids
        )
        rows = conn.execute(
            f"""
            SELECT *, 1 - (embedding <=> %s::vector) AS similarity
            FROM observations
            WHERE deleted_at IS NULL
              AND superseded_by IS NULL
              AND embedding IS NOT NULL
              {scope}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, *scope_params, query_embedding, limit),
        ).fetchall()
    return [dict(r) for r in rows]


MIN_RELATED_SIMILARITY = 0.65

RELATED_CANDIDATES = 40


def find_related_observations(
    workspace_id: str,
    embedding: list[float],
    exclude_id: str,
    limit: int = 3,
    min_similarity: float = MIN_RELATED_SIMILARITY,
) -> list[dict]:
    """Most similar existing observations across the whole workspace.

    Mirrors search_similar's workspace-wide join, labeled with the owning
    project like get_recent_observations. Takes an already-computed
    embedding — never re-embeds. Excludes the observation just saved
    (exclude_id), deleted and superseded rows, and anything below
    min_similarity, so only entries worth surfacing come back.

    Best-effort by design: the bar is applied to the nearest
    RELATED_CANDIDATES rows rather than to the whole table, so an
    approximate index scan can miss a match a full scan would have found.
    Cheap surfacing on every save beats exhaustive recall here —
    search_similar is the exhaustive path.
    """
    conn = get_connection()
    query_embedding = str(embedding)
    # Nearest-K-then-filter: the bar sits outside the vector-ordered
    # subquery so ORDER BY/LIMIT can drive idx_obs_embedding, K over-fetches
    # because an approximate scan under row filters can under-return, and
    # the bar is a distance ceiling because Postgres sorts NaN above every
    # float — a zero-norm embedding must fail the bar, not pass it.
    rows = conn.execute(
        """
        SELECT id, title, topic_key, project, 1 - distance AS similarity
        FROM (
            SELECT o.id, o.title, o.topic_key, p.name AS project,
                   o.embedding <=> %s::vector AS distance
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE p.workspace_id = %s
              AND o.id != %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND o.embedding IS NOT NULL
            ORDER BY o.embedding <=> %s::vector
            LIMIT %s
        ) candidates
        WHERE distance <= %s
        ORDER BY similarity DESC
        LIMIT %s
        """,
        (
            query_embedding,
            workspace_id,
            exclude_id,
            query_embedding,
            RELATED_CANDIDATES,
            1 - min_similarity,
            limit,
        ),
    ).fetchall()
    return [dict(r) for r in rows]


CLUSTER_CANDIDATES = 10

RECENT_ACTIVITY_CANDIDATES = 5


def _has_recent_similar_activity(
    workspace_id: str,
    member_ids: list[str],
    min_age_days: int,
    similarity_threshold: float,
) -> bool:
    """One cheap per-cluster check: does any member have a fresher
    (younger than the age gate) embedding-similar neighbor anywhere in
    the workspace?

    The mechanical, honest distillation of "something recent touches
    this topic" — memodi cannot tell "migrated away" from "still uses",
    that reading is semantic and left to the agent reviewing the cluster
    before it fuses anything.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1
        FROM observations m
        CROSS JOIN LATERAL (
            SELECT 1 - (m.embedding <=> o2.embedding) AS similarity
            FROM observations o2
            JOIN projects p2 ON p2.id = o2.project_id
            WHERE p2.workspace_id = %s
              AND o2.id != m.id
              AND o2.deleted_at IS NULL
              AND o2.superseded_by IS NULL
              AND o2.embedding IS NOT NULL
              AND o2.created_at > now() - make_interval(days => %s)
            ORDER BY m.embedding <=> o2.embedding
            LIMIT %s
        ) n
        WHERE m.id = ANY(%s) AND n.similarity >= %s
        LIMIT 1
        """,
        (
            workspace_id,
            min_age_days,
            RECENT_ACTIVITY_CANDIDATES,
            member_ids,
            similarity_threshold,
        ),
    ).fetchone()
    return row is not None


def find_consolidation_clusters(
    workspace_id: str,
    min_age_days: int = 30,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.75,
    theme: str | None = None,
) -> list[dict]:
    """Mechanically detect clusters of similar, aged, live observations
    ripe for a compressed-logbook rollup.

    Never re-embeds and never writes: builds K-NN edges via a LATERAL
    self-join against idx_obs_embedding over the eligible set (live,
    embedded, aged, workspace-scoped, optionally theme-narrowed via the
    same keyword FTS builder as search_observations_by_workspace), then
    finds connected components in Python with union-find. memodi
    recommends the cluster as evidence; the agent vets it and writes the
    compression.
    """
    conn = get_connection()

    theme_cte = ""
    theme_join = ""
    theme_filter = ""
    params: list = []
    if theme:
        theme_cte = """
        theme_q AS (
            SELECT string_agg(lexeme, ' | ') AS tsq
            FROM unnest(tsvector_to_array(to_tsvector('simple', %s))) AS lexeme
            WHERE char_length(lexeme) >= 3 AND lexeme <> ALL(%s)
        ),
        """
        theme_join = "CROSS JOIN theme_q"
        theme_filter = """
              AND theme_q.tsq IS NOT NULL AND theme_q.tsq <> ''
              AND o.search_vector @@ to_tsquery('simple', theme_q.tsq)
        """
        params.extend([theme, list(PROMPT_STOPWORDS)])

    params.extend([workspace_id, min_age_days])
    params.extend([CLUSTER_CANDIDATES, 1 - similarity_threshold])

    query = f"""
        WITH {theme_cte}eligible AS (
            SELECT o.id
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            {theme_join}
            WHERE p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND o.embedding IS NOT NULL
              AND o.created_at <= now() - make_interval(days => %s)
              {theme_filter}
        )
        SELECT
            oe.id AS a_id, oe.title AS a_title, oe.topic_key AS a_topic_key,
            oe.type AS a_type, oe.created_at AS a_created_at,
            length(oe.content) AS a_chars,
            n.id AS b_id, n.title AS b_title, n.topic_key AS b_topic_key,
            n.type AS b_type, n.created_at AS b_created_at, n.chars AS b_chars,
            1 - n.distance AS similarity
        FROM eligible e
        JOIN observations oe ON oe.id = e.id
        CROSS JOIN LATERAL (
            SELECT o2.id, o2.title, o2.topic_key, o2.type, o2.created_at,
                   length(o2.content) AS chars,
                   oe.embedding <=> o2.embedding AS distance
            FROM observations o2
            JOIN eligible e2 ON e2.id = o2.id
            WHERE o2.id != oe.id
            ORDER BY oe.embedding <=> o2.embedding
            LIMIT %s
        ) n
        WHERE n.distance <= %s
    """
    rows = conn.execute(query, params).fetchall()

    attrs: dict = {}
    parent: dict = {}
    edge_similarity: dict = {}
    edge_pairs: set = set()

    def find(node_id):
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for row in rows:
        a_id, b_id = row["a_id"], row["b_id"]
        for node_id, prefix in ((a_id, "a"), (b_id, "b")):
            parent.setdefault(node_id, node_id)
            if node_id not in attrs:
                attrs[node_id] = {
                    "id": node_id,
                    "title": row[f"{prefix}_title"],
                    "topic_key": row[f"{prefix}_topic_key"],
                    "type": row[f"{prefix}_type"],
                    "created_at": row[f"{prefix}_created_at"],
                    "chars": row[f"{prefix}_chars"],
                }
        edge_similarity[frozenset((a_id, b_id))] = row["similarity"]
        edge_pairs.add((a_id, b_id))

    for a_id, b_id in edge_pairs:
        union(a_id, b_id)

    components: dict = {}
    for node_id in parent:
        components.setdefault(find(node_id), set()).add(node_id)

    now = datetime.now(UTC)
    clusters = []
    for member_ids in components.values():
        if len(member_ids) < min_cluster_size:
            continue

        internal_similarities = [
            similarity
            for pair, similarity in edge_similarity.items()
            if pair <= member_ids
        ]
        confidence = sum(internal_similarities) / len(internal_similarities)
        members = [attrs[member_id] for member_id in member_ids]
        total_chars = sum(member["chars"] for member in members)
        largest_chars = max(member["chars"] for member in members)
        oldest_days = max((now - member["created_at"]).days for member in members)

        reason = []
        if confidence >= 0.85:
            reason.append("high_cohesion")
        reason.append(f"oldest_member_days:{oldest_days}")
        if _has_recent_similar_activity(
            workspace_id, list(member_ids), min_age_days, similarity_threshold
        ):
            reason.append("recent_similar_activity")

        clusters.append(
            {
                "members": members,
                "confidence": confidence,
                "reason": reason,
                "member_count": len(members),
                "total_chars": total_chars,
                "estimated_gain": 1 - (largest_chars / total_chars)
                if total_chars
                else 0.0,
            }
        )
    return clusters


def search_hybrid(
    project_ids: str | list[str] | None,
    query: str,
    embedding: list[float],
    limit: int = 10,
    workspace_id: str | None = None,
    project_name: str | None = None,
    inherited_ids: str | list[str] | None = None,
) -> list[dict]:
    conn = get_connection()
    query_embedding = str(embedding)
    k = 60  # RRF constant

    # Both ranking CTEs and the outer join each need the scope: a CTE that
    # still ranked the whole workspace would let foreign rows crowd out the
    # in-scope ones before the outer filter ever runs.
    scope, scope_params = _project_scope(
        project_ids, project_name, inherited_ids=inherited_ids
    )
    outer_scope, outer_params = _project_scope(
        project_ids, project_name, alias="o", inherited_ids=inherited_ids
    )

    cte = f"""
        WITH keyword AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       ORDER BY
                           ts_rank(search_vector, plainto_tsquery('simple', %s)) DESC
                   ) AS rank
            FROM observations
            WHERE deleted_at IS NULL
              AND superseded_by IS NULL
              AND search_vector @@ plainto_tsquery('simple', %s)
              {scope}
        ),
        semantic AS (
            SELECT id,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM observations
            WHERE deleted_at IS NULL
              AND superseded_by IS NULL
              AND embedding IS NOT NULL
              {scope}
        )
    """
    cte_params = [query, query, *scope_params, query_embedding, *scope_params]
    if workspace_id:
        tail = f"""
            SELECT o.*,
                   COALESCE(1.0 / (%s + k.rank), 0)
                       + COALESCE(1.0 / (%s + s.rank), 0) AS rrf_score
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            LEFT JOIN keyword k ON o.id = k.id
            LEFT JOIN semantic s ON o.id = s.id
            WHERE p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND (k.id IS NOT NULL OR s.id IS NOT NULL)
              {outer_scope}
            ORDER BY rrf_score DESC
            LIMIT %s
        """
        params = [*cte_params, k, k, workspace_id, *outer_params, limit]
    else:
        tail = f"""
            SELECT o.*,
                   COALESCE(1.0 / (%s + k.rank), 0)
                       + COALESCE(1.0 / (%s + s.rank), 0) AS rrf_score
            FROM observations o
            LEFT JOIN keyword k ON o.id = k.id
            LEFT JOIN semantic s ON o.id = s.id
            WHERE o.deleted_at IS NULL
              AND o.superseded_by IS NULL
              AND (k.id IS NOT NULL OR s.id IS NOT NULL)
              {outer_scope}
            ORDER BY rrf_score DESC
            LIMIT %s
        """
        params = [*cte_params, k, k, *outer_params, limit]
    rows = conn.execute(cte + tail, params).fetchall()
    return [dict(r) for r in rows]


def get_observations_without_embedding(
    project_id: str, batch_size: int = 100
) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, title, content FROM observations
        WHERE project_id = %s AND embedding IS NULL AND deleted_at IS NULL
        LIMIT %s
        """,
        (project_id, batch_size),
    ).fetchall()
    return [dict(r) for r in rows]


def get_observations_with_wiki_links(project_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, content, topic_key FROM observations
        WHERE project_id = %s
          AND topic_key IS NOT NULL
          AND deleted_at IS NULL
          AND superseded_by IS NULL
          AND content LIKE '%%[[%%'
        ORDER BY created_at
        """,
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_observation_embedding(observation_id: str, embedding: list[float]) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE observations SET embedding = %s WHERE id = %s",
        (str(embedding), observation_id),
    )
    conn.commit()


def count_workspace_resources(workspace_name: str, owner_user_id: str) -> dict | None:
    """Count deletable resources for a workspace. Returns None if workspace
    does not exist (or is not owned by owner_user_id), so callers can
    distinguish 'empty' from 'missing'."""
    conn = get_connection()
    ws_row = conn.execute(
        "SELECT id FROM workspaces WHERE name = %s AND owner_user_id = %s",
        (workspace_name, owner_user_id),
    ).fetchone()
    if not ws_row:
        return None
    ws_id = ws_row["id"]

    observations = conn.execute(
        """
        SELECT COUNT(*) AS c FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).fetchone()["c"]

    workflows = conn.execute(
        """
        SELECT COUNT(*) AS c FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).fetchone()["c"]

    workflow_transitions = conn.execute(
        """
        SELECT COUNT(*) AS c FROM workflow_transitions
        WHERE workflow_id IN (
            SELECT id FROM workflows
            WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        )
        """,
        (ws_id,),
    ).fetchone()["c"]

    sessions = conn.execute(
        """
        SELECT COUNT(*) AS c FROM sessions
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).fetchone()["c"]

    projects = conn.execute(
        "SELECT COUNT(*) AS c FROM projects WHERE workspace_id = %s",
        (ws_id,),
    ).fetchone()["c"]

    paths = conn.execute(
        "SELECT COUNT(*) AS c FROM workspace_paths WHERE workspace_id = %s",
        (ws_id,),
    ).fetchone()["c"]

    project_names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM projects WHERE workspace_id = %s ORDER BY name",
            (ws_id,),
        ).fetchall()
    ]

    return {
        "workspace": workspace_name,
        "observations": observations,
        "workflows": workflows,
        "workflow_transitions": workflow_transitions,
        "sessions": sessions,
        "projects": projects,
        "workspace_paths": paths,
        "project_names": project_names,
    }


def purge_workspace_data(
    workspace_name: str, owner_user_id: str, mode: str = "medium"
) -> dict:
    """Hard-delete workspace data. Returns actual deletion counts.

    mode='medium': observations, workflow_transitions, workflows, sessions.
    mode='hard': medium + workspace_paths, projects, workspace.

    Does NOT touch the knowledge graph — graph has no workspace scoping.
    Callers that want graph purge must call graph_repository.purge_all_graph()
    explicitly (opt-in).
    """
    if mode not in ("medium", "hard"):
        raise ValueError("mode must be 'medium' or 'hard'")

    conn = get_connection()
    ws_row = conn.execute(
        "SELECT id FROM workspaces WHERE name = %s AND owner_user_id = %s",
        (workspace_name, owner_user_id),
    ).fetchone()
    if not ws_row:
        raise ValueError(f"Workspace '{workspace_name}' not found")
    ws_id = ws_row["id"]

    # Delete children before parents to respect FKs.
    transitions_deleted = conn.execute(
        """
        DELETE FROM workflow_transitions
        WHERE workflow_id IN (
            SELECT id FROM workflows
            WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        )
        """,
        (ws_id,),
    ).rowcount

    workflows_deleted = conn.execute(
        """
        DELETE FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).rowcount

    # Delete observations first — sessions have no other references, so
    # they can be dropped cleanly once observations are gone.
    observations_deleted = conn.execute(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).rowcount

    sessions_deleted = conn.execute(
        """
        DELETE FROM sessions
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    ).rowcount

    paths_deleted = 0
    projects_deleted = 0
    workspace_deleted = False

    if mode == "hard":
        paths_deleted = conn.execute(
            "DELETE FROM workspace_paths WHERE workspace_id = %s",
            (ws_id,),
        ).rowcount
        projects_deleted = conn.execute(
            "DELETE FROM projects WHERE workspace_id = %s",
            (ws_id,),
        ).rowcount
        conn.execute(
            "DELETE FROM workspaces WHERE id = %s",
            (ws_id,),
        )
        workspace_deleted = True

    conn.commit()

    return {
        "mode": mode,
        "workspace": workspace_name,
        "observations": observations_deleted,
        "workflow_transitions": transitions_deleted,
        "workflows": workflows_deleted,
        "sessions": sessions_deleted,
        "workspace_paths": paths_deleted,
        "projects": projects_deleted,
        "workspace_deleted": workspace_deleted,
    }
