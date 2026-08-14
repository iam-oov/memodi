#!/bin/sh
# memodi — Plugin installer for Claude Code
#
# Installs the memodi plugin (hooks + skills) and configures
# the MCP server connection to the shared production instance.
#
# Usage:
#   ./install.sh             # opens a browser to log in automatically
#
# Or via curl:
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
#
# The browser hand-off needs a browser on this machine; without one (SSH,
# headless, no python3) it falls back to a paste prompt, so the key never
# lands in your shell history. It is persisted to your shell rc file
# automatically so the plugin hooks work in future sessions. To run
# non-interactively, export MEMODI_API_KEY beforehand.

set -e

MEMODI_BASE_URL="https://memodi.valdoh.com"
MEMODI_URL="${MEMODI_BASE_URL}/mcp"
LOGIN_URL="${MEMODI_BASE_URL}/login"

MARKER_START="# >>> memodi env >>>"
MARKER_END="# <<< memodi env <<<"

# Pick the shell rc file to persist exports to.
detect_rc() {
  case "$(basename "${SHELL:-/bin/sh}")" in
    zsh)  echo "$HOME/.zshrc" ;;
    bash) if [ -f "$HOME/.bash_profile" ]; then echo "$HOME/.bash_profile"; else echo "$HOME/.bashrc"; fi ;;
    *)    echo "$HOME/.profile" ;;
  esac
}

# Rewrite the memodi-managed block in an rc file (idempotent).
persist_env() {
  rc="$1"
  tmp="${rc}.memodi.tmp"
  touch "$rc"
  awk -v s="$MARKER_START" -v e="$MARKER_END" '
    $0==s {skip=1}
    skip==0 {print}
    $0==e {skip=0}
  ' "$rc" > "$tmp"
  {
    printf '%s\n' "$MARKER_START"
    printf 'export MEMODI_API_KEY="%s"\n' "$MEMODI_API_KEY"
    printf 'export MEMODI_MACHINE="%s"\n' "$MEMODI_MACHINE"
    printf '%s\n' "$MARKER_END"
  } >> "$tmp"
  mv "$tmp" "$rc"
}

echo "[1/6] Logging in..."

if ! command -v claude >/dev/null 2>&1; then
  echo "Error: claude CLI not found. Install Claude Code first."
  exit 1
fi

if [ -n "$MEMODI_API_KEY" ]; then
  PROBE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -X POST \
    -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"path": "/", "query": "login probe"}' \
    "${MEMODI_BASE_URL}/hooks/prompt-search" 2>/dev/null) || PROBE_CODE=""
  if [ "$PROBE_CODE" = "401" ]; then
    echo "      MEMODI_API_KEY in your environment is no longer valid — starting a fresh login."
    MEMODI_API_KEY=""
  else
    echo "      Using MEMODI_API_KEY from your environment (no login needed)."
    echo "      To log in as a different account: unset MEMODI_API_KEY and run again."
  fi
fi

if [ -z "$MEMODI_API_KEY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    LOGIN_OUT=$(python3 - "$LOGIN_URL" <<'PYEOF'
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
PYEOF
) || LOGIN_OUT=""
    MEMODI_API_KEY="${LOGIN_OUT%% *}"
    MEMODI_EMAIL="${LOGIN_OUT#* }"
  fi

  if [ -z "$MEMODI_API_KEY" ]; then
    echo "memodi needs a per-user api key before it can be installed."
    echo "Log in with Google here if you don't have one yet:"
    echo ""
    echo "  ${LOGIN_URL}"
    echo ""
    if [ -r /dev/tty ]; then
      printf "Paste your memodi api key (mmd_...): " > /dev/tty
      stty -echo < /dev/tty 2>/dev/null || true
      read MEMODI_API_KEY < /dev/tty
      stty echo < /dev/tty 2>/dev/null || true
      printf "\n" > /dev/tty
    fi
  fi
fi

if [ -z "$MEMODI_API_KEY" ]; then
  echo "Error: no api key provided."
  echo "Run again and paste your key, or export MEMODI_API_KEY beforehand."
  exit 1
fi

if [ -n "$MEMODI_EMAIL" ]; then
  echo "Logged in as $MEMODI_EMAIL"
fi

MEMODI_MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"

CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "Installing memodi plugin for Claude Code..."

# --- Add marketplace and refresh its snapshot ---
# add no-ops when the marketplace exists but does NOT refresh it, so
# update always runs after it — otherwise a machine that installed once
# keeps serving a stale snapshot forever.
echo "[2/6] Adding marketplace..."
claude plugin marketplace add iam-oov/memodi
claude plugin marketplace update memodi

# --- Install or update the plugin (hooks + skills + commands) ---
# install no-ops when already installed and update no-ops when freshly
# installed — running both covers first installs and upgrades alike.
echo "[3/6] Installing plugin..."
claude plugin install memodi@memodi
claude plugin update memodi@memodi

# --- Configure MCP server connection ---
echo "[4/6] Configuring MCP server..."
claude mcp remove memodi --scope user 2>/dev/null || true
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
  -H "X-Memodi-Machine: ${MEMODI_MACHINE}" \
  --scope user \
  memodi "$MEMODI_URL"

# --- Add wildcard permission for all memodi tools ---
echo "[5/6] Adding permissions..."
if [ -f "$CLAUDE_SETTINGS" ]; then
  if ! grep -q '"mcp__memodi__\*"' "$CLAUDE_SETTINGS" 2>/dev/null; then
    python3 -c "
import json, sys
with open('$CLAUDE_SETTINGS') as f:
    cfg = json.load(f)
allow = cfg.setdefault('permissions', {}).setdefault('allow', [])
if 'mcp__memodi__*' not in allow:
    allow.append('mcp__memodi__*')
with open('$CLAUDE_SETTINGS', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null || echo "  Warning: could not add permissions automatically. Add \"mcp__memodi__*\" to ~/.claude/settings.json manually."
  fi
else
  mkdir -p "$HOME/.claude"
  printf '{\n  "permissions": {\n    "allow": [\n      "mcp__memodi__*"\n    ]\n  }\n}\n' > "$CLAUDE_SETTINGS"
fi

# --- Persist env vars for the plugin hooks ---
# The hooks (session start, session end, post-compaction, subagent capture) read
# MEMODI_API_KEY and MEMODI_MACHINE from the shell environment. Without
# them they stay silently inactive in future sessions.
echo "[6/6] Persisting environment to your shell rc..."
RC_FILE="$(detect_rc)"
persist_env "$RC_FILE"

echo ""
echo "Done! Wrote MEMODI_API_KEY and MEMODI_MACHINE to ${RC_FILE}."
echo ""
echo "Next:"
echo "  1. Reload your shell:   source ${RC_FILE}   (or open a new terminal)"
echo "  2. Restart Claude Code, then run:  /memodi:start"
echo ""
echo "     /memodi:start registers this workspace (first time only) and loads"
echo "     its memories. Register the SAME workspace name on another machine"
echo "     and the memories are shared across both. After the first run, memory"
echo "     loads automatically whenever you open this repo."
