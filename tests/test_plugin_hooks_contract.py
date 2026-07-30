"""Contract tests over the SHIPPED plugin files.

The plugin is markdown and shell, so the only way to pin its contract is to
read what actually ships. These guard the split the hooks depend on: the
SessionEnd/SessionStart hooks own the session lifecycle over plain HTTP, and
the model owns only the summary. A file telling the model to call
memodi_session_start re-opens an untagged session and the hook's close can
never match it again — the whole feature goes inert.
"""

from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "claude-code"
SKILL = PLUGIN / "skills" / "memory" / "SKILL.md"
START_COMMAND = PLUGIN / "commands" / "start.md"
SESSION_START_HOOK = PLUGIN / "scripts" / "session-start.sh"
SUBAGENT_STOP_HOOK = PLUGIN / "scripts" / "subagent-stop.sh"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

_PROHIBITION_MARKERS = ("do not", "never", "not call", "no llames")


def _lines_instructing_session_start(text: str) -> list[str]:
    """Lines naming memodi_session_start without forbidding it."""
    return [
        line
        for line in text.splitlines()
        if "memodi_session_start" in line
        and not any(marker in line.lower() for marker in _PROHIBITION_MARKERS)
    ]


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
