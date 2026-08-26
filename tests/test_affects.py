import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema, get_connection
from memodi.server import memodi_save
from memodi.tools.memory import save, search, search_hybrid, search_similar
from tests.conftest import _path
from tests.test_server_auth import FakeCtx

A_QUERY = "pdfqueue"
B_QUERY = "rediscache"
# plainto_tsquery ANDs its terms, so a term both features share is the only way
# to ask one question that can legitimately return both.
SHARED = "queuecontract"


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def repos():
    suffix = uuid.uuid4()
    return {
        "load_matching": f"load-matching-{suffix}",
        "celery_service": f"celery-service-{suffix}",
        "celery_scheduler": f"celery-scheduler-{suffix}",
    }


def _stored_metadata(observation_id: str) -> dict:
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    row = conn.execute(
        "SELECT metadata FROM observations WHERE id = %s", (observation_id,)
    ).fetchone()
    return row["metadata"] or {}


def _titles(payload: str) -> list[str]:
    return [r["title"] for r in json.loads(payload)]


def _save_two_features(ws: dict, repos: dict) -> None:
    """The scenario this feature exists for: two features saved from the
    workspace root, each touching a different pair of repos."""
    common = dict(path=ws["root"], user_id=ws["user_id"], machine=ws["machine"])
    save(
        **common,
        title="Feature A pdfqueue",
        content=f"Added a new {A_QUERY} to process pdf from s3, {SHARED}",
        type="decision",
        affects=[repos["load_matching"], repos["celery_service"]],
    )
    save(
        **common,
        title="Feature B rediscache",
        content=f"Dropped {B_QUERY}, data arrives straight from a {SHARED}",
        type="decision",
        affects=[repos["celery_scheduler"], repos["load_matching"]],
    )


# --- The visibility matrix this feature exists to produce ---


def test_search_from_a_repo_returns_only_features_that_affect_it(
    registered_workspace, repos
):
    """The core contract. celery-scheduler took part in feature B only, so
    searching from it must not surface feature A even though both live in the
    same workspace."""
    _save_two_features(registered_workspace, repos)
    common = dict(
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )

    scheduler = _titles(
        search(
            path=_path(registered_workspace, repos["celery_scheduler"]),
            query=SHARED,
            **common,
        )
    )
    assert scheduler == ["Feature B rediscache"]

    load_matching = _titles(
        search(
            path=_path(registered_workspace, repos["load_matching"]),
            query=SHARED,
            **common,
        )
    )
    assert sorted(load_matching) == ["Feature A pdfqueue", "Feature B rediscache"]

    service = _titles(
        search(
            path=_path(registered_workspace, repos["celery_service"]),
            query=SHARED,
            **common,
        )
    )
    assert service == ["Feature A pdfqueue"]


def test_search_from_the_workspace_root_sees_every_project(
    registered_workspace, repos
):
    """At the registered root there is no single repo in scope, so the project
    predicate is dropped entirely rather than narrowing to basename(root)."""
    _save_two_features(registered_workspace, repos)

    titles = _titles(
        search(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query=SHARED,
        )
    )
    assert sorted(titles) == ["Feature A pdfqueue", "Feature B rediscache"]


def test_search_hybrid_honors_affects_and_root(registered_workspace, repos):
    """search_hybrid filters project_id in three separate places (both CTEs and
    the outer join) — a partial fix returns wrong results rather than none."""
    _save_two_features(registered_workspace, repos)
    common = dict(
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )

    scheduler = _titles(
        search_hybrid(
            path=_path(registered_workspace, repos["celery_scheduler"]),
            query=B_QUERY,
            **common,
        )
    )
    assert "Feature B rediscache" in scheduler
    assert "Feature A pdfqueue" not in scheduler

    root = _titles(
        search_hybrid(path=registered_workspace["root"], query=A_QUERY, **common)
    )
    assert "Feature A pdfqueue" in root


