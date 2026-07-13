import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema
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
from tests.conftest import _path


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-wf-{uuid.uuid4()}"


def test_full_workflow_cycle(registered_workspace, project_name):
    wf = json.loads(
        plan(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Add login feature",
            objective="Implement JWT auth",
        )
    )
    wf_id = wf["id"]
    assert wf["phase"] == "plan"

    update_plan(
        wf_id,
        acceptance_criteria=[{"id": "AC-1", "description": "JWT issued on login"}],
        tasks=[
            {
                "name": "Add /login endpoint",
                "status": "pending",
                "criteria": ["AC-1"],
                "files": [],
            }
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


def test_verify_failure_returns_to_apply(registered_workspace, project_name):
    wf = json.loads(
        plan(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Fix null pointer",
            objective="Handle null user gracefully",
        )
    )
    wf_id = wf["id"]

    json.loads(approve_plan(wf_id))
    json.loads(apply_done(wf_id))

    wf = json.loads(
        verify(wf_id, result={"failure": "still crashes on null"}, passed=False)
    )
    assert wf["phase"] == "apply"


def test_invalid_transition_raises(registered_workspace, project_name):
    wf = json.loads(
        plan(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Refactor DB layer",
            objective="Extract repository pattern",
        )
    )
    wf_id = wf["id"]

    from memodi.database.workflow_repository import transition_phase

    with pytest.raises(ValueError, match="Invalid transition"):
        transition_phase(wf_id, "verify")


def test_task_update(registered_workspace, project_name):
    wf = json.loads(
        plan(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Upgrade deps",
            objective="Bump all dependencies",
        )
    )
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


def test_progress_shows_active_workflow(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    wf_created = json.loads(
        plan(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Add caching",
            objective="Cache DB queries with Redis",
        )
    )

    result = json.loads(
        progress(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["id"] == wf_created["id"]
    assert result["phase"] == "plan"


def test_workflow_responses_never_leak_project_id(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    wf = json.loads(
        plan(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Serialization audit",
            objective="No internal FK leak",
        )
    )
    assert "project_id" not in wf
    wf_id = wf["id"]

    wf = json.loads(
        update_plan(
            wf_id,
            acceptance_criteria=[],
            tasks=[{"name": "Check boundary", "status": "pending", "files": []}],
        )
    )
    assert "project_id" not in wf

    wf = json.loads(approve_plan(wf_id))
    assert "project_id" not in wf

    wf = json.loads(apply_done(wf_id))
    assert "project_id" not in wf

    wf = json.loads(verify(wf_id, result={"checks": "ok"}, passed=True))
    assert "project_id" not in wf

    wf = json.loads(unify(wf_id, summary="Done"))
    assert "project_id" not in wf

    result = json.loads(
        progress(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert "project_id" not in result


def test_update_plan_twice_succeeds_and_overwrites_scope(
    registered_workspace, project_name
):
    wf = json.loads(
        plan(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            name="Iterate on plan",
            objective="Refine the plan across two passes",
        )
    )
    wf_id = wf["id"]

    first = json.loads(
        update_plan(
            wf_id,
            acceptance_criteria=[{"id": "AC-1", "description": "Single criterion"}],
            tasks=[{"name": "Only task", "status": "pending", "criteria": ["AC-1"]}],
        )
    )
    assert first["_scope"] == "quick-fix"
    assert first["result"]["scope"] == "quick-fix"

    second = json.loads(
        update_plan(
            wf_id,
            acceptance_criteria=[
                {"id": "AC-1", "description": "First"},
                {"id": "AC-2", "description": "Second"},
            ],
            tasks=[
                {"name": f"Task {i}", "status": "pending", "criteria": ["AC-1"]}
                for i in range(6)
            ],
        )
    )
    assert second["phase"] == "plan"
    assert second["_scope"] == "complex"
    assert second["result"]["scope"] == "complex"


def test_progress_no_active_workflow(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    assert proj is not None

    result = json.loads(
        progress(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    assert result["status"] == "no active workflow"
    assert result["project"] == project_name
