import json

from memodi.database.connection import get_connection

VALID_TRANSITIONS: dict[str, list[str]] = {
    "plan": ["apply", "abandoned"],
    "apply": ["verify", "abandoned"],
    "verify": ["unify", "apply", "abandoned"],
    "unify": ["completed", "abandoned"],
}

VALID_TASK_STATUSES = {"pending", "in_progress", "done", "blocked"}


def create_workflow(project_id: str, name: str, objective: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        """
        INSERT INTO workflows (project_id, name, objective)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (project_id, name, objective),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_workflow(workflow_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM workflows WHERE id = %s",
        (workflow_id,),
    ).fetchone()
    return dict(row) if row else None


def get_active_workflow(project_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM workflows
        WHERE project_id = %s
          AND phase NOT IN ('completed', 'abandoned')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def update_plan(
    workflow_id: str,
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT phase FROM workflows WHERE id = %s",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Workflow {workflow_id} not found")
    if row["phase"] != "plan":
        raise ValueError(
            f"Cannot update plan in phase '{row['phase']}' — only allowed in 'plan'"
        )
    row = conn.execute(
        """
        UPDATE workflows
        SET acceptance_criteria = %s,
            tasks = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (json.dumps(acceptance_criteria), json.dumps(tasks), workflow_id),
    ).fetchone()
    conn.commit()
    return dict(row)


def transition_phase(
    workflow_id: str,
    to_phase: str,
    notes: str | None = None,
) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT phase FROM workflows WHERE id = %s",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Workflow {workflow_id} not found")

    from_phase = row["phase"]
    allowed = VALID_TRANSITIONS.get(from_phase, [])
    if to_phase not in allowed:
        raise ValueError(
            f"Invalid transition: '{from_phase}' → '{to_phase}'. "
            f"Allowed from '{from_phase}': {allowed}"
        )

    conn.execute(
        """
        INSERT INTO workflow_transitions (workflow_id, from_phase, to_phase, notes)
        VALUES (%s, %s, %s, %s)
        """,
        (workflow_id, from_phase, to_phase, notes),
    )

    if to_phase == "completed":
        row = conn.execute(
            """
            UPDATE workflows
            SET phase = %s,
                updated_at = now(),
                completed_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (to_phase, workflow_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            UPDATE workflows
            SET phase = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (to_phase, workflow_id),
        ).fetchone()

    conn.commit()
    return dict(row)


def update_task_status(
    workflow_id: str,
    task_index: int,
    status: str,
    notes: str | None = None,
) -> dict:
    if status not in VALID_TASK_STATUSES:
        raise ValueError(
            f"Invalid task status '{status}'. "
            f"Must be one of: {sorted(VALID_TASK_STATUSES)}"
        )

    conn = get_connection()
    row = conn.execute(
        "SELECT tasks FROM workflows WHERE id = %s",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Workflow {workflow_id} not found")

    tasks = row["tasks"] if row["tasks"] is not None else []
    if task_index < 0 or task_index >= len(tasks):
        raise ValueError(
            f"Task index {task_index} out of range — workflow has {len(tasks)} tasks"
        )

    tasks[task_index]["status"] = status
    if notes is not None:
        tasks[task_index]["notes"] = notes

    row = conn.execute(
        """
        UPDATE workflows
        SET tasks = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (json.dumps(tasks), workflow_id),
    ).fetchone()
    conn.commit()
    return dict(row)


def update_result(workflow_id: str, result: dict) -> dict:
    conn = get_connection()
    row = conn.execute(
        """
        UPDATE workflows
        SET result = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (json.dumps(result), workflow_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Workflow {workflow_id} not found")
    conn.commit()
    return dict(row)


def list_workflows(
    project_id: str,
    include_completed: bool = False,
) -> list[dict]:
    conn = get_connection()
    if include_completed:
        rows = conn.execute(
            """
            SELECT * FROM workflows
            WHERE project_id = %s
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM workflows
            WHERE project_id = %s
              AND phase NOT IN ('completed', 'abandoned')
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]
