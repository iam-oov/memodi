import http.server
import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_plugin_hooks_contract import (
    PLUGIN,
    PROMPT_SEARCH_HOOK,
    SESSION_DIGEST_HOOK,
    SESSION_END_HOOK,
    SESSION_START_HOOK,
    _run_hook,
    _StubHandler,
)

SCRIPTS = PLUGIN / "scripts"
RESOLVER = SCRIPTS / "resolve_path.py"
HOOKS_USING_CWD = [
    "session-start.sh",
    "post-compaction.sh",
    "prompt-search.sh",
    "session-digest.sh",
    "session-end.sh",
    "subagent-stop.sh",
]

_spec = importlib.util.spec_from_file_location("resolve_path", RESOLVER)
resolve_path = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve_path)


@pytest.fixture
def hook_server():
    posts: list[dict] = []
    handler = type("_Handler", (_StubHandler,), {"posts": posts, "gets": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", posts=posts)
    server.shutdown()
    server.server_close()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    # main/ is a normal repo with a subfolder; orca/diag is a linked worktree
    # of it living elsewhere, the way Orca lays out its workers.
    main = tmp_path / "main"
    (main / "src" / "billing").mkdir(parents=True)
    _git("init", "-q", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=main)
    worktree = tmp_path / "orca" / "diag"
    worktree.parent.mkdir()
    _git("worktree", "add", "-q", str(worktree), cwd=main)
    (worktree / "deep").mkdir()
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    return {"main": main, "worktree": worktree, "plain": plain, "tmp": tmp_path}


def _resolve(cwd: Path, env: dict | None = None) -> str:
    return resolve_path.resolve(str(cwd), env or {})


def test_repo_root_resolves_to_itself(repo):
    assert _resolve(repo["main"]) == str(repo["main"])


def test_subfolder_resolves_to_the_repo_root(repo):
    assert _resolve(repo["main"] / "src" / "billing") == str(repo["main"])


def test_linked_worktree_resolves_to_the_main_repo(repo):
    assert _resolve(repo["worktree"]) == str(repo["main"])
    assert _resolve(repo["worktree"] / "deep") == str(repo["main"])


def test_folder_outside_git_is_unchanged(repo):
    assert _resolve(repo["plain"]) == str(repo["plain"])


def test_missing_folder_is_unchanged(repo):
    gone = repo["tmp"] / "deleted-worktree"

    assert _resolve(gone) == str(gone)


def test_memodi_path_overrides_everything(repo):
    pinned = "/home/someone/System/"

    assert _resolve(repo["worktree"], {"MEMODI_PATH": pinned}) == "/home/someone/System"


def test_blank_memodi_path_is_ignored(repo):
    assert _resolve(repo["main"] / "src", {"MEMODI_PATH": "  "}) == str(repo["main"])


def test_symlinked_prefix_is_kept_for_a_subfolder(repo):
    # Registered paths are matched as text, so resolving the symlink away
    # would turn a registered folder into not_started.
    link = repo["tmp"] / "link"
    link.symlink_to(repo["main"])

    assert _resolve(link / "src" / "billing") == str(link)


def test_falls_back_to_cwd_when_git_is_missing(repo):
    result = subprocess.run(
        [sys.executable, "-I", str(RESOLVER), str(repo["main"] / "src")],
        capture_output=True,
        text=True,
        env={"PATH": str(repo["plain"])},
        check=False,
    )

    assert result.stdout.strip() == str(repo["main"] / "src")


@pytest.mark.parametrize("hook", HOOKS_USING_CWD)
def test_every_cwd_hook_routes_through_the_resolver(hook):
    assert 'resolve_path.py" "$RAW_CWD"' in (SCRIPTS / hook).read_text()


def test_session_start_uses_the_main_repo_for_a_worktree(repo, hook_server):
    out = _run_hook(
        SESSION_START_HOOK,
        {"cwd": str(repo["worktree"]), "session_id": "sid-abc"},
        hook_server.url,
    )

    assert f'path: "{repo["main"]}"' in out
    assert f"belongs to the repo at {repo['main']}" in out
    assert hook_server.posts == [
        {"path": str(repo["main"]), "client_session_id": "sid-abc"}
    ]


def test_session_start_has_no_repo_note_when_cwd_is_the_repo(repo, hook_server):
    out = _run_hook(SESSION_START_HOOK, {"cwd": str(repo["main"])}, hook_server.url)

    assert "belongs to the repo" not in out


def test_memodi_path_pins_a_non_git_agent(repo, hook_server):
    out = _run_hook(
        SESSION_START_HOOK,
        {"cwd": str(repo["plain"])},
        hook_server.url,
        extra_env={"MEMODI_PATH": "/srv/System"},
    )

    assert 'path: "/srv/System"' in out
    assert hook_server.posts == [{"path": "/srv/System"}]


@pytest.mark.parametrize(
    ("hook", "payload"),
    [
        (PROMPT_SEARCH_HOOK, {"prompt": "billing renewal invoice"}),
        (SESSION_DIGEST_HOOK, {}),
        (SESSION_END_HOOK, {"session_id": "sid-abc", "reason": "other"}),
    ],
)
def test_other_hooks_post_the_main_repo_path(repo, hook_server, hook, payload):
    _run_hook(hook, {"cwd": str(repo["worktree"] / "deep"), **payload}, hook_server.url)

    assert hook_server.posts, f"{hook.name} posted nothing"
    assert {post["path"] for post in hook_server.posts} == {str(repo["main"])}


def test_resolver_runs_without_a_project_venv():
    assert os.access(RESOLVER, os.R_OK)
    assert "import memodi" not in RESOLVER.read_text()
