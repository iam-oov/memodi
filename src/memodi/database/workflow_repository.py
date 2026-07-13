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


def _classify_scope(
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> str:
    """Classify plan scope based on AC and task count."""
    if len(tasks) <= 2 and len(acceptance_criteria) <= 1:
        return "quick-fix"
    elif len(tasks) <= 5:
        return "standard"
    return "complex"


def _validate_plan(
    acceptance_criteria: list[dict],
    tasks: list[dict],
    scope: str,
) -> list[str]:
    """Validate AC and task structure. Returns list of warnings."""
    warnings = []
    ac_ids: set[str] = set()

    for i, ac in enumerate(acceptance_criteria):
        if "id" not in ac:
            raise ValueError(
                f"Acceptance criterion {i} missing 'id' field. "
                "Use format: {\"id\": \"AC-1\", \"description\": \"...\"}"
            )
        if "description" not in ac:
            raise ValueError(
                f"Acceptance criterion '{ac['id']}' missing 'description'. "
                "Use Given/When/Then format for clarity."
            )
        ac_ids.add(ac["id"])

    for i, task in enumerate(tasks):
        if "name" not in task:
            raise ValueError(
                f"Task {i} missing 'name' field. "
                "Use format: {\"name\": \"...\", \"criteria\": [\"AC-1\"]}"
            )
        if "status" not in task:
            task["status"] = "pending"
        criteria = task.get("criteria", [])
        # Only warn about missing criteria for standard+ plans
        if not criteria and scope != "quick-fix":
            warnings.append(
                f"Task '{task['name']}' has no 'criteria' linking to ACs"
            )
        for cid in criteria:
            if cid not in ac_ids:
                raise ValueError(
                    f"Task '{task['name']}' references unknown criterion "
                    f"'{cid}'. Valid IDs: {sorted(ac_ids)}"
                )

    if scope == "complex":
        warnings.append(
            f"Complex plan ({len(tasks)} tasks). "
            "Consider splitting into multiple smaller plans for better focus."
        )

    return warnings


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

    scope = _classify_scope(acceptance_criteria, tasks)
    warnings = _validate_plan(acceptance_criteria, tasks, scope)

    # Store scope in the result JSONB (avoids migration)
    existing_result = (
        conn.execute(
            "SELECT result FROM workflows WHERE id = %s", (workflow_id,)
        ).fetchone()["result"]
        or {}
    )
    existing_result["scope"] = scope

    row = conn.execute(
        """
        UPDATE workflows
        SET acceptance_criteria = %s,
            tasks = %s,
            result = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (
            json.dumps(acceptance_criteria),
            json.dumps(tasks),
            json.dumps(existing_result),
            workflow_id,
        ),
    ).fetchone()
    conn.commit()
    result = dict(row)
    result["_scope"] = scope
    if warnings:
        result["_warnings"] = warnings
    return result


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
