"""Single source of truth for the memodi package version.

Read by:
- pyproject.toml via [tool.hatch.version] (dynamic build version)
- importlib.metadata.version("memodi") at runtime (memodi_version tool)
- scripts/sync_plugin_version.py (propagates to plugin.json)
"""

__version__ = "0.20.0"
