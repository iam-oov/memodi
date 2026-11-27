import base64
import hashlib
import hmac
import json
import time

from starlette.requests import Request
from starlette.responses import Response

from memodi.config import settings

SESSION_COOKIE = "memodi_session"


def enabled() -> bool:
    return bool(settings.session_secret)


def _sign(payload: str) -> str:
    return hmac.new(
        settings.session_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def encode(user_id: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + settings.session_days * 86400)
    raw = json.dumps({"u": str(user_id), "exp": expires}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def decode(value: str | None, now: float | None = None) -> str | None:
    if not enabled() or not value or value.count(".") != 1:
        return None
    payload, signature = value.split(".")
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), int):
        return None
    if data["exp"] <= (now or time.time()):
        return None
    user_id = data.get("u")
    return user_id if isinstance(user_id, str) and user_id else None


def current_user_id(request: Request) -> str | None:
    return decode(request.cookies.get(SESSION_COOKIE))


def set_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        encode(user_id),
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=(settings.google_redirect_uri or "").startswith("https://"),
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
