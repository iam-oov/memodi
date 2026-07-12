import hashlib
import json

from psycopg.errors import UniqueViolation

from memodi.database.connection import get_connection


def _content_hash(title: str, content: str) -> str:
    """SHA-256 hash of title+content for deduplication."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def _normalize_path(path: str) -> str:
    return path.rstrip("/")


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
        SELECT w.* FROM workspaces w
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
        SELECT w.id AS workspace_id, w.name AS workspace_name,
               w.owner_user_id AS owner_user_id
        FROM workspace_paths wp
        JOIN workspaces w ON w.id = wp.workspace_id
        WHERE wp.machine = %s AND wp.path = %s
        """,
        (machine, normalized_path),
    ).fetchone()

    if existing_path:
        owned = conn.execute(
            "SELECT id FROM workspaces WHERE owner_user_id = %s AND name = %s",
            (user_id, workspace_name),
        ).fetchone()
        if owned and existing_path["workspace_id"] == owned["id"]:
            return get_or_create_workspace(workspace_name, owner_user_id=user_id)
        conn.rollback()
        if existing_path["owner_user_id"] == user_id:
            raise ValueError(
                f"Path already registered to workspace "
                f"'{existing_path['workspace_name']}'"
            )
        raise ValueError("Path already registered on this machine")

    workspace = get_or_create_workspace(workspace_name, owner_user_id=user_id)
    try:
        conn.execute(
            "INSERT INTO workspace_paths (workspace_id, machine, path) "
            "VALUES (%s, %s, %s)",
            (workspace["id"], machine, normalized_path),
        )
        conn.commit()
    except UniqueViolation as e:
        conn.rollback()
        raise ValueError("Path already registered on this machine") from e
    return workspace


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


def create_session(project_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "INSERT INTO sessions (project_id) VALUES (%s) RETURNING *",
        (project_id,),
    ).fetchone()
    conn.commit()
    return dict(row)


def end_session(session_id: str, summary: str | None = None) -> dict:
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE sessions
        SET ended_at = now(), summary = %s
        WHERE id = %s
        RETURNING *
        """,
        (summary, session_id),
    ).fetchone()
    conn.commit()
    return dict(row)


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


def get_latest_session_summary(project_id: str) -> dict | None:
    """Get the most recent completed session with a summary."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM sessions
        WHERE project_id = %s AND ended_at IS NOT NULL AND summary IS NOT NULL
        ORDER BY ended_at DESC
        LIMIT 1
        """,
        (project_id,),
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
) -> dict:
    if type not in ALLOWED_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(ALLOWED_TYPES))}")

    conn = get_connection()
    meta = json.dumps(metadata or {})

    chash = _content_hash(title, content)

    if topic_key:
        existing = conn.execute(
            """
            SELECT id FROM observations
            WHERE project_id = %s AND topic_key = %s AND deleted_at IS NULL
            """,
            (project_id, topic_key),
        ).fetchone()
        if existing:
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

    # Dedup: check for identical content in same project within 15 min window
    existing_dup = conn.execute(
        """
        SELECT id FROM observations
        WHERE project_id = %s
          AND content_hash = %s
          AND deleted_at IS NULL
          AND created_at > now() - interval '15 minutes'
        LIMIT 1
        """,
        (project_id, chash),
    ).fetchone()
    if existing_dup:
        row = conn.execute(
            """
            UPDATE observations
            SET duplicate_count = duplicate_count + 1,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (existing_dup["id"],),
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
    project_id: str,
    query: str,
    type: str | None = None,
    limit: int = 10,
    workspace_id: str | None = None,
) -> list[dict]:
    conn = get_connection()
    if workspace_id:
        base = """
            SELECT o.*, ts_rank(o.search_vector, q) AS rank
            FROM observations o
            JOIN projects p ON p.id = o.project_id,
            plainto_tsquery('simple', %s) q
            WHERE o.project_id = %s
              AND p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.search_vector @@ q
        """
        params: list = [query, project_id, workspace_id]
    else:
        base = """
            SELECT *, ts_rank(search_vector, query) AS rank
            FROM observations, plainto_tsquery('simple', %s) query
            WHERE project_id = %s
              AND deleted_at IS NULL
              AND search_vector @@ query
        """
        params = [query, project_id]

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


