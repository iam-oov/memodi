import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.session import session_end, session_start
from tests.conftest import _path


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-session-{uuid.uuid4()}"


def test_session_start_creates_session(registered_workspace, project_name):
    result = json.loads(
        session_start(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )

    assert result["started"] is True
    assert result["project"] == project_name
    assert "session_id" in result


def test_session_start_closes_previous(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    first = json.loads(session_start(**kwargs))
    second = json.loads(session_start(**kwargs))

    assert first["session_id"] != second["session_id"]

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    active = repository.get_active_session(proj["id"])
    assert str(active["id"]) == second["session_id"]


def test_session_end_with_summary(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    start_result = json.loads(
        session_start(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
        )
    )
    session_id = start_result["session_id"]

    end_result = json.loads(
        session_end(
            path=path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            summary="Worked on auth module",
        )
    )

    assert end_result["ended"] is True
    assert end_result["session_id"] == session_id
    assert end_result["summary"] == "Worked on auth module"


def test_session_end_no_active_session(registered_workspace, project_name):
    result = json.loads(
        session_end(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            summary="nothing",
        )
    )

    assert result["ended"] is False
    assert "error" in result


def test_session_end_clears_active(registered_workspace, project_name):
    path = _path(registered_workspace, project_name)
    kwargs = {
        "path": path,
        "user_id": registered_workspace["user_id"],
        "machine": registered_workspace["machine"],
    }
    session_start(**kwargs)
    session_end(**kwargs, summary="done")

    proj = repository.get_or_create_project(
        project_name, workspace_id=registered_workspace["workspace"]["id"]
    )
    active = repository.get_active_session(proj["id"])
    assert active is None
