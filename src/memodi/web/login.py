import base64
import hmac
import html
import json
import secrets
import urllib.parse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

STATE_COOKIE = "memodi_oauth_state"
STATE_MAX_AGE = 600


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{
  font-family: sans-serif; max-width: 32rem; margin: 3rem auto; padding: 0 1rem;
}}
label {{ display: block; margin-bottom: 1rem; }}
input {{ display: block; width: 100%; padding: 0.4rem; margin-top: 0.25rem; }}
pre {{
  background: #eee; padding: 1rem; overflow-x: auto;
  word-break: break-all; white-space: pre-wrap;
}}
.warning {{ color: #a33; font-weight: bold; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _error_page(status_code: int, message: str) -> HTMLResponse:
    body = f"<h1>Login failed</h1><p>{html.escape(message)}</p>"
    response = HTMLResponse(
        _page("memodi login — error", body), status_code=status_code
    )
    response.delete_cookie(STATE_COOKIE)
    return response


def _disabled_page() -> HTMLResponse:
    body = "<h1>Login is disabled</h1><p>Login is not currently configured.</p>"
    return HTMLResponse(_page("memodi login — disabled", body), status_code=503)


def _success_page(email: str, api_key: str) -> HTMLResponse:
    safe_email = html.escape(email)
    safe_key = html.escape(api_key)
    body = f"""
<h1>Logged in</h1>
<p>Email: {safe_email}</p>
<p class="warning">Save this key now — it will not be shown again.</p>
<pre id="api-key">{safe_key}</pre>
<button id="copy-btn" type="button" onclick="copyLoginKey()">Copy key</button>
<p>Paste this key back into your terminal.</p>
<script>
function copyLoginKey() {{
  var key = document.getElementById('api-key').textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(key);
  }} else {{
    var el = document.createElement('textarea');
    el.value = key;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
  }}
}}
</script>
"""
    response = HTMLResponse(_page("memodi login — your api key", body))
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(STATE_COOKIE)
    return response


def _configured() -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_redirect_uri
    )


async def get_login(request: Request) -> HTMLResponse | RedirectResponse:
    if not _configured():
        return _disabled_page()

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.google_redirect_uri.startswith("https://"),
    )
    return response


async def _exchange_code(code: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError:
            return None
    if response.status_code != 200:
        return None
    try:
        tokens = response.json()
    except ValueError:
        return None
    return tokens if isinstance(tokens, dict) else None


def _email_from_id_token(id_token: str) -> str:
    """No signature verification: id_token comes straight from Google's own
    token endpoint over TLS, never from a client-supplied value, so there is
    no third party in a position to have forged it in transit (OIDC
    §3.1.3.7). aud, iss, and email_verified are still checked below as the
    substantive gates against a misconfigured or wrong client.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed id_token.")

    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("Malformed id_token.") from e
    if not isinstance(payload, dict):
        raise ValueError("Malformed id_token.")

    if payload.get("aud") != settings.google_client_id:
        raise PermissionError("Unexpected audience in id_token.")
    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise PermissionError("Unexpected issuer in id_token.")
    if payload.get("email_verified") is not True:
        raise PermissionError("Email is not verified.")

    email = payload.get("email")
    if not email:
        raise ValueError("id_token has no email claim.")
    return email


async def get_oauth_callback(request: Request) -> HTMLResponse:
    if not _configured():
        return _disabled_page()

    error = request.query_params.get("error")
    if error:
        return _error_page(400, f"Google returned an error: {error}")

    state = request.query_params.get("state") or ""
    cookie_state = request.cookies.get(STATE_COOKIE) or ""
    if (
        not state
        or not cookie_state
        or not hmac.compare_digest(state.encode(), cookie_state.encode())
    ):
        return _error_page(400, "Invalid or missing state.")

    code = request.query_params.get("code")
    if not code:
        return _error_page(400, "Missing authorization code.")

    tokens = await _exchange_code(code)
    if tokens is None:
        return _error_page(502, "Could not reach Google to exchange the code.")

    id_token = tokens.get("id_token")
    if not id_token:
        return _error_page(502, "Google response is missing an id_token.")

    try:
        email = _email_from_id_token(id_token)
    except PermissionError as e:
        return _error_page(403, str(e))
    except ValueError as e:
        return _error_page(400, str(e))

    ensure_schema()
    user = auth_repository.login_with_email(email)

    return _success_page(user["email"], user["api_key"])
