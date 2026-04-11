import functools
import json


def handle_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            return json.dumps({"error": str(e), "type": "validation"})
        except Exception as e:
            return json.dumps({"error": str(e), "type": "internal"})

    return wrapper
