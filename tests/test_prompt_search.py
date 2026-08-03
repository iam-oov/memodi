import json
import uuid

import pytest

from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.tools.memory import delete, save, search_for_prompt
from tests.conftest import _path, cleanup_rows


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


@pytest.fixture
def project_name():
    return f"test-prompt-search-{uuid.uuid4()}"


def _extra_workspace(user_id: str) -> dict:
    machine = f"test-machine-{uuid.uuid4()}"
    root = f"/tmp/test-extra-{uuid.uuid4()}"
    name = f"test-extra-ws-{uuid.uuid4()}"
    workspace = repository.workspace_start(user_id, machine, root, name)
    return {
        "user_id": user_id,
        "machine": machine,
        "root": root,
        "workspace": workspace,
    }


def _cleanup_workspace(workspace: dict) -> None:
    ws_id = workspace["id"]
    cleanup_rows(
        """
        DELETE FROM observations
        WHERE project_id IN (SELECT id FROM projects WHERE workspace_id = %s)
        """,
        (ws_id,),
    )
    cleanup_rows("DELETE FROM projects WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspace_paths WHERE workspace_id = %s", (ws_id,))
    cleanup_rows("DELETE FROM workspaces WHERE id = %s", (ws_id,))


def test_search_observations_by_workspace_crosses_projects_in_same_workspace(
    registered_workspace,
):
    proj_a = f"test-proj-a-{uuid.uuid4()}"
    proj_b = f"test-proj-b-{uuid.uuid4()}"

    save(
        path=f"{registered_workspace['root']}/{proj_a}",
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Hexagonal architecture note",
        content="Adopted hexagonal architecture for project A",
        type="architecture",
    )
    save(
        path=f"{registered_workspace['root']}/{proj_b}",
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Hexagonal architecture in B",
        content="Project B also follows hexagonal architecture",
        type="architecture",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "hexagonal architecture"
    )
    titles = [r["title"] for r in rows]
    assert "Hexagonal architecture note" in titles
    assert "Hexagonal architecture in B" in titles


def test_search_observations_by_workspace_excludes_deleted(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    obs_id = json.loads(
        save(
            **common,
            title="Zebra note",
            content="Zebra migration runs on Sunday",
            type="discovery",
        )
    )["id"]
    delete(**common, observation_id=obs_id)

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "zebra migration"
    )
    assert rows == []


def test_search_observations_by_workspace_excludes_superseded(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    old_id = json.loads(
        save(
            **common,
            title="Old flamingo note",
            content="Flamingo migration runs on Sunday",
            type="config",
        )
    )["id"]
    save(
        **common,
        title="New flamingo note",
        content="Flamingo migration runs on Monday",
        type="config",
        supersedes=old_id,
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "flamingo migration"
    )
    titles = [r["title"] for r in rows]
    assert "New flamingo note" in titles
    assert "Old flamingo note" not in titles


def test_search_observations_by_workspace_respects_limit(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    for i in range(5):
        save(
            **common,
            title=f"Giraffe note {i}",
            content="Giraffe migration notes",
            type="discovery",
        )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "giraffe migration", limit=2
    )
    assert len(rows) == 2


def test_search_observations_by_workspace_ranks_by_relevance(
    registered_workspace, project_name
):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Mentions penguin once",
        content="A single mention of penguin here",
        type="discovery",
    )
    save(
        **common,
        title="Penguin penguin penguin",
        content="Penguin penguin penguin penguin migration",
        type="discovery",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "penguin"
    )
    titles = [r["title"] for r in rows]
    assert titles.index("Penguin penguin penguin") < titles.index(
        "Mentions penguin once"
    )


def test_search_observations_by_workspace_no_cross_workspace_leak(
    registered_workspace, project_name
):
    other = _extra_workspace(registered_workspace["user_id"])
    try:
        save(
            path=f"{other['root']}/{project_name}",
            user_id=other["user_id"],
            machine=other["machine"],
            title="Toucan note in other workspace",
            content="Toucan migration must not leak across workspaces",
            type="discovery",
        )

        rows = repository.search_observations_by_workspace(
            registered_workspace["workspace"]["id"], "toucan migration"
        )
        assert rows == []
    finally:
        _cleanup_workspace(other["workspace"])


