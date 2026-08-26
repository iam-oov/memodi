"""The digest is shown to the USER at startup, so a leak here is the most
visible one there is: a sibling repo's todo list greeting you in a folder that
has nothing to do with it."""

import json
import uuid

import pytest

from memodi.database.connection import ensure_schema
from memodi.tools.memory import digest_for_session_start
from memodi.tools.session import session_end, session_start

SUMMARY = """## Goal
Something in project A.

## Next Steps
- Finish the thing in A
"""


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


def _close_session_with_summary(ws: dict, path: str, summary: str) -> None:
    common = dict(user_id=ws["user_id"], machine=ws["machine"])
    session_start(path=path, **common)
    session_end(path=path, summary=summary, **common)


def _digest(ws: dict, path: str) -> str:
    return json.loads(
        digest_for_session_start(
            path=path, user_id=ws["user_id"], machine=ws["machine"]
        )
    )["digest"]


def test_digest_does_not_show_a_sibling_projects_next_steps(registered_workspace):
    """The reported bug: opening an unrelated folder greeted the user with
    another project's pending list."""
    worked_in = f"{registered_workspace['root']}/test-proj-a-{uuid.uuid4()}"
    unrelated = f"{registered_workspace['root']}/test-proj-b-{uuid.uuid4()}"

    _close_session_with_summary(registered_workspace, worked_in, SUMMARY)

    assert "Finish the thing in A" not in _digest(registered_workspace, unrelated)


def test_digest_shows_its_own_projects_next_steps(registered_workspace):
    folder = f"{registered_workspace['root']}/test-proj-a-{uuid.uuid4()}"
    _close_session_with_summary(registered_workspace, folder, SUMMARY)

    assert "Finish the thing in A" in _digest(registered_workspace, folder)


def test_digest_in_a_child_inherits_the_root_containers_next_steps(
    registered_workspace,
):
    """Container-level work is the shared layer a child is entitled to — the
    same rule the observation read paths follow."""
    child = f"{registered_workspace['root']}/test-proj-a-{uuid.uuid4()}"
    root_summary = SUMMARY.replace(
        "Finish the thing in A", "Finish the container-wide thing"
    )
    _close_session_with_summary(
        registered_workspace, registered_workspace["root"], root_summary
    )

    assert "Finish the container-wide thing" in _digest(registered_workspace, child)


def test_digest_at_the_root_spans_every_project(registered_workspace):
    folder = f"{registered_workspace['root']}/test-proj-a-{uuid.uuid4()}"
    _close_session_with_summary(registered_workspace, folder, SUMMARY)

    assert "Finish the thing in A" in _digest(
        registered_workspace, registered_workspace["root"]
    )


def test_digest_is_empty_in_a_folder_with_no_project_and_no_container(
    registered_workspace,
):
    """The empty-scope trap: no project here and none at the root means
    NOTHING is in scope. The SQL predicate reads an empty id list as
    'no filter', so falling through would hand back the whole workspace —
    which is the leak, not the fix."""
    worked_in = f"{registered_workspace['root']}/test-proj-a-{uuid.uuid4()}"
    _close_session_with_summary(registered_workspace, worked_in, SUMMARY)

    fresh = f"{registered_workspace['root']}/never-opened-{uuid.uuid4()}"
    assert _digest(registered_workspace, fresh) == ""
