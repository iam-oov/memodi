"""Contract tests over the SHIPPED plugin files.

The plugin is markdown and shell, so the only way to pin its contract is to
read what actually ships. These guard the split the hooks depend on: the
SessionEnd/SessionStart hooks own the session lifecycle over plain HTTP, and
the model owns only the summary. A file telling the model to call
memodi_session_start re-opens an untagged session and the hook's close can
never match it again — the whole feature goes inert.
"""

import http.server
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "claude-code"
SKILL = PLUGIN / "skills" / "memory" / "SKILL.md"
START_COMMAND = PLUGIN / "commands" / "start.md"
END_COMMAND = PLUGIN / "commands" / "end.md"
LOGOUT_COMMAND = PLUGIN / "commands" / "logout.md"
FORGET_COMMAND = PLUGIN / "commands" / "forget.md"
SESSION_START_HOOK = PLUGIN / "scripts" / "session-start.sh"
SESSION_DIGEST_HOOK = PLUGIN / "scripts" / "session-digest.sh"
SESSION_END_HOOK = PLUGIN / "scripts" / "session-end.sh"
POST_COMPACTION_HOOK = PLUGIN / "scripts" / "post-compaction.sh"
SUBAGENT_STOP_HOOK = PLUGIN / "scripts" / "subagent-stop.sh"
PROMPT_SEARCH_HOOK = PLUGIN / "scripts" / "prompt-search.sh"
LOGIN_LISTENER = PLUGIN / "scripts" / "login_listener.py"
LOGIN_SCRIPT = PLUGIN / "scripts" / "login.sh"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"

_PROHIBITION_MARKERS = ("do not", "never", "not call", "no llames")


def _lines_instructing(text: str, what: str) -> list[str]:
    """Lines naming something without forbidding it."""
    return [
        line
        for line in text.splitlines()
        if what in line
        and not any(marker in line.lower() for marker in _PROHIBITION_MARKERS)
    ]


def _lines_forbidding(text: str, what: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if what in line
        and any(marker in line.lower() for marker in _PROHIBITION_MARKERS)
    ]


def _lines_instructing_session_start(text: str) -> list[str]:
    return _lines_instructing(text, "memodi_session_start")


def test_skill_does_not_instruct_calling_session_start():
    assert _lines_instructing_session_start(SKILL.read_text()) == []


def test_start_command_does_not_instruct_calling_session_start():
    assert _lines_instructing_session_start(START_COMMAND.read_text()) == []


def test_subagent_stop_hook_sends_no_topic_key():
    """A topic_key upserts, so every capture would overwrite the same row."""
    assert "'topic_key'" not in SUBAGENT_STOP_HOOK.read_text()


def test_subagent_stop_hook_truncates_the_extracted_content():
    text = SUBAGENT_STOP_HOOK.read_text()
    assert "MAX_CONTENT" in text


def _post_curl(text: str) -> str:
    """The curl invocation that POSTs to /hooks/session-start."""
    url_at = text.rindex('"${MEMODI_URL}/hooks/session-start"')
    return text[text.rindex("curl", 0, url_at) : url_at]


def test_session_start_hook_emits_the_protocol_before_the_post():
    text = SESSION_START_HOOK.read_text()
    protocol_at = text.index("## Memodi Memory — Session Start")
    post_at = text.rindex('"${MEMODI_URL}/hooks/session-start"')
    assert protocol_at < post_at


def test_session_start_hook_post_budget_is_five_seconds():
    assert "--max-time 5" in _post_curl(SESSION_START_HOOK.read_text())


def test_session_start_hook_timeout_covers_probe_plus_post():
    import json

    hooks = json.loads(HOOKS_JSON.read_text())
    startup = next(
        entry
        for entry in hooks["hooks"]["SessionStart"]
        if entry["matcher"] == "startup|clear"
    )
    assert startup["hooks"][0]["timeout"] >= 8


def test_session_start_hook_does_not_assert_the_session_is_open():
    """The POST can fail; the injected protocol must not claim it succeeded."""
    text = SESSION_START_HOOK.read_text()
    assert "already opened" not in text


def test_end_command_instructs_passing_client_session_id_when_provided():
    """/memodi:end step 3 must pass client_session_id when the SessionStart
    protocol supplied one, so the close targets this window's own session
    instead of whichever session is newest. Asserts intent, not the mere
    presence of the word: a line telling the model NEVER to pass it also
    contains the string."""
    text = END_COMMAND.read_text()
    assert _lines_instructing(text, "client_session_id") != []
    assert _lines_forbidding(text, "client_session_id") == []