def test_search_observations_by_workspace_no_match_returns_empty(
    registered_workspace,
):
    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "no-such-lexeme-anywhere"
    )
    assert rows == []


def test_search_observations_by_workspace_is_keyword_not_semantic(
    registered_workspace, project_name
):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Penguin note",
        content="Penguin migration runs every winter",
        type="discovery",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "database indexing strategy"
    )
    assert rows == []


def test_natural_language_prompt_matches_by_significant_terms(
    registered_workspace, project_name
):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Ports and adapters note",
        content="hexagonal architecture ports adapters",
        type="architecture",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"],
        "contame cómo quedó lo de la hexagonal architecture",
    )
    titles = [r["title"] for r in rows]
    assert "Ports and adapters note" in titles


def test_or_semantics_matches_any_significant_term(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Penguin colony note",
        content="penguin colony",
        type="discovery",
    )
    save(
        **common,
        title="Hexagonal architecture note",
        content="hexagonal architecture",
        type="architecture",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "penguin architecture"
    )
    titles = [r["title"] for r in rows]
    assert "Penguin colony note" in titles
    assert "Hexagonal architecture note" in titles


def test_stopword_or_short_only_query_returns_empty(registered_workspace, project_name):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Unrelated note",
        content="qué es esto y lo otro",
        type="discovery",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "qué es esto y lo otro"
    )
    assert rows == []


def test_ranks_more_matching_terms_higher(registered_workspace, project_name):
    common = dict(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
    )
    save(
        **common,
        title="Both terms note",
        content="hexagonal architecture with ports and adapters",
        type="architecture",
    )
    save(
        **common,
        title="One term note",
        content="hexagonal design without the other word",
        type="architecture",
    )

    rows = repository.search_observations_by_workspace(
        registered_workspace["workspace"]["id"], "hexagonal adapters"
    )
    titles = [r["title"] for r in rows]
    assert titles.index("Both terms note") < titles.index("One term note")


def test_search_for_prompt_returns_serialized_list(registered_workspace, project_name):
    save(
        path=_path(registered_workspace, project_name),
        user_id=registered_workspace["user_id"],
        machine=registered_workspace["machine"],
        title="Owl architecture decision",
        content="Owl service uses hexagonal architecture",
        type="architecture",
    )

    rows = json.loads(
        search_for_prompt(
            path=_path(registered_workspace, project_name),
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="owl hexagonal architecture",
        )
    )
    assert any(r["title"] == "Owl architecture decision" for r in rows)
    assert all("content" not in r for r in rows)


def test_search_for_prompt_unregistered_path_is_not_started_and_creates_no_project(
    registered_workspace,
):
    unregistered_path = f"/never/registered/{uuid.uuid4()}"
    before = repository.list_projects(owner_user_id=registered_workspace["user_id"])

    result = json.loads(
        search_for_prompt(
            path=unregistered_path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="some keywords",
        )
    )

    assert result["type"] == "not_started"
    after = repository.list_projects(owner_user_id=registered_workspace["user_id"])
    assert len(after) == len(before)


def test_search_for_prompt_registered_subpath_creates_no_project(
    registered_workspace,
):
    subpath = f"{registered_workspace['root']}/fresh-subdir-{uuid.uuid4()}"
    before = repository.list_projects(owner_user_id=registered_workspace["user_id"])

    result = json.loads(
        search_for_prompt(
            path=subpath,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query="some keywords",
        )
    )

    assert result == []
    after = repository.list_projects(owner_user_id=registered_workspace["user_id"])
    assert len(after) == len(before)


@pytest.mark.parametrize("blank_query", ["", "   "])
def test_search_for_prompt_empty_query_returns_empty_list_without_db(
    registered_workspace, blank_query
):
    unregistered_path = f"/never/registered/{uuid.uuid4()}"

    result = json.loads(
        search_for_prompt(
            path=unregistered_path,
            user_id=registered_workspace["user_id"],
            machine=registered_workspace["machine"],
            query=blank_query,
        )
    )

    assert result == []