def test_search_similar_honors_affects(registered_workspace, repos):
    _save_two_features(registered_workspace, repos)

    titles = _titles(
        search_similar(
            path=_path(registered_workspace, repos["celery_service"]),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="pdf processing queue",
        )
    )
    assert titles == ["Feature A pdfqueue"]


# --- Silent-loss traps ---


def test_topic_key_upsert_without_affects_preserves_the_stored_list(
    registered_workspace, repos
):
    """The upsert branch replaces metadata wholesale. Omitting affects on a
    later revision must not strip an observation's cross-repo visibility —
    an omitted optional field means 'leave it alone'."""
    common = dict(
        path=registered_workspace["root"],
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        topic_key="architecture/queue-contract",
    )
    first = json.loads(
        save(
            **common,
            title="Queue contract",
            content="First revision",
            type="architecture",
            affects=[repos["load_matching"], repos["celery_service"]],
        )
    )
    second = json.loads(
        save(
            **common,
            title="Queue contract",
            content="Second revision, no affects passed",
            type="architecture",
        )
    )

    assert second["id"] == first["id"]
    assert _stored_metadata(second["id"])["affects"] == [
        repos["load_matching"],
        repos["celery_service"],
    ]


def test_topic_key_upsert_with_empty_affects_clears_the_stored_list(
    registered_workspace, repos
):
    """affects=[] is the explicit way to clear, as opposed to omitting it."""
    common = dict(
        path=registered_workspace["root"],
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        topic_key="architecture/queue-contract-cleared",
    )
    save(
        **common,
        title="Queue contract",
        content="First revision",
        type="architecture",
        affects=[repos["load_matching"]],
    )
    second = json.loads(
        save(
            **common,
            title="Queue contract",
            content="Second revision clears it",
            type="architecture",
            affects=[],
        )
    )

    assert "affects" not in _stored_metadata(second["id"])


def test_dedup_hit_unions_the_incoming_affects(registered_workspace, repos):
    """content_hash covers title+content only, so an identical re-save inside
    the 15-minute window is absorbed as a duplicate. Its affects must extend
    the stored set instead of being dropped on the floor."""
    common = dict(
        path=registered_workspace["root"],
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Same title",
        content="Same content",
        type="discovery",
    )
    first = json.loads(save(**common, affects=[repos["load_matching"]]))
    second = json.loads(save(**common, affects=[repos["celery_service"]]))

    assert second["id"] == first["id"]
    assert sorted(_stored_metadata(second["id"])["affects"]) == sorted(
        [repos["load_matching"], repos["celery_service"]]
    )


# --- Must-not-widen guards ---


def test_affects_does_not_let_a_save_upsert_another_projects_topic(
    registered_workspace, repos
):
    """Upsert is defined as 'same topic_key within the same primary project'.
    Listing a project in affects must not make its rows upsertable from
    elsewhere, or one repo silently overwrites another's memory."""
    common = dict(
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        topic_key="architecture/shared-key",
        type="architecture",
    )
    owned = json.loads(
        save(
            path=_path(registered_workspace, repos["load_matching"]),
            title="Owned by load-matching",
            content="Original content",
            **common,
        )
    )
    intruder = json.loads(
        save(
            path=_path(registered_workspace, repos["celery_service"]),
            title="Saved from celery-service",
            content="Different content",
            affects=[repos["load_matching"]],
            **common,
        )
    )

    assert intruder["id"] != owned["id"]
    conn = get_connection()
    if conn.info.transaction_status != 0:
        conn.rollback()
    row = conn.execute(
        "SELECT title, revision_count FROM observations WHERE id = %s", (owned["id"],)
    ).fetchone()
    assert row["title"] == "Owned by load-matching"
    assert row["revision_count"] == 1


def test_affects_does_not_absorb_a_save_into_another_projects_dedup_window(
    registered_workspace, repos
):
    """Same danger in miniature: identical content in a different primary
    project is genuinely new content, not a duplicate."""
    common = dict(
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Identical title",
        content="Identical content",
        type="discovery",
    )
    owned = json.loads(
        save(path=_path(registered_workspace, repos["load_matching"]), **common)
    )
    other = json.loads(
        save(
            path=_path(registered_workspace, repos["celery_service"]),
            affects=[repos["load_matching"]],
            **common,
        )
    )

    assert other["id"] != owned["id"]


