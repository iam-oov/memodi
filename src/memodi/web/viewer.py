import html
import uuid
from datetime import UTC, datetime

from markdown_it import MarkdownIt
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from memodi.config import settings
from memodi.database import repository
from memodi.database.connection import ensure_schema
from memodi.web import session

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_markdown = MarkdownIt("js-default")


def _document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: sans-serif; max-width: 48rem; margin: 2rem auto;
  padding: 0 1rem; line-height: 1.5; }}
pre {{ background: #f4f4f4; padding: 1rem; overflow-x: auto; }}
code {{ background: #f4f4f4; }}
.meta {{ color: #555; font-size: 0.9rem; }}
.outcome {{ border-left: 4px solid #2a7; padding-left: 0.75rem; }}
section {{ border-top: 1px solid #ddd; margin-top: 1.5rem; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _respond(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(_document(title, body), status_code=status_code)
    response.headers.update(SECURITY_HEADERS)
    return response


def _not_found() -> HTMLResponse:
    return _respond("memodi — not found", "<h1>Not found</h1>", status_code=404)


def _status(obs: dict, now: datetime) -> str:
    parts = [
        f"{obs['workspace']}/{obs['project']}",
        obs["type"],
        obs.get("horizon") or "",
    ]
    if obs.get("resolved_at"):
        parts.append(f"resolved {obs['resolved_at']:%Y-%m-%d %H:%M} UTC")
    elif obs.get("horizon") == "todo":
        parts.append("open")
    current, total = obs.get("phase_current"), obs.get("phase_total")
    if current or total:
        phase = f"F-{current or 0}/{total}" if total else f"F-{current}"
        label = obs.get("phase_label")
        parts.append(f"{phase} ({label})" if label else phase)
    until = obs.get("claimed_until")
    if obs.get("claimed_by") and until and until > now:
        holder = obs.get("claimed_label") or obs["claimed_by"]
        parts.append(f"held by {holder} until {until:%Y-%m-%d %H:%M} UTC")
    return " · ".join(html.escape(str(p)) for p in parts if p)


def render(obs: dict) -> str:
    now = datetime.now(UTC)
    body = [
        f"<h1>{html.escape(obs['title'])}</h1>",
        f'<p class="meta">{_status(obs, now)}</p>',
    ]
    if obs.get("outcome"):
        body.append(f'<p class="outcome">{html.escape(obs["outcome"])}</p>')
    if repository.plan_is_live(obs, settings.plan_grace_days):
        body.append(_section("Plan", obs["plan_md"]))
    body.append(_section("Note", obs["content"] or ""))
    return "\n".join(body)


def _section(heading: str, markdown: str) -> str:
    return f"<section><h2>{heading}</h2>{_markdown.render(markdown)}</section>"


async def get_observation_page(request: Request) -> HTMLResponse | RedirectResponse:
    try:
        obs_id = str(uuid.UUID(request.path_params["observation_id"]))
    except ValueError:
        return _not_found()
    if not session.enabled():
        return _respond(
            "memodi — web view disabled",
            "<h1>Web view is disabled</h1>"
            "<p>Set MEMODI_SESSION_SECRET to enable it.</p>",
            status_code=503,
        )
    user_id = session.current_user_id(request)
    if user_id is None:
        response = RedirectResponse(f"/login?next=/o/{obs_id}", status_code=302)
        response.headers["Cache-Control"] = "no-store"
        return response
    ensure_schema()
    obs = repository.get_observation_for_owner(obs_id, user_id)
    if obs is None:
        return _not_found()
    return _respond(f"memodi — {obs['title']}", render(obs))


async def get_web_logout(request: Request) -> HTMLResponse:
    response = _respond("memodi — logged out", "<h1>Logged out</h1>")
    session.clear_cookie(response)
    return response
