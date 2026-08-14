"""Sync plugin.json version with the package SSoT (src/memodi/__about__.py).

Usage:
    python scripts/sync_plugin_version.py           # writes plugin.json
    python scripts/sync_plugin_version.py --check   # CI: exit 1 on mismatch
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ABOUT_FILE = ROOT / "src" / "memodi" / "__about__.py"
PLUGIN_FILE = ROOT / "plugin" / "claude-code" / ".claude-plugin" / "plugin.json"
MARKETPLACE_FILE = ROOT / ".claude-plugin" / "marketplace.json"

VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)


def read_package_version(about_file: Path) -> str:
    """Extract __version__ from a Python SSoT file."""
    content = about_file.read_text()
    match = VERSION_RE.search(content)
    if not match:
        raise RuntimeError(f"__version__ not found in {about_file}")
    return match.group(1)


def read_plugin_version(plugin_file: Path) -> str:
    """Read the version field from plugin.json."""
    data = json.loads(plugin_file.read_text())
    return data["version"]


def write_plugin_version(plugin_file: Path, version: str) -> None:
    """Update the version field in plugin.json, preserving 2-space indent and trailing newline."""
    data = json.loads(plugin_file.read_text())
    data["version"] = version
    plugin_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_marketplace_version(marketplace_file: Path) -> str:
    """Read the plugins[0].version field from marketplace.json."""
    data = json.loads(marketplace_file.read_text())
    return data["plugins"][0]["version"]


def write_marketplace_version(marketplace_file: Path, version: str) -> None:
    """Update plugins[0].version in marketplace.json, preserving indent and newline."""
    data = json.loads(marketplace_file.read_text())
    data["plugins"][0]["version"] = version
    marketplace_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync plugin.json version from src/memodi/__about__.py"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify alignment without writing. Exit 1 on mismatch.",
    )
    args = parser.parse_args(argv)

    pkg = read_package_version(ABOUT_FILE)
    plugin = read_plugin_version(PLUGIN_FILE)
    marketplace = read_marketplace_version(MARKETPLACE_FILE)

    if args.check:
        mismatched = []
        if pkg != plugin:
            mismatched.append(f"plugin.json={plugin}")
        if pkg != marketplace:
            mismatched.append(f"marketplace.json={marketplace}")
        if mismatched:
            print(
                f"VERSION MISMATCH: package={pkg}, " + ", ".join(mismatched),
                file=sys.stderr,
            )
            print(
                "Run: uv run python scripts/sync_plugin_version.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: all in sync at {pkg}")
        return 0

    if pkg == plugin:
        print(f"plugin.json already in sync at {pkg}")
    else:
        write_plugin_version(PLUGIN_FILE, pkg)
        print(f"Updated plugin.json: {plugin} -> {pkg}")

    if pkg == marketplace:
        print(f"marketplace.json already in sync at {pkg}")
    else:
        write_marketplace_version(MARKETPLACE_FILE, pkg)
        print(f"Updated marketplace.json: {marketplace} -> {pkg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
