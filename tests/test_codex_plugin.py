"""Contract tests for the Codex plugin and installer."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "memodi"
MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
MCP_CONFIG = PLUGIN / ".mcp.json"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
SKILL = PLUGIN / "skills" / "memodi" / "SKILL.md"
INSTALLER = ROOT / "install-codex.sh"


def test_codex_plugin_points_to_memodi_with_environment_headers() -> None:
    config = json.loads(MCP_CONFIG.read_text())
    server = config["mcpServers"]["memodi"]

    assert server["type"] == "http"
    assert server["url"] == "https://memodi.valdoh.com/mcp"
    assert server["env_http_headers"] == {
        "X-Memodi-Api-Key": "MEMODI_API_KEY",
        "X-Memodi-Machine": "MEMODI_MACHINE",
    }
    assert "mmd_" not in MCP_CONFIG.read_text()


def test_codex_plugin_manifest_exposes_the_skill_and_mcp_server() -> None:
    manifest = json.loads(MANIFEST.read_text())

    assert manifest["name"] == "memodi"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"


def test_codex_marketplace_points_to_repo_plugin() -> None:
    marketplace = json.loads(MARKETPLACE.read_text())
    entry = marketplace["plugins"][0]

    assert marketplace["name"] == "memodi"
    assert entry["name"] == "memodi"
    assert entry["source"] == {
        "source": "local",
        "path": "./plugins/memodi",
    }
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }


def test_memodi_skill_covers_orientation_and_explicit_activation() -> None:
    text = SKILL.read_text()

    assert "memodi_context" in text
    assert "memodi_workspace_start" in text
    assert "not_started" in text
    assert "unless the user explicitly asks" in text


def test_codex_installer_is_valid_shell_and_uses_plugin_cli() -> None:
    result = subprocess.run(
        ["sh", "-n", str(INSTALLER)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    text = INSTALLER.read_text()
    assert "codex plugin marketplace add iam-oov/memodi --ref main" in text
    assert "codex plugin marketplace upgrade memodi" in text
    assert "codex plugin add memodi@memodi" in text
    assert "MEMODI_API_KEY" in text
    assert "MEMODI_MACHINE" in text


def test_codex_installer_configures_plugin_without_printing_key(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "codex.log"

    codex = fake_bin / "codex"
    codex.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CODEX_LOG"\n')
    codex.chmod(0o755)

    curl = fake_bin / "curl"
    curl.write_text('#!/bin/sh\nprintf "200"\n')
    curl.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    api_key = "mmd_test_key_that_must_stay_private"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(home),
        "SHELL": "/bin/zsh",
        "CODEX_LOG": str(command_log),
        "MEMODI_API_KEY": api_key,
        "MEMODI_MACHINE": "test-machine",
    }

    result = subprocess.run(
        ["sh", str(INSTALLER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert api_key not in result.stdout
    assert api_key not in result.stderr
    assert command_log.read_text().splitlines() == [
        "plugin marketplace add iam-oov/memodi --ref main",
        "plugin add memodi@memodi",
    ]
    shell_rc = (home / ".zshrc").read_text()
    assert f'export MEMODI_API_KEY="{api_key}"' in shell_rc
    assert 'export MEMODI_MACHINE="test-machine"' in shell_rc