def get_recent_observations(
    project_id: str,
    limit: int = 20,
    workspace_id: str | None = None,
) -> list[dict]:
    conn = get_connection()
    if workspace_id:
        rows = conn.execute(
            """
            SELECT o.* FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE o.project_id = %s
              AND p.workspace_id = %s
              AND o.deleted_at IS NULL
            ORDER BY COALESCE(o.occurred_at, o.created_at) DESC
            LIMIT %s
            """,
            (project_id, workspace_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE project_id = %s AND deleted_at IS NULL
            ORDER BY COALESCE(occurred_at, created_at) DESC
            LIMIT %s
            """,
            (project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_observation(observation_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM observations WHERE id = %s AND deleted_at IS NULL",
        (observation_id,),
    ).fetchone()
    return dict(row) if row else None


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
    project_id: str,
    embedding: list[float],
    limit: int = 10,
    workspace_id: str | None = None,
) -> list[dict]:
    conn = get_connection()
    query_embedding = str(embedding)
    if workspace_id:
        rows = conn.execute(
            """
            SELECT o.*, 1 - (o.embedding <=> %s::vector) AS similarity
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            WHERE o.project_id = %s
              AND p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND o.embedding IS NOT NULL
            ORDER BY o.embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, project_id, workspace_id, query_embedding, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *, 1 - (embedding <=> %s::vector) AS similarity
            FROM observations
            WHERE project_id = %s
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, project_id, query_embedding, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_hybrid(
    project_id: str,
    query: str,
    embedding: list[float],
    limit: int = 10,
    workspace_id: str | None = None,
) -> list[dict]:
    conn = get_connection()
    query_embedding = str(embedding)
    k = 60  # RRF constant

    cte = """
        WITH keyword AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       ORDER BY
                           ts_rank(search_vector, plainto_tsquery('simple', %s)) DESC
                   ) AS rank
            FROM observations
            WHERE project_id = %s
              AND deleted_at IS NULL
              AND search_vector @@ plainto_tsquery('simple', %s)
        ),
        semantic AS (
            SELECT id,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM observations
            WHERE project_id = %s
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
        )
    """
    if workspace_id:
        tail = """
            SELECT o.*,
                   COALESCE(1.0 / (%s + k.rank), 0)
                       + COALESCE(1.0 / (%s + s.rank), 0) AS rrf_score
            FROM observations o
            JOIN projects p ON p.id = o.project_id
            LEFT JOIN keyword k ON o.id = k.id
            LEFT JOIN semantic s ON o.id = s.id
            WHERE o.project_id = %s
              AND p.workspace_id = %s
              AND o.deleted_at IS NULL
              AND (k.id IS NOT NULL OR s.id IS NOT NULL)
            ORDER BY rrf_score DESC
            LIMIT %s
        """
        params = [
            query,
            project_id,
            query,
            query_embedding,
            project_id,
            k,
            k,
            project_id,
            workspace_id,
            limit,
        ]
    else:
        tail = """
            SELECT o.*,
                   COALESCE(1.0 / (%s + k.rank), 0)
                       + COALESCE(1.0 / (%s + s.rank), 0) AS rrf_score
            FROM observations o
            LEFT JOIN keyword k ON o.id = k.id
            LEFT JOIN semantic s ON o.id = s.id
            WHERE o.project_id = %s
              AND o.deleted_at IS NULL
              AND (k.id IS NOT NULL OR s.id IS NOT NULL)
            ORDER BY rrf_score DESC
            LIMIT %s
        """
        params = [
            query,
            project_id,
            query,
            query_embedding,
            project_id,
            k,
            k,
            project_id,
            limit,
        ]
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
