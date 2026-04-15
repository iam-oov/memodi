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
    plugin_file.write_text(json.dumps(data, indent=2) + "\n")


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

    if args.check:
        if pkg != plugin:
            print(
                f"VERSION MISMATCH: package={pkg}, plugin={plugin}",
                file=sys.stderr,
            )
            print(
                "Run: uv run python scripts/sync_plugin_version.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: both at {pkg}")
        return 0

    if pkg == plugin:
        print(f"Already in sync at {pkg}")
        return 0

    write_plugin_version(PLUGIN_FILE, pkg)
    print(f"Updated plugin.json: {plugin} -> {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
