import json
import uuid

import psycopg
import pytest

from memodi.database import auth_repository, repository, workflow_repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.tools.memory import merge_projects
from tests.conftest import cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def projects(registered_workspace):
    created: list[str] = []
    workspace_id = registered_workspace["workspace"]["id"]

    def make(suffix: str = "proj") -> dict:
        name = f"test-merge-{suffix}-{uuid.uuid4()}"
        project = repository.get_or_create_project(name, workspace_id=workspace_id)
        created.append(project["id"])
        return project

    yield make

    if created:
        cleanup_rows(
            """
            DELETE FROM workflow_transitions WHERE workflow_id IN
                (SELECT id FROM workflows WHERE project_id = ANY(%s))
            """,
            (created,),
        )
        cleanup_rows("DELETE FROM workflows WHERE project_id = ANY(%s)", (created,))
        cleanup_rows("DELETE FROM observations WHERE project_id = ANY(%s)", (created,))
        cleanup_rows("DELETE FROM sessions WHERE project_id = ANY(%s)", (created,))
        cleanup_rows("DELETE FROM projects WHERE id = ANY(%s)", (created,))


def test_merge_moves_observations_sessions_workflows(projects):
    source = projects("source")
    target = projects("target")

    repository.save_observation(source["id"], "Obs A", "content a", "decision")
    session = repository.create_session(source["id"])
    workflow_repository.create_workflow(source["id"], "wf-a", "test workflow")

    result = repository.merge_projects(source["id"], target["id"])

    assert result["observations_moved"] == 1
    assert result["sessions_moved"] == 1
    assert result["workflows_moved"] == 1

    conn = get_connection()
    obs = conn.execute(
        "SELECT project_id FROM observations WHERE title = %s", ("Obs A",)
    ).fetchone()
    assert obs["project_id"] == target["id"]

    remaining_session = conn.execute(
        "SELECT project_id FROM sessions WHERE id = %s", (session["id"],)
    ).fetchone()
    assert remaining_session["project_id"] == target["id"]

    wf = conn.execute(
        "SELECT project_id FROM workflows WHERE name = %s", ("wf-a",)
    ).fetchone()
    assert wf["project_id"] == target["id"]


def test_merge_deletes_source_project(projects):
    source = projects("source")
    target = projects("target")

    repository.merge_projects(source["id"], target["id"])

    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM projects WHERE id = %s", (source["id"],)
    ).fetchone()
    assert row is None


def test_merge_supersedes_colliding_topic_key_and_keeps_one_live(projects):
    source = projects("source")
    target = projects("target")

    src_obs = repository.save_observation(
        source["id"], "Source note", "content", "decision", topic_key="shared/topic"
    )
    repository.save_observation(
        target["id"], "Target note", "content", "decision", topic_key="shared/topic"
    )

    result = repository.merge_projects(source["id"], target["id"])

    assert result["topic_key_collisions"] == ["shared/topic"]
    assert str(src_obs["id"]) in result["superseded_observation_ids"]

    conn = get_connection()
    live = conn.execute(
        """
        SELECT id, title FROM observations
        WHERE project_id = %s AND topic_key = %s AND deleted_at IS NULL
        """,
        (target["id"], "shared/topic"),
    ).fetchall()
    assert len(live) == 1
    assert live[0]["title"] == "Target note"

    superseded = conn.execute(
        "SELECT project_id, deleted_at FROM observations WHERE id = %s",
        (src_obs["id"],),
    ).fetchone()
    assert superseded["project_id"] == target["id"]
    assert superseded["deleted_at"] is not None


def test_merge_unknown_source_raises(projects):
    target = projects("target")
    conn = get_connection()

    with pytest.raises(ValueError):
        repository.merge_projects(str(uuid.uuid4()), target["id"])

    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_merge_unknown_target_raises(projects):
    source = projects("source")
    conn = get_connection()

    with pytest.raises(ValueError):
        repository.merge_projects(source["id"], str(uuid.uuid4()))

    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_merge_same_source_and_target_raises(projects):
    project = projects("only")

    with pytest.raises(ValueError):
        repository.merge_projects(project["id"], project["id"])


# --- Tool-layer: ownership enforcement + dry_run ---


def test_tool_merge_dry_run_default_does_not_delete(registered_workspace, projects):
    source = projects("source")
    target = projects("target")
    repository.save_observation(source["id"], "Obs A", "content a", "decision")

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )

    assert result["dry_run"] is True
    assert result["would_move"]["observations"] == 1

    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM projects WHERE id = %s", (source["id"],)
    ).fetchone()
    assert row is not None


def test_tool_merge_executes_when_dry_run_false(registered_workspace, projects):
    source = projects("source")
    target = projects("target")
    repository.save_observation(source["id"], "Obs A", "content a", "decision")

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
            dry_run=False,
        )
    )

    assert result["dry_run"] is False
    assert result["observations_moved"] == 1

    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM projects WHERE id = %s", (source["id"],)
    ).fetchone()
    assert row is None


def test_tool_merge_rejects_when_source_not_owned_by_caller(projects):
    source = projects("source")
    target = projects("target")

    email = f"test-merge-other-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(email)
    try:
        result = json.loads(
            merge_projects(
                source_project_id=source["id"],
                target_project_id=target["id"],
                user_id=other["id"],
            )
        )
        assert result["type"] == "validation"

        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM projects WHERE id = %s", (source["id"],)
        ).fetchone()
        assert row is not None
    finally:
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


