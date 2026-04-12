import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.workflow import (
    apply_done,
    approve_plan,
    plan,
    progress,
    task_update,
    unify,
    update_plan,
    verify,
)


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-wf-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(project_name):
    yield
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM workflow_transitions
        WHERE workflow_id IN (
            SELECT w.id FROM workflows w
            JOIN projects p ON p.id = w.project_id
            WHERE p.name = %s
        )
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM workflows
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.commit()


def test_full_workflow_cycle(project_name):
    wf = json.loads(plan(project_name, "Add login feature", "Implement JWT auth"))
    wf_id = wf["id"]
    assert wf["phase"] == "plan"

    update_plan(
        wf_id,
        acceptance_criteria=[{"id": "AC-1", "description": "JWT issued on login"}],
        tasks=[
            {"name": "Add /login endpoint", "status": "pending", "criteria": ["AC-1"], "files": []}
        ],
    )

    wf = json.loads(approve_plan(wf_id, notes="Plan looks good"))
    assert wf["phase"] == "apply"

    wf = json.loads(apply_done(wf_id, notes="Endpoint implemented"))
    assert wf["phase"] == "verify"

    wf = json.loads(
        verify(wf_id, result={"checks": "all passed"}, passed=True, notes="LGTM")
    )
    assert wf["phase"] == "unify"

    wf = json.loads(unify(wf_id, summary="Feature shipped and verified"))
    assert wf["phase"] == "completed"
    assert wf["completed_at"] is not None


def test_verify_failure_returns_to_apply(project_name):
    wf = json.loads(
        plan(project_name, "Fix null pointer", "Handle null user gracefully")
    )
    wf_id = wf["id"]

    json.loads(approve_plan(wf_id))
    json.loads(apply_done(wf_id))

    wf = json.loads(
        verify(wf_id, result={"failure": "still crashes on null"}, passed=False)
    )
    assert wf["phase"] == "apply"


def test_invalid_transition_raises(project_name):
    wf = json.loads(
        plan(project_name, "Refactor DB layer", "Extract repository pattern")
    )
    wf_id = wf["id"]

    from memodi.database.workflow_repository import transition_phase

    with pytest.raises(ValueError, match="Invalid transition"):
        transition_phase(wf_id, "verify")


def test_task_update(project_name):
    wf = json.loads(plan(project_name, "Upgrade deps", "Bump all dependencies"))
    wf_id = wf["id"]

    update_plan(
        wf_id,
        acceptance_criteria=[],
        tasks=[
            {"name": "Bump psycopg", "status": "pending", "files": []},
            {"name": "Bump fastmcp", "status": "pending", "files": []},
        ],
    )

    wf = json.loads(task_update(wf_id, 0, "done", notes="bumped to 3.2"))
    assert wf["tasks"][0]["status"] == "done"
    assert wf["tasks"][0]["notes"] == "bumped to 3.2"
    assert wf["tasks"][1]["status"] == "pending"

    wf = json.loads(task_update(wf_id, 1, "in_progress"))
    assert wf["tasks"][1]["status"] == "in_progress"


def test_progress_shows_active_workflow(project_name):
    wf_created = json.loads(
        plan(project_name, "Add caching", "Cache DB queries with Redis")
    )

    result = json.loads(progress(project_name))
    assert result["id"] == wf_created["id"]
    assert result["phase"] == "plan"


def test_progress_no_active_workflow(project_name):
    proj = repository.get_or_create_project(project_name)
    assert proj is not None

    result = json.loads(progress(project_name))
    assert result["status"] == "no active workflow"
    assert result["project"] == project_name
