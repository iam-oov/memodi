from __future__ import annotations

import os
import subprocess
import sys

GIT_TIMEOUT_SECONDS = 2


def _git(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _linked_worktree_main_repo(cwd: str) -> str | None:
    git_dir = _git(cwd, "--path-format=absolute", "--git-dir")
    common_dir = _git(cwd, "--path-format=absolute", "--git-common-dir")
    if not git_dir or not common_dir or git_dir == common_dir:
        return None
    if os.path.basename(common_dir) != ".git":
        return None
    return os.path.dirname(common_dir)


def _toplevel(cwd: str) -> str | None:
    cdup = _git(cwd, "--show-cdup")
    if cdup is None:
        return None
    return os.path.normpath(os.path.join(cwd, cdup))


def resolve(cwd: str, env: dict) -> str:
    override = (env.get("MEMODI_PATH") or "").strip()
    if override:
        return override.rstrip("/") or "/"
    if _git(cwd, "--is-inside-work-tree") != "true":
        return cwd
    return _linked_worktree_main_repo(cwd) or _toplevel(cwd) or cwd


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.getcwd()
    print(resolve(raw, os.environ))
