import socket

from memodi.config import settings

API_KEY_HEADER = "X-Memodi-Api-Key"
MACHINE_HEADER = "X-Memodi-Machine"


def client_context(ctx) -> dict:
    headers = _extract_headers(ctx)

    if headers is None:
        return {
            "api_key": settings.user_api_key,
            "machine": settings.machine or socket.gethostname(),
        }

    return {
        "api_key": headers.get(API_KEY_HEADER),
        "machine": headers.get(MACHINE_HEADER),
    }


def _extract_headers(ctx):
    if ctx is None:
        return None
    try:
        request_context = ctx.request_context
    except (LookupError, ValueError):
        return None
    request = getattr(request_context, "request", None)
    if request is None:
        return None
    return getattr(request, "headers", None)
