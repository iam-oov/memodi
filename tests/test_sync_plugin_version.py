"""Tests for scripts/sync_plugin_version.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sync_plugin_version  # noqa: E402
from sync_plugin_version import (  # noqa: E402
    read_marketplace_version,
    read_package_version,
    read_plugin_version,
    write_marketplace_version,
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


class TestReadMarketplaceVersion:
    def test_reads_version_field(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(
            json.dumps({"plugins": [{"name": "memodi", "version": "1.2.3"}]})
        )
        assert read_marketplace_version(marketplace) == "1.2.3"


class TestWriteMarketplaceVersion:
    def test_updates_version_field(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(
            json.dumps({"plugins": [{"name": "memodi", "version": "1.0.0"}]})
        )
        write_marketplace_version(marketplace, "2.0.0")
        data = json.loads(marketplace.read_text())
        assert data["plugins"][0]["version"] == "2.0.0"

    def test_preserves_other_fields(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(
            json.dumps(
                {
                    "name": "memodi",
                    "plugins": [
                        {
                            "name": "memodi",
                            "version": "1.0.0",
                            "category": "productivity",
                        }
                    ],
                }
            )
        )
        write_marketplace_version(marketplace, "2.0.0")
        data = json.loads(marketplace.read_text())
        assert data["name"] == "memodi"
        assert data["plugins"][0]["category"] == "productivity"

    def test_file_ends_with_newline(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(json.dumps({"plugins": [{"version": "1.0.0"}]}))
        write_marketplace_version(marketplace, "2.0.0")
        assert marketplace.read_text().endswith("\n")

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(
            json.dumps({"plugins": [{"name": "memodi", "version": "1.0.0"}]})
        )
        write_marketplace_version(marketplace, "2.0.0")
        content = marketplace.read_text()
        assert '  "version": "2.0.0"' in content


class TestMarketplaceRoundtrip:
    def test_write_then_read_returns_same_version(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        marketplace.write_text(
            json.dumps({"plugins": [{"name": "memodi", "version": "0.0.1"}]})
        )
        write_marketplace_version(marketplace, "9.9.9")
        assert read_marketplace_version(marketplace) == "9.9.9"


class TestNonAsciiIsPreservedLiterally:
    """A version bump must not rewrite prose. json.dumps defaults to
    ensure_ascii=True, which turns every em dash and accent into a \\uXXXX
    escape — a silent diff over text nobody asked to touch."""

    def test_marketplace_description_keeps_its_em_dash(self, tmp_path: Path) -> None:
        marketplace = tmp_path / "marketplace.json"
        description = "Memoria distribuida — persistente entre sesiones"
        marketplace.write_text(
            json.dumps(
                {
                    "description": description,
                    "plugins": [{"version": "1.0.0"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        write_marketplace_version(marketplace, "2.0.0")

        raw = marketplace.read_text(encoding="utf-8")
        assert description in raw
        assert "\\u" not in raw

    def test_plugin_description_keeps_its_em_dash(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin.json"
        description = "Distributed memory — across sessions"
        plugin.write_text(
            json.dumps({"description": description, "version": "1.0.0"}),
            encoding="utf-8",
        )

        write_plugin_version(plugin, "2.0.0")

        raw = plugin.read_text(encoding="utf-8")
        assert description in raw
        assert "\\u" not in raw


def _write_fixture_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pkg_version: str,
    plugin_version: str,
    marketplace_version: str,
) -> None:
    about = tmp_path / "__about__.py"
    about.write_text(f'__version__ = "{pkg_version}"\n')
    plugin = tmp_path / "plugin.json"
    plugin.write_text(json.dumps({"version": plugin_version}))
    codex_plugin = tmp_path / "codex-plugin.json"
    codex_plugin.write_text(json.dumps({"version": plugin_version}))
    marketplace = tmp_path / "marketplace.json"
    marketplace.write_text(json.dumps({"plugins": [{"version": marketplace_version}]}))
    monkeypatch.setattr(sync_plugin_version, "ABOUT_FILE", about)
    monkeypatch.setattr(sync_plugin_version, "PLUGIN_FILE", plugin)
    monkeypatch.setattr(sync_plugin_version, "CODEX_PLUGIN_FILE", codex_plugin)
    monkeypatch.setattr(sync_plugin_version, "MARKETPLACE_FILE", marketplace)


class TestMainCheck:
    def test_passes_when_everything_in_sync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "1.0.0", "1.0.0", "1.0.0")
        assert sync_plugin_version.main(["--check"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_fails_when_plugin_json_out_of_sync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "1.0.0", "0.9.0", "1.0.0")
        assert sync_plugin_version.main(["--check"]) == 1
        error = capsys.readouterr().err
        assert "claude plugin.json=0.9.0" in error
        assert "codex plugin.json=0.9.0" in error

    def test_fails_when_only_codex_plugin_json_is_out_of_sync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "1.0.0", "1.0.0", "1.0.0")
        sync_plugin_version.CODEX_PLUGIN_FILE.write_text(
            json.dumps({"version": "0.9.0"})
        )
        assert sync_plugin_version.main(["--check"]) == 1
        assert "codex plugin.json=0.9.0" in capsys.readouterr().err

    def test_fails_when_marketplace_json_out_of_sync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "1.0.0", "1.0.0", "0.9.0")
        assert sync_plugin_version.main(["--check"]) == 1
        assert "marketplace.json=0.9.0" in capsys.readouterr().err


class TestMainWrite:
    def test_writes_all_files_when_out_of_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "2.0.0", "1.0.0", "1.0.0")
        assert sync_plugin_version.main([]) == 0
        assert (
            json.loads(sync_plugin_version.PLUGIN_FILE.read_text())["version"]
            == "2.0.0"
        )
        assert (
            json.loads(sync_plugin_version.CODEX_PLUGIN_FILE.read_text())["version"]
            == "2.0.0"
        )
        assert (
            json.loads(sync_plugin_version.MARKETPLACE_FILE.read_text())["plugins"][0][
                "version"
            ]
            == "2.0.0"
        )

    def test_no_writes_when_already_in_sync(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _write_fixture_files(tmp_path, monkeypatch, "1.0.0", "1.0.0", "1.0.0")
        assert sync_plugin_version.main([]) == 0
        assert "already in sync" in capsys.readouterr().out.lower()