def test_tool_merge_rejects_when_target_not_owned_by_caller(
    registered_workspace, projects
):
    source = projects("source")

    email = f"test-merge-other-{uuid.uuid4()}@example.com"
    other = auth_repository.create_user(email)
    other_machine = f"test-machine-{uuid.uuid4()}"
    other_root = f"/tmp/test-merge-other-{uuid.uuid4()}"
    other_ws = repository.workspace_start(
        other["id"], other_machine, other_root, f"test-merge-other-ws-{uuid.uuid4()}"
    )
    other_target = repository.get_or_create_project(
        f"test-merge-other-target-{uuid.uuid4()}", workspace_id=other_ws["id"]
    )

    try:
        result = json.loads(
            merge_projects(
                source_project_id=source["id"],
                target_project_id=other_target["id"],
                user_id=registered_workspace["user_id"],
            )
        )
        assert result["type"] == "validation"
    finally:
        cleanup_rows("DELETE FROM projects WHERE id = %s", (other_target["id"],))
        cleanup_rows(
            "DELETE FROM workspace_paths WHERE workspace_id = %s", (other_ws["id"],)
        )
        cleanup_rows("DELETE FROM workspaces WHERE id = %s", (other_ws["id"],))
        cleanup_rows("DELETE FROM users WHERE id = %s", (other["id"],))


# --- dry_run must surface what the merge would HIDE ---


def test_dry_run_reports_topic_key_collisions_and_both_sides(
    registered_workspace, projects
):
    """The whole reason dry_run exists: a colliding source row is moved AND
    soft-deleted, so the target's version wins. Counts alone never showed
    that, which made the destructive case the invisible one."""
    source = projects("source")
    target = projects("target")
    key = f"topic/collide-{uuid.uuid4()}"

    repository.save_observation(
        target["id"], "Target version", "kept", "decision", topic_key=key
    )
    repository.save_observation(
        source["id"], "Source version", "hidden", "decision", topic_key=key
    )

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )

    assert result["dry_run"] is True
    assert result["topic_key_collisions"] == [key]

    hidden = result["would_hide"]
    assert len(hidden) == 1
    assert hidden[0]["topic_key"] == key
    assert hidden[0]["source_title"] == "Source version"
    assert hidden[0]["target_title"] == "Target version"
    # Source was written last, so the merge as proposed buries the fresher row.
    assert hidden[0]["hidden_side"] == "source (newer)"


def test_dry_run_flags_the_harmless_direction_differently(
    registered_workspace, projects
):
    source = projects("source")
    target = projects("target")
    key = f"topic/collide-{uuid.uuid4()}"

    repository.save_observation(
        source["id"], "Older source", "hidden", "decision", topic_key=key
    )
    repository.save_observation(
        target["id"], "Newer target", "kept", "decision", topic_key=key
    )

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )
    assert result["would_hide"][0]["hidden_side"] == "source (older)"


def test_dry_run_reports_no_collisions_when_topics_do_not_overlap(
    registered_workspace, projects
):
    source = projects("source")
    target = projects("target")

    repository.save_observation(
        source["id"], "A", "a", "decision", topic_key=f"topic/a-{uuid.uuid4()}"
    )
    repository.save_observation(
        target["id"], "B", "b", "decision", topic_key=f"topic/b-{uuid.uuid4()}"
    )

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )
    assert result["topic_key_collisions"] == []
    assert result["would_hide"] == []


def test_dry_run_prediction_matches_what_the_merge_actually_hides(
    registered_workspace, projects
):
    """A prediction nobody checks is a guess. The ids dry_run says would be
    hidden must be exactly the ids the real merge supersedes."""
    source = projects("source")
    target = projects("target")
    key = f"topic/collide-{uuid.uuid4()}"

    repository.save_observation(
        target["id"], "Target version", "kept", "decision", topic_key=key
    )
    source_obs = repository.save_observation(
        source["id"], "Source version", "hidden", "decision", topic_key=key
    )

    predicted = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )["would_hide"]

    executed = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
            dry_run=False,
        )
    )

    assert [h["source_id"] for h in predicted] == executed["superseded_observation_ids"]
    assert executed["superseded_observation_ids"] == [str(source_obs["id"])]


def test_dry_run_ignores_a_deleted_row_on_either_side(registered_workspace, projects):
    """A soft-deleted row cannot collide — it no longer surfaces, so counting
    it would scare the caller off a merge that is in fact clean."""
    source = projects("source")
    target = projects("target")
    key = f"topic/collide-{uuid.uuid4()}"

    stale = repository.save_observation(
        target["id"], "Deleted target version", "gone", "decision", topic_key=key
    )
    repository.delete_observation(
        stale["id"], registered_workspace["workspace"]["id"]
    )
    repository.save_observation(
        source["id"], "Live source version", "kept", "decision", topic_key=key
    )

    result = json.loads(
        merge_projects(
            source_project_id=source["id"],
            target_project_id=target["id"],
            user_id=registered_workspace["user_id"],
        )
    )
    assert result["topic_key_collisions"] == []
    assert result["would_hide"] == []
