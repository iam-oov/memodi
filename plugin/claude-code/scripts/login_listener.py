import hmac
import http.server
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser

NONCE = secrets.token_urlsafe(24)
STATE: dict[str, str | None] = {"key": None, "email": None}

KEY_RE = re.compile(r"\Ammd_[A-Za-z0-9_-]{16,128}\Z")
EMAIL_RE = re.compile(r"\A[^\s@]+@[^\s@]+\Z")
EMAIL_MAX = 254

SUCCESS_BODY = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>memodi login</title></head><body>"
    b"<p>Logged in. You can close this tab and return to your terminal.</p>"
    b'<script>history.replaceState(null, "", "/")</script>'
    b"</body></html>"
)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query, keep_blank_values=True
        )
        key = query.get("key", [""])[0]
        if not KEY_RE.fullmatch(key):
            self.send_response(400)
            self.end_headers()
            return

        nonce = query.get("nonce", [None])[0]
        if nonce is None or not hmac.compare_digest(nonce.encode(), NONCE.encode()):
            self.send_response(403)
            self.end_headers()
            return

        email = query.get("email", [""])[0]
        if len(email) > EMAIL_MAX or not EMAIL_RE.fullmatch(email):
            self.send_response(400)
            self.end_headers()
            return

        STATE["key"] = key
        STATE["email"] = email

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(SUCCESS_BODY)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(SUCCESS_BODY)
        self.server.done = True

    def log_message(self, *args: object) -> None:
        pass


login_url = sys.argv[1]

srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
srv.done = False
port = srv.server_address[1]

url = f"{login_url}?port={port}&nonce={NONCE}"
print(f"Open this URL to log in:\n{url}", file=sys.stderr)

if os.environ.get("MEMODI_NO_BROWSER") != "1":
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

timeout = float(os.environ.get("MEMODI_LOGIN_TIMEOUT", "180"))
deadline = time.monotonic() + timeout

while not srv.done:
    # Recomputed off the absolute deadline each pass, so a stray request (e.g. /favicon.ico) can never push the wait later.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        sys.exit(1)
    srv.timeout = remaining
    srv.handle_request()

print(f"{STATE['key']} {STATE['email']}")
sys.exit(0)
