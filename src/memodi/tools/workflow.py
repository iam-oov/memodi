import json

from memodi.database import repository, workflow_repository
from memodi.database.connection import ensure_schema
from memodi.tools.errors import handle_errors


def _ensure() -> None:
    ensure_schema()


@handle_errors
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


@handle_errors
def update_plan(
    workflow_id: str,
    acceptance_criteria: list[dict],
    tasks: list[dict],
) -> str:
    _ensure()
    wf = workflow_repository.update_plan(workflow_id, acceptance_criteria, tasks)
    return json.dumps(wf, default=str)


@handle_errors
def approve_plan(workflow_id: str, notes: str | None = None) -> str:
    _ensure()
    wf = workflow_repository.transition_phase(workflow_id, "apply", notes)
    return json.dumps(wf, default=str)


@handle_errors
def apply_done(workflow_id: str, notes: str | None = None) -> str:
    _ensure()
    wf = workflow_repository.transition_phase(workflow_id, "verify", notes)
    return json.dumps(wf, default=str)


@handle_errors
def verify(
    workflow_id: str,
    result: dict,
    passed: bool,
    notes: str | None = None,
) -> str:
    _ensure()
    wf = workflow_repository.get_workflow(workflow_id)
    if wf is None:
        raise ValueError(f"Workflow {workflow_id} not found")

    # Validate ac_results against stored acceptance criteria
    warnings = []
    ac_results = result.get("ac_results", [])
    if ac_results:
        stored_acs = wf.get("acceptance_criteria") or []
        stored_ids = {ac["id"] for ac in stored_acs if "id" in ac}
        result_ids = {r["id"] for r in ac_results if "id" in r}
        missing = stored_ids - result_ids
        if missing:
            warnings.append(
                f"ACs not evaluated: {sorted(missing)}"
            )
        unknown = result_ids - stored_ids
        if unknown:
            warnings.append(
                f"Unknown AC IDs in results: {sorted(unknown)}"
            )

    workflow_repository.update_result(workflow_id, result)
    to_phase = "unify" if passed else "apply"
    wf = workflow_repository.transition_phase(workflow_id, to_phase, notes)
    response = json.loads(json.dumps(wf, default=str))
    if warnings:
        response["_warnings"] = warnings
    return json.dumps(response, default=str)


@handle_errors
def unify(workflow_id: str, summary: str, notes: str | None = None) -> str:
    _ensure()
    wf = workflow_repository.get_workflow(workflow_id)
    if wf is None:
        raise ValueError(f"Workflow {workflow_id} not found")

    existing_result = wf.get("result") or {}
    existing_result["summary"] = summary

    # Auto-generate AC summary table
    stored_acs = wf.get("acceptance_criteria") or []
    ac_results = existing_result.get("ac_results", [])
    if stored_acs:
        results_by_id = {r["id"]: r for r in ac_results if "id" in r}
        ac_summary = []
        for ac in stored_acs:
            ac_id = ac.get("id", "?")
            evaluated = results_by_id.get(ac_id, {})
            ac_summary.append({
                "id": ac_id,
                "description": ac.get("description", ""),
                "status": evaluated.get("status", "not_evaluated"),
                "evidence": evaluated.get("evidence", ""),
            })
        existing_result["ac_summary"] = ac_summary

    workflow_repository.update_result(workflow_id, existing_result)
    wf = workflow_repository.transition_phase(workflow_id, "completed", notes)
    return json.dumps(wf, default=str)


@handle_errors
def progress(project: str) -> str:
    _ensure()
    proj = repository.get_or_create_project(project)
    active = workflow_repository.get_active_workflow(proj["id"])
    if active is None:
        return json.dumps({"status": "no active workflow", "project": project})
    return json.dumps(active, default=str)


@handle_errors
def task_update(
    workflow_id: str,
    task_index: int,
    status: str,
    notes: str | None = None,
) -> str:
    _ensure()
    wf = workflow_repository.update_task_status(workflow_id, task_index, status, notes)
    return json.dumps(wf, default=str)
