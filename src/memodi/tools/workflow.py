import json

from memodi.database import repository, workflow_repository
from memodi.database.connection import ensure_schema


def _ensure() -> None:
    ensure_schema()


def plan(project: str, name: str, objective: str) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    active = workflow_repository.get_active_workflow(proj["id"])
    if active:
        return json.dumps(active, default=str)
    wf = workflow_repository.create_workflow(
        project_id=proj["id"],
        name=name,
        objective=objective,
    )
    return json.dumps(wf, default=str)


def update_plan(
    workflow_id: str,
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> str:
    _ensure()
    wf = workflow_repository.update_plan(
        workflow_id, acceptance_criteria, tasks
    )
    return json.dumps(wf, default=str)


def approve_plan(workflow_id: str, notes: str | None = None) -> str:
    _ensure()
    wf = workflow_repository.transition_phase(workflow_id, "apply", notes)
    return json.dumps(wf, default=str)


def apply_done(workflow_id: str, notes: str | None = None) -> str:
    _ensure()
    wf = workflow_repository.transition_phase(
        workflow_id, "verify", notes
    )
    return json.dumps(wf, default=str)


def verify(
    workflow_id: str,
    result: dict,
    passed: bool,
    notes: str | None = None,
) -> str:
    _ensure()
    workflow_repository.update_result(workflow_id, result)
    to_phase = "unify" if passed else "apply"
    wf = workflow_repository.transition_phase(
        workflow_id, to_phase, notes
    )
    return json.dumps(wf, default=str)


def unify(
    workflow_id: str, summary: str, notes: str | None = None
) -> str:
    _ensure()
    wf = workflow_repository.get_workflow(workflow_id)
    if wf is None:
        raise ValueError(f"Workflow {workflow_id} not found")
    existing_result = wf.get("result") or {}
    existing_result["summary"] = summary
    workflow_repository.update_result(workflow_id, existing_result)
    wf = workflow_repository.transition_phase(
        workflow_id, "completed", notes
    )
    return json.dumps(wf, default=str)


def progress(project: str) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    active = workflow_repository.get_active_workflow(proj["id"])
    if active is None:
        return json.dumps(
            {"status": "no active workflow", "project": project}
        )
    return json.dumps(active, default=str)


def task_update(
    workflow_id: str,
    task_index: int,
    status: str,
    notes: str | None = None,
) -> str:
    _ensure()
    wf = workflow_repository.update_task_status(
        workflow_id, task_index, status, notes
    )
    return json.dumps(wf, default=str)