def test_skill_instructs_passing_client_session_id_on_close():
    """SKILL.md is what SURVIVES compaction — injected protocol text does
    not. If the id is only taught in the injected protocol, every compacted
    session silently falls back to closing whichever session is newest."""
    text = SKILL.read_text()
    assert _lines_instructing(text, "client_session_id") != []
    assert _lines_forbidding(text, "client_session_id") == []


def test_skill_does_not_claim_session_start_leaks_a_session_forever():
    """A stale claim the server contradicts: an extra untagged session is a
    harmless orphan (summary IS NULL makes it invisible to
    get_latest_session_summary), not a leak. Documentation that lies about
    the server teaches the model the wrong model of the system."""
    assert "leaking the session open forever" not in SKILL.read_text()


# --- Rendered hook output (the judges ran the scripts; so do these) ---


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Answers the hooks' connectivity probe and records what they POST."""

    posts: ClassVar[list[dict]] = []
    gets: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.gets.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            self.posts.append(json.loads(raw))
        except ValueError:
            self.posts.append({"unparseable": raw.decode(errors="replace")})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def hook_server():
    posts: list[dict] = []
    gets: list[str] = []
    handler = type("_Handler", (_StubHandler,), {"posts": posts, "gets": gets})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{server.server_port}", posts=posts, gets=gets
    )
    server.shutdown()
    server.server_close()


def _run_hook(script: Path, payload: dict, url: str) -> str:
    """Run a shipped hook script for real and return what it injects.

    Rendered output is the only honest contract for these files: reading the
    source cannot tell a quoted heredoc from an unquoted one, and with
    <<'EOF' the model would receive the literal ${SESSION_ID} instead of an
    id.
    """
    result = subprocess.run(
        ["sh", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "MEMODI_URL": url,
            "MEMODI_API_KEY": "test-key",
            "MEMODI_MACHINE": "test-machine",
        },
    )
    assert result.returncode == 0
    assert "not reachable" not in result.stdout.lower()
    return result.stdout


def test_session_start_hook_injects_the_real_session_id(hook_server):
    out = _run_hook(
        SESSION_START_HOOK,
        {"cwd": "/tmp/hook-cwd", "session_id": "sid-abc"},
        hook_server.url,
    )

    assert "${SESSION_ID}" not in out
    assert "${CWD}" not in out
    assert 'client_session_id: "sid-abc"' in out
    assert '"/tmp/hook-cwd"' in out


def test_session_start_hook_posts_the_session_id(hook_server):
    _run_hook(
        SESSION_START_HOOK,
        {"cwd": "/tmp/hook-cwd", "session_id": "sid-abc"},
        hook_server.url,
    )

    assert hook_server.posts == [
        {"path": "/tmp/hook-cwd", "client_session_id": "sid-abc"}
    ]


def test_session_start_hook_omits_client_session_id_when_there_is_none(hook_server):
    """No session_id on stdin: the protocol must not name client_session_id
    at all. Instructing the model to pass "" is instructing it into the bug
    — an empty id is the untagged identity, not this window's session."""
    out = _run_hook(SESSION_START_HOOK, {"cwd": "/tmp/hook-cwd"}, hook_server.url)

    assert "client_session_id" not in out
    assert "memodi_session_end" in out
    assert hook_server.posts == [{"path": "/tmp/hook-cwd"}]


def test_post_compaction_hook_reinjects_the_session_close_with_the_id(hook_server):
    """Compaction is exactly the event that drops the injected protocol, so
    this hook must restore the session-close instruction WITH the id — or
    the compacted window closes whichever session is newest, which is
    another window's still-open row."""
    out = _run_hook(
        POST_COMPACTION_HOOK,
        {"cwd": "/tmp/hook-cwd", "session_id": "sid-abc"},
        hook_server.url,
    )

    assert "memodi_session_end" in out
    assert 'client_session_id: "sid-abc"' in out
    assert "${SESSION_ID}" not in out


def test_post_compaction_hook_omits_client_session_id_when_there_is_none(hook_server):
    out = _run_hook(POST_COMPACTION_HOOK, {"cwd": "/tmp/hook-cwd"}, hook_server.url)

    assert "memodi_session_end" in out
    assert "client_session_id" not in out


# --- session-digest.sh (SessionStart, user-visible digest) ---


