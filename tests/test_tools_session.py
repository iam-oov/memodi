import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.session import session_end, session_start


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-session-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def cleanup(project_name):
    yield
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    conn.execute(
        """
        DELETE FROM sessions
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE name = %s)
        """,
        (project_name,),
    )
    conn.execute("DELETE FROM projects WHERE name = %s", (project_name,))
    conn.commit()


def test_session_start_creates_session(project_name):
    result = json.loads(session_start(project=project_name))

    assert result["started"] is True
    assert result["project"] == project_name
    assert "session_id" in result


def test_session_start_closes_previous(project_name):
    first = json.loads(session_start(project=project_name))
    second = json.loads(session_start(project=project_name))

    assert first["session_id"] != second["session_id"]

    # First session should be closed now
    proj = repository.get_or_create_project(project_name)
    active = repository.get_active_session(proj["id"])
    assert str(active["id"]) == second["session_id"]


def test_session_end_with_summary(project_name):
    start_result = json.loads(session_start(project=project_name))
    session_id = start_result["session_id"]

    end_result = json.loads(
        session_end(project=project_name, summary="Worked on auth module")
    )

    assert end_result["ended"] is True
    assert end_result["session_id"] == session_id
    assert end_result["summary"] == "Worked on auth module"


def test_session_end_no_active_session(project_name):
    result = json.loads(session_end(project=project_name, summary="nothing"))

    assert result["ended"] is False
    assert "error" in result


def test_session_end_clears_active(project_name):
    session_start(project=project_name)
    session_end(project=project_name, summary="done")

    proj = repository.get_or_create_project(project_name)
    active = repository.get_active_session(proj["id"])
    assert active is None
