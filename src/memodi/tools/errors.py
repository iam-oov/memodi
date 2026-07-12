import functools
import json


class NotAuthenticatedError(Exception):
    """Raised when the caller has no valid api key."""


class NotStartedError(Exception):
    """Raised when the path has no registered workspace."""


def handle_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except NotAuthenticatedError as e:
            return json.dumps({"error": str(e), "type": "not_authenticated"})
        except NotStartedError as e:
            return json.dumps({"error": str(e), "type": "not_started"})
        except ValueError as e:
            return json.dumps({"error": str(e), "type": "validation"})
        except Exception as e:
            return json.dumps({"error": str(e), "type": "internal"})

    return wrapper