class _DigestStubHandler(_StubHandler):
    """A stub that answers /hooks/digest with a configurable JSON body."""

    body: ClassVar[bytes] = b""

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            self.posts.append(json.loads(raw))
        except ValueError:
            self.posts.append({"unparseable": raw.decode(errors="replace")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


@pytest.fixture
def digest_server():
    servers = []

    def factory(body: dict) -> SimpleNamespace:
        posts: list[dict] = []
        gets: list[str] = []
        handler = type(
            "_Handler",
            (_DigestStubHandler,),
            {"posts": posts, "gets": gets, "body": json.dumps(body).encode()},
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return SimpleNamespace(
            url=f"http://127.0.0.1:{server.server_port}", posts=posts
        )

    yield factory
    for server in servers:
        server.shutdown()
        server.server_close()


def test_session_digest_hook_shows_the_digest_as_system_message(digest_server):
    digest = "memodi — ws · last 5 days\n\nRecent:\n  [decision] X (2h ago)"
    stub = digest_server({"digest": digest})

    out = _run_hook(SESSION_DIGEST_HOOK, {"cwd": "/tmp/hook-cwd"}, stub.url)

    assert json.loads(out) == {"systemMessage": digest}
    assert stub.posts == [{"path": "/tmp/hook-cwd"}]


def test_session_digest_hook_is_silent_when_digest_is_empty(digest_server):
    stub = digest_server({"digest": ""})

    out = _run_hook(SESSION_DIGEST_HOOK, {"cwd": "/tmp/hook-cwd"}, stub.url)

    assert out.strip() == ""


def test_session_digest_hook_is_silent_on_a_server_error_body(digest_server):
    """not_started / not_authenticated bodies must never leak to the user —
    session-start.sh owns messaging for those states."""
    stub = digest_server({"error": "memodi is not started", "type": "not_started"})

    out = _run_hook(SESSION_DIGEST_HOOK, {"cwd": "/tmp/hook-cwd"}, stub.url)

    assert out.strip() == ""


def test_session_digest_hook_is_silent_without_api_key(digest_server):
    stub = digest_server({"digest": "should never be fetched"})

    result = subprocess.run(
        ["sh", str(SESSION_DIGEST_HOOK)],
        input=json.dumps({"cwd": "/tmp/hook-cwd"}),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "MEMODI_URL": stub.url, "MEMODI_API_KEY": ""},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert stub.posts == []


def test_hooks_json_registers_the_digest_hook_after_session_start():
    hooks = json.loads(HOOKS_JSON.read_text())
    startup = next(
        entry
        for entry in hooks["hooks"]["SessionStart"]
        if entry["matcher"] == "startup|clear"
    )
    commands = [hook["command"] for hook in startup["hooks"]]
    assert commands[0].endswith("session-start.sh")
    assert commands[1].endswith("session-digest.sh")
    digest = startup["hooks"][1]
    assert digest["timeout"] >= 6


# --- prompt-search.sh (UserPromptSubmit) ---


def _run_hook_with_response(script: Path, payload: dict, response_body: bytes) -> str:
    """Run a shipped hook script against a stub server that answers every
    POST with response_body — the counterpart to hook_server for hooks that
    read the server's response instead of only sending a fire-and-forget
    POST."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(200)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return _run_hook(script, payload, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()


def test_prompt_search_hook_prints_markdown_block_with_topic_key():
    body = json.dumps(
        [
            {
                "id": "abc-123",
                "type": "decision",
                "title": "Use JWT",
                "topic_key": "auth/model",
                "project": "memodi",
                "rank": 0.5,
            }
        ]
    ).encode()

    out = _run_hook_with_response(
        PROMPT_SEARCH_HOOK, {"cwd": "/tmp/x", "prompt": "auth jwt"}, body
    )

    assert "## Related memory (memodi — keyword match)" in out
    assert "memodi_get_observation" in out
    assert "[decision] Use JWT" in out
    assert "topic: auth/model" in out
    assert "project: memodi" in out
    assert "(id: abc-123)" in out


def test_prompt_search_hook_drops_topic_segment_when_absent():
    body = json.dumps(
        [{"id": "x", "type": "discovery", "title": "T", "project": "p", "rank": 0.1}]
    ).encode()

    out = _run_hook_with_response(
        PROMPT_SEARCH_HOOK, {"cwd": "/tmp/x", "prompt": "q"}, body
    )

    assert "topic:" not in out
    assert "project: p (id: x)" in out


def test_prompt_search_hook_prints_nothing_for_empty_list_response():
    out = _run_hook_with_response(
        PROMPT_SEARCH_HOOK, {"cwd": "/tmp/x", "prompt": "q"}, b"[]"
    )

    assert out == ""


def test_prompt_search_hook_prints_nothing_for_error_object_response():
    body = json.dumps({"error": "not started", "type": "not_started"}).encode()

    out = _run_hook_with_response(
        PROMPT_SEARCH_HOOK, {"cwd": "/tmp/x", "prompt": "q"}, body
    )

    assert out == ""


def test_prompt_search_hook_exits_0_when_server_unreachable():
    result = subprocess.run(
        ["sh", str(PROMPT_SEARCH_HOOK)],
        input=json.dumps({"cwd": "/tmp/x", "prompt": "q"}),
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "MEMODI_URL": "http://127.0.0.1:1",
            "MEMODI_API_KEY": "test-key",
            "MEMODI_MACHINE": "test-machine",
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_prompt_search_hook_posts_path_and_truncated_prompt(hook_server):
    long_prompt = "x" * 3000

    payload = {"cwd": "/tmp/hook-cwd", "prompt": long_prompt}
    _run_hook(PROMPT_SEARCH_HOOK, payload, hook_server.url)

    assert hook_server.posts[0]["path"] == "/tmp/hook-cwd"
    assert len(hook_server.posts[0]["query"]) <= 2000


def test_prompt_search_hook_exits_0_silently_when_prompt_is_absent(hook_server):
    out = _run_hook(PROMPT_SEARCH_HOOK, {"cwd": "/tmp/hook-cwd"}, hook_server.url)

    assert out == ""
    assert hook_server.posts == []


# --- Loopback login hand-off ---


def _run_hook_with_env(
    script: Path, payload: dict, url: str, api_key: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "MEMODI_URL": url,
            "MEMODI_API_KEY": api_key,
            "MEMODI_MACHINE": "test-machine",
        },
    )


def test_session_start_hook_without_api_key_tells_model_to_login(hook_server):
    result = _run_hook_with_env(
        SESSION_START_HOOK,
        {"cwd": "/tmp/hook-cwd", "session_id": "sid-abc"},
        hook_server.url,
        api_key="",
    )

    assert result.returncode == 0
    assert "/memodi:login" in result.stdout
    assert "## Memodi Memory — Session Start" not in result.stdout
    assert hook_server.posts == []


@pytest.mark.parametrize(
    "script",
    [POST_COMPACTION_HOOK, PROMPT_SEARCH_HOOK, SESSION_END_HOOK, SUBAGENT_STOP_HOOK],
)
def test_hooks_without_api_key_stay_silent_and_send_nothing(hook_server, script):
    """Every hook must be inert until the user logs in — an unauthenticated
    POST would only be rejected server-side, and any stdout would nag the
    model on a machine that deliberately has no key."""
    result = _run_hook_with_env(
        script,
        {
            "cwd": "/tmp/hook-cwd",
            "session_id": "sid-abc",
            "prompt": "q",
            "last_assistant_message": (
                "## Learnings\nA hook with no api key must send nothing at all.\n"
            ),
        },
        hook_server.url,
        api_key="",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert hook_server.posts == []
    assert hook_server.gets == []


def test_install_sh_embeds_the_login_listener_verbatim():
    assert LOGIN_LISTENER.read_text() in INSTALL_SH.read_text()


def test_env_markers_are_identical_across_install_login_and_logout():
    marker_start = "# >>> memodi env >>>"
    marker_end = "# <<< memodi env <<<"
    for path in (INSTALL_SH, LOGIN_SCRIPT, LOGOUT_COMMAND):
        text = path.read_text()
        assert marker_start in text
        assert marker_end in text


def test_login_script_never_prints_the_key_outside_persist_env():
    text = LOGIN_SCRIPT.read_text()
    func_start = text.index("persist_env() {")
    func_end = text.index("\n}\n", func_start)
    outside = text[:func_start] + text[func_end:]
    for line in outside.splitlines():
        if "MEMODI_API_KEY" in line:
            assert "echo" not in line
            assert "printf" not in line


# --- /memodi:forget ---


def test_forget_command_exists_and_is_discoverable():
    """Commands are auto-discovered from the directory, so the file name IS
    the registration: commands/forget.md -> /memodi:forget."""
    assert FORGET_COMMAND.is_file()
    assert FORGET_COMMAND.read_text().startswith("---")


def test_forget_command_reads_the_registrations_before_dropping_one():
    """Forgetting blind is how you drop the ancestor that every sibling folder
    depends on. The listing is what tells them apart."""
    text = FORGET_COMMAND.read_text()
    assert "memodi_list_paths" in text
    assert "memodi_workspace_forget" in text


def test_forget_command_refuses_to_drop_an_ancestor_on_the_users_behalf():
    text = FORGET_COMMAND.read_text()
    assert "Do not forget the ancestor" in text


def test_forget_command_waits_for_confirmation():
    text = FORGET_COMMAND.read_text()
    assert "WAIT" in text


def test_forget_command_states_that_memories_survive():
    """The whole reason the tool is safe to reach for — if the command does not
    say it, the user will assume the opposite and never run it."""
    text = FORGET_COMMAND.read_text()
    assert "memories are NOT deleted" in text
