import json
from importlib.metadata import version as pkg_version

from memodi.database.connection import health_check


def ping() -> str:
    return "pong"


def status() -> str:
    result = health_check()
    return json.dumps(result, indent=2)


def version() -> str:
    try:
        v = pkg_version("memodi")
    except Exception:
        v = "unknown"
    return json.dumps({"version": v})
