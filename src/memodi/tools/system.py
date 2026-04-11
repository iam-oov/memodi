import json

from memodi.database.connection import health_check


def ping() -> str:
    """Check if mimeco is alive."""
    return "pong"


def status() -> str:
    """Check mimeco server status including database connectivity."""
    result = health_check()
    return json.dumps(result, indent=2)
