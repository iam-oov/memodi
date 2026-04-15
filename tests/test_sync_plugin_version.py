"""Tests for scripts/sync_plugin_version.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from sync_plugin_version import (  # noqa: E402
    read_package_version,
    read_plugin_version,
    write_plugin_version,
)


class TestReadPackageVersion:
    def test_extracts_double_quoted_version(self, tmp_path: Path) -> None:
        about = tmp_path / "__about__.py"
        about.write_text('__version__ = "1.2.3"\n')
        assert read_package_version(about) == "1.2.3"

    def test_extracts_single_quoted_version(self, tmp_path: Path) -> None:
        about = tmp_path / "__about__.py"
        about.write_text("__version__ = '1.2.3'\n")
        assert read_package_version(about) == "1.2.3"

    def test_ignores_other_dunder_assignments(self, tmp_path: Path) -> None:
        about = tmp_path / "__about__.py"
        about.write_text(
            '__author__ = "someone"\n'
            '__version__ = "2.0.0"\n'
            '__license__ = "MIT"\n'
        )
        assert read_package_version(about) == "2.0.0"

    def test_raises_when_version_missing(self, tmp_path: Path) -> None:
        about = tmp_path / "__about__.py"
        about.write_text('__author__ = "someone"\n')
        with pytest.raises(RuntimeError, match="__version__ not found"):
            read_package_version(about)


class TestReadPluginVersion:
    def test_reads_version_field(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(json.dumps({"name": "memodi", "version": "1.2.3"}))
        assert read_plugin_version(plugin) == "1.2.3"


class TestWritePluginVersion:
    def test_updates_version_field(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(json.dumps({"name": "memodi", "version": "1.0.0"}))
        write_plugin_version(plugin, "2.0.0")
        data = json.loads(plugin.read_text())
        assert data["version"] == "2.0.0"

    def test_preserves_other_fields(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(
            json.dumps(
                {"name": "memodi", "version": "1.0.0", "author": {"name": "x"}}
            )
        )
        write_plugin_version(plugin, "2.0.0")
        data = json.loads(plugin.read_text())
        assert data["name"] == "memodi"
        assert data["author"] == {"name": "x"}

    def test_file_ends_with_newline(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(json.dumps({"version": "1.0.0"}))
        write_plugin_version(plugin, "2.0.0")
        assert plugin.read_text().endswith("\n")

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(json.dumps({"name": "memodi", "version": "1.0.0"}))
        write_plugin_version(plugin, "2.0.0")
        content = plugin.read_text()
        assert '  "version": "2.0.0"' in content


class TestRoundtrip:
    def test_write_then_read_returns_same_version(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        plugin.write_text(json.dumps({"name": "memodi", "version": "0.0.1"}))
        write_plugin_version(plugin, "9.9.9")
        assert read_plugin_version(plugin) == "9.9.9"
