import hashlib
import json

from memodi.database.connection import get_connection


def _content_hash(title: str, content: str) -> str:
    """SHA-256 hash of title+content for deduplication."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()

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


def get_or_create_workspace(name: str) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM workspaces WHERE name = %s", (name,)).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "INSERT INTO workspaces (name) VALUES (%s) RETURNING *", (name,)
    ).fetchone()
    conn.commit()
    return dict(row)


def get_or_create_project(name: str, workspace_id: str | None = None) -> dict:
    conn = get_connection()
    if workspace_id:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = %s AND workspace_id = %s",
            (name, workspace_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = %s ORDER BY workspace_id ASC NULLS LAST LIMIT 1",
            (name,),
        ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "INSERT INTO projects (name, workspace_id) VALUES (%s, %s) RETURNING *",
        (name, workspace_id),
    ).fetchone()
    conn.commit()
    return dict(row)


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
                        revision_count = revision_count + 1,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (title, content, type, session_id, meta, chash, existing["id"]),
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
                 topic_key, metadata, embedding, content_hash)
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
                str(embedding),
                chash,
            ),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO observations
                (project_id, session_id, type, title, content,
                 topic_key, metadata, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (project_id, session_id, type, title, content, topic_key, meta, chash),
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
    type: str | None = None,
    limit: int = 10,
) -> list[dict]:
    conn = get_connection()
    base = """
        SELECT o.*, p.name AS project_name, ts_rank(o.search_vector, q) AS rank
        FROM observations o
        JOIN projects p ON p.id = o.project_id,
        plainto_tsquery('simple', %s) q
        WHERE o.deleted_at IS NULL
          AND o.search_vector @@ q
    """
    params: list = [query]

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
            ORDER BY o.created_at DESC
            LIMIT %s
            """,
            (project_id, workspace_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE project_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
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


def list_projects(workspace_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if workspace_id:
        rows = conn.execute(
            "SELECT * FROM projects WHERE workspace_id = %s ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_workspaces() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT w.*, COUNT(p.id) AS project_count
        FROM workspaces w
        LEFT JOIN projects p ON p.workspace_id = w.id
        GROUP BY w.id
        ORDER BY w.created_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def link_project_to_workspace(
    project_name: str,
    workspace_name: str,
) -> dict:
    conn = get_connection()
    ws = get_or_create_workspace(workspace_name)
    row = conn.execute(
        "SELECT * FROM projects WHERE name = %s AND workspace_id IS NULL",
        (project_name,),
    ).fetchone()
    if row:
        row = conn.execute(
            """
            UPDATE projects SET workspace_id = %s, updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (ws["id"], row["id"]),
        ).fetchone()
        conn.commit()
        return dict(row)
    return get_or_create_project(project_name, workspace_id=ws["id"])


def register_path(path: str, workspace_name: str) -> dict:
    conn = get_connection()
    ws = get_or_create_workspace(workspace_name)
    existing = conn.execute(
        "SELECT * FROM workspace_paths WHERE path = %s",
        (path,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE workspace_paths
            SET workspace_id = %s WHERE path = %s
            RETURNING *
            """,
            (ws["id"], path),
        )
        conn.commit()
    else:
        conn.execute(
            """
            INSERT INTO workspace_paths (workspace_id, path)
            VALUES (%s, %s)
            """,
            (ws["id"], path),
        )
        conn.commit()
    return {"path": path, "workspace": workspace_name}


def resolve_path(path: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT w.* FROM workspaces w
        JOIN workspace_paths wp ON wp.workspace_id = w.id
        WHERE wp.path = %s
        """,
        (path,),
    ).fetchone()
    return dict(row) if row else None


def delete_workspace(workspace_name: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM workspaces WHERE name = %s",
        (workspace_name,),
    ).fetchone()
    if not row:
        return False
    ws_id = row["id"]
    conn.execute(
        "DELETE FROM workspace_paths WHERE workspace_id = %s",
        (ws_id,),
    )
    conn.execute(
        "UPDATE projects SET workspace_id = NULL WHERE workspace_id = %s",
        (ws_id,),
    )
    conn.execute("DELETE FROM workspaces WHERE id = %s", (ws_id,))
    conn.commit()
    return True


def rename_workspace(old_name: str, new_name: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE workspaces SET name = %s, updated_at = now()
        WHERE name = %s RETURNING *
        """,
        (new_name, old_name),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def get_project_workspace(project_name: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT w.* FROM workspaces w
        JOIN projects p ON p.workspace_id = w.id
        WHERE p.name = %s
        """,
        (project_name,),
    ).fetchone()
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

    sql = """
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
    rows = conn.execute(sql, params).fetchall()
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
