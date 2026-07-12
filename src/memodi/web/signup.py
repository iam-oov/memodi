import hmac
import html

from starlette.requests import Request
from starlette.responses import HTMLResponse

from memodi.config import settings
from memodi.database import auth_repository
from memodi.database.connection import ensure_schema

MAX_SIGNUP_BODY = 8 * 1024


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
    body = f"<h1>Signup failed</h1><p>{html.escape(message)}</p>"
    return HTMLResponse(_page("memodi signup — error", body), status_code=status_code)


def _disabled_page() -> HTMLResponse:
    body = "<h1>Signup is disabled</h1><p>Signup is not currently open.</p>"
    return HTMLResponse(_page("memodi signup — disabled", body), status_code=503)


def _form_page() -> HTMLResponse:
    body = """
<h1>memodi signup</h1>
<form method="post" action="/signup">
  <label>Email
    <input type="email" name="email" required>
  </label>
  <label>Invite code
    <input type="password" name="invite_code" required>
  </label>
  <button type="submit">Sign up</button>
</form>
"""
    return HTMLResponse(_page("memodi signup", body))


def _success_page(email: str, api_key: str) -> HTMLResponse:
    safe_email = html.escape(email)
    safe_key = html.escape(api_key)
    body = f"""
<h1>Account created</h1>
<p>Email: {safe_email}</p>
<p class="warning">Save this key now — it will not be shown again.</p>
<pre id="api-key">{safe_key}</pre>
<button id="copy-btn" type="button" onclick="copySignupKey()">Copy key</button>
<script>
function copySignupKey() {{
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
    return HTMLResponse(_page("memodi signup — your api key", body))


async def get_signup(request: Request) -> HTMLResponse:
    if not settings.signup_code:
        return _disabled_page()
    return _form_page()


async def post_signup(request: Request) -> HTMLResponse:
    if not settings.signup_code:
        return _disabled_page()

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_SIGNUP_BODY:
                return _error_page(413, "Request body too large.")
        except ValueError:
            return _error_page(400, "Invalid Content-Length header.")

    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_SIGNUP_BODY:
            return _error_page(413, "Request body too large.")
    request._body = body

    form = await request.form()
    invite_code = str(form.get("invite_code") or "")
    email = str(form.get("email") or "").strip()

    if not hmac.compare_digest(
        invite_code.encode("utf-8"), settings.signup_code.encode("utf-8")
    ):
        return _error_page(403, "Invalid invite code.")

    if not email or "@" not in email:
        return _error_page(400, "Please provide a valid email address.")

    ensure_schema()

    try:
        user = auth_repository.create_user(email)
    except ValueError:
        return _error_page(409, f"An account with email '{email}' already exists.")

    return _success_page(user["email"], user["api_key"])