# --- Parameter validation, mirroring the supersedes precedent ---


def test_affects_rejects_a_non_string_element_before_saving(registered_workspace):
    ack = json.loads(
        save(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Bad affects",
            content="Should not save",
            type="decision",
            affects=["fine", 7],
        )
    )
    assert ack.get("type") == "validation"
    assert "id" not in ack


def test_affects_over_the_cap_still_saves_and_reports_the_reason(
    registered_workspace,
):
    """Mirrors supersedes: the observation always persists, the rejected list
    is reported with a discriminated reason instead of failing the call."""
    ack = json.loads(
        save(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Too many affects",
            content="Saves anyway",
            type="decision",
            affects=[f"repo-{i}" for i in range(21)],
        )
    )
    assert ack["affects_reason"] == "too_many"
    assert "affects" not in _stored_metadata(ack["id"])


def test_affects_strips_whitespace_and_folds_case(registered_workspace):
    """Project names are case-folded on the way in, so Repo-One and repo-one
    are the SAME project — the affects list collapses to one entry instead of
    naming a project that will never be created."""
    ack = json.loads(
        save(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="Messy affects",
            content="Normalized on the way in",
            type="decision",
            affects=["  Repo-One  ", "repo-one", "Repo-One", "   "],
        )
    )
    assert _stored_metadata(ack["id"])["affects"] == ["repo-one"]


def test_affects_reports_only_the_projects_it_created(registered_workspace, repos):
    """Auto-creation is fine, silent auto-creation is not — a typo has to be
    visible in the ack instead of quietly swallowing the memory."""
    common = dict(
        path=registered_workspace["root"],
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        type="decision",
    )
    save(
        **common,
        title="Establishes the project",
        content="First",
        affects=[repos["load_matching"]],
    )
    ack = json.loads(
        save(
            **common,
            title="Adds one new name",
            content="Second",
            affects=[repos["load_matching"], repos["celery_service"]],
        )
    )

    assert ack["projects_created"] == [repos["celery_service"]]


def test_save_without_affects_reports_neither_new_field(registered_workspace):
    ack = json.loads(
        save(
            path=registered_workspace["root"],
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            title="No affects at all",
            content="Plain save",
            type="decision",
        )
    )
    assert "projects_created" not in ack
    assert "affects_reason" not in ack


# --- Root detection ---


def test_memodi_save_forwards_affects_through_the_mcp_boundary(registered_workspace):
    """server.memodi_save delegates with a hand-written argument list and no
    other test enters through it, so a parameter added to the tool signature but
    dropped in the delegating call would ship silently."""
    ctx = FakeCtx(
        {
            "X-Memodi-Api-Key": registered_workspace["api_key"],
            "X-Memodi-Machine": registered_workspace["machine"],
        }
    )

    ack = json.loads(
        memodi_save(
            ctx,
            path=registered_workspace["root"],
            title="Saved through the tool",
            content="Reached the repository with its affects intact",
            type="decision",
            affects=["repo-through-mcp"],
        )
    )

    assert _stored_metadata(ack["id"])["affects"] == ["repo-through-mcp"]
    assert ack["projects_created"] == ["repo-through-mcp"]


def test_resolve_workspace_reports_the_path_it_matched(registered_workspace):
    """Root widening must be a fact, not a guess: the resolver reports which
    registered path the cwd matched so callers can compare."""
    root = registered_workspace["root"]
    at_root = repository.resolve_workspace(
        registered_workspace["user_id"], registered_workspace["machine"], root
    )
    nested = repository.resolve_workspace(
        registered_workspace["user_id"],
        registered_workspace["machine"],
        f"{root}/some-repo/app",
    )

    assert at_root["matched_path"] == root
    assert nested["matched_path"] == root
