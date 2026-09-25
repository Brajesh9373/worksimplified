#!/usr/bin/env bash
#
# WorkSimplified — start the system.
#
#   ./start.sh            check the environment, ask for any credential that is
#                         missing, then run the service
#   ./start.sh --check    do the checks and the prompts, then exit
#
# The service is the whole product: the console at /, the intake that starts a
# run, /api/progress for live status, /api/download for the files each stage
# produced, the Telegram poller and the WhatsApp inbound endpoint.
#
# Configuration lives in two dotenv files. Anything already written there is left
# alone — the prompts below only fire for a key that is still empty, and a real
# environment variable wins over both files.
#
#   my_agents/delivery_pipeline/.env    LLM_API_KEY, LLM_API_BASE, LLM_MODEL, FRAPPE_*
#   my_agents/connectors/.env           host/port, TELEGRAM_BOT_TOKEN, WHATSAPP_*
#   my_agents/*_agent/.env              LLM_* (filled in from the pipeline answer)
#
# Nothing you type is echoed back or logged.
#
# The console is served on the first free port from 8765 up (8080, 8000 and
# friends are taken by other services); the port actually used is printed.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PIPE_ENV="$ROOT/my_agents/delivery_pipeline/.env"
CONN_ENV="$ROOT/my_agents/connectors/.env"

CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (try --check)" >&2; exit 2 ;;
esac

info() { printf '  %s\n' "$*"; }
head_() { printf '\n%s\n' "$*"; }

# ---------------------------------------------------------------- interpreter
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "python3 not found — install Python 3.10+ (or create .venv with: python3 -m venv .venv)" >&2
  exit 1
fi

head_ "Environment"
info "python: $("$PY" -V 2>&1) ($PY)"

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "  missing packages (fastapi/uvicorn) — install them first:" >&2
  echo "      $PY -m pip install -e .       # or: uv sync" >&2
  exit 1
fi
info "packages: fastapi, uvicorn ok"

# -------------------------------------------------------------- dotenv helpers
env_get() {  # file key -> value on stdout (empty when unset)
  "$PY" - "$1" "$2" <<'PY'
import sys
from pathlib import Path
path, key = Path(sys.argv[1]), sys.argv[2]
if path.is_file():
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            print(v.strip().strip('"').strip("'"))
            break
PY
}

env_set() {  # file key value  (replace in place, else append; creates the file)
  "$PY" - "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path
path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
pending = {key: value}
out = []
for line in lines:
    s = line.strip()
    if "=" in s and not s.startswith("#") and s.partition("=")[0].strip() in pending:
        k = s.partition("=")[0].strip()
        out.append(f"{k}={pending.pop(k)}")
        continue
    out.append(line)
if pending:
    if out and out[-1].strip():
        out.append("")
    out.extend(f"{k}={v}" for k, v in pending.items())
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

mask() {  # show only the tail of a secret
  local v="${1:-}"
  if [[ -z "$v" ]]; then printf 'not set'; return; fi
  if (( ${#v} > 8 )); then printf 'set (…%s)' "${v: -4}"; else printf 'set'; fi
}

ask() {  # key file label default [secret]
  local key="$1" file="$2" label="$3" default="${4:-}" secret="${5:-}"
  local cur reply
  cur="$(env_get "$file" "$key")"
  if [[ -n "$cur" ]]; then
    if [[ "$secret" == "secret" ]]; then info "$label: $(mask "$cur")"; else info "$label: $cur"; fi
    return 0
  fi
  if [[ ! -t 0 ]]; then
    info "$label: not set (non-interactive — left empty)"
    return 0
  fi
  if [[ "$secret" == "secret" ]]; then
    read -r -s -p "  $label${default:+  [$default]}: " reply; echo
  else
    read -r -p "  $label${default:+  [$default]}: " reply
  fi
  reply="${reply:-$default}"
  if [[ -n "$reply" ]]; then
    env_set "$file" "$key" "$reply"
    info "$label: saved"
  else
    info "$label: left empty — you can set it later in the Connectors tab"
  fi
}

# ------------------------------------------------------------------ credentials
head_ "Credentials"

ask LLM_API_KEY "$PIPE_ENV" "LLM API key (needed to run a project)" "" secret
ask LLM_API_BASE "$PIPE_ENV" "LLM API base" "https://api.commandcode.ai/provider/v1"
ask LLM_MODEL "$PIPE_ENV" "LLM model" "openai/deepseek/deepseek-v4.1-flash"
ask FRAPPE_BASE_URL "$PIPE_ENV" "Frappe base URL (optional, Enter to skip)" ""
ask FRAPPE_USERNAME "$PIPE_ENV" "Frappe username (optional)" ""
ask FRAPPE_PASSWORD "$PIPE_ENV" "Frappe password (optional)" "" secret

head_ "Service"
ask CONNECTORS_HOST "$CONN_ENV" "Bind host" "127.0.0.1"
ask CONNECTORS_PORT "$CONN_ENV" "Bind port" "8765"
ask TELEGRAM_BOT_TOKEN "$CONN_ENV" "Telegram bot token (optional)" "" secret
ask WHATSAPP_ENABLED "$CONN_ENV" "Enable WhatsApp (true/false)" "false"

# The agents each read their own .env when they run under `adk web`; mirror the
# pipeline answers there so the console and the agents agree on the model.
LLM_KEY="$(env_get "$PIPE_ENV" LLM_API_KEY)"
LLM_BASE="$(env_get "$PIPE_ENV" LLM_API_BASE)"
LLM_NAME="$(env_get "$PIPE_ENV" LLM_MODEL)"
FRAPPE_BASE="$(env_get "$PIPE_ENV" FRAPPE_BASE_URL)"
for agent in ba project functional technical frappe; do
  f="$ROOT/my_agents/${agent}_agent/.env"
  [[ -f "$f" ]] || continue
  [[ -n "$LLM_KEY" && -z "$(env_get "$f" LLM_API_KEY)" ]] && env_set "$f" LLM_API_KEY "$LLM_KEY"
  [[ -n "$LLM_BASE" && -z "$(env_get "$f" LLM_API_BASE)" ]] && env_set "$f" LLM_API_BASE "$LLM_BASE"
  [[ -n "$LLM_NAME" && -z "$(env_get "$f" LLM_MODEL)" ]] && env_set "$f" LLM_MODEL "$LLM_NAME"
done

head_ "Config in effect"
info "LLM API key : $(mask "$LLM_KEY")"
info "LLM base    : ${LLM_BASE:-unset}"
info "LLM model   : ${LLM_NAME:-unset}"
info "Frappe      : ${FRAPPE_BASE:-unset}"
info "Telegram    : $(mask "$(env_get "$CONN_ENV" TELEGRAM_BOT_TOKEN)")"
info "WhatsApp    : $(env_get "$CONN_ENV" WHATSAPP_ENABLED || true)"
info "Bind        : $(env_get "$CONN_ENV" CONNECTORS_HOST || echo 127.0.0.1):$(env_get "$CONN_ENV" CONNECTORS_PORT || echo 8765) (walks up if busy)"
if [[ -z "$LLM_KEY" && -z "${LLM_API_KEY:-}" ]]; then
  info "note: no LLM key yet — the console starts, but a run will fail until one is set."
fi

if (( CHECK_ONLY )); then
  head_ "Checks only — nothing started."
  exit 0
fi

# ------------------------------------------------------------------------ start
HOST="$(env_get "$CONN_ENV" CONNECTORS_HOST)"; HOST="${HOST:-127.0.0.1}"
WANT="$(env_get "$CONN_ENV" CONNECTORS_PORT)"; WANT="${WANT:-8765}"
case "$WANT" in ''|*[!0-9]*) WANT=8765 ;; esac

head_ "Starting"

health_ok() {  # url — an instance of this service is already answering there
  "$PY" - "$1" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.5) as r:
        sys.exit(0 if json.load(r).get("ok") else 1)
except Exception:
    sys.exit(1)
PY
}

port_free() {  # host port — nothing else is listening on it
  "$PY" - "$1" "$2" <<'PY'
import socket, sys
sock = socket.socket()
try:
    sock.bind((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

# 8000 and the neighbouring well-known ports belong to other services on the
# server, so start at 8765 and walk up to the first free one. A healthy
# instance on any of them means the system is already up — say so instead of
# letting uvicorn die with a bare "address already in use".
PORT=""
for (( p = WANT; p < WANT + 20 && p < 65536; p++ )); do
  if health_ok "http://$HOST:$p/health"; then
    info "already running at http://$HOST:$p/ — stop it first (Ctrl-C on that"
    info "  terminal, or: kill \$(pgrep -f 'connectors_service[.]py'))"
    exit 0
  fi
  if port_free "$HOST" "$p"; then
    PORT="$p"
    break
  fi
done
if [[ -z "$PORT" ]]; then
  echo "  no free port in $WANT–$((WANT + 19)) — set CONNECTORS_PORT in $CONN_ENV" >&2
  exit 1
fi
if [[ "$PORT" != "$WANT" ]]; then
  info "port $WANT busy — using $PORT"
fi
URL="http://$HOST:$PORT"
info "$URL/  (Ctrl-C stops)"
if [[ "$(env_get "$CONN_ENV" WHATSAPP_ENABLED)" =~ ^(1|true|yes|on)$ ]]; then
  info "WhatsApp is enabled — start the bridge if it is not running yet:"
  info "  cd my_agents/connectors/whatsapp_bridge && npm install && node index.js"
fi

# The chosen port is passed on the command line so it wins over whatever is in
# the dotenv file.
CONNECTORS_PORT="$PORT" "$PY" "$ROOT/my_agents/connectors_service.py" &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

# Wait for /health so a bad start says so instead of looking like a hang.
if "$PY" - "$URL/health" "$PID" <<'PY'
import json, os, sys, time, urllib.request

url, pid = sys.argv[1], int(sys.argv[2])
last = ""
for _ in range(40):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            data = json.load(r)
        ch = data.get("channels", {})
        print("  healthy — channels: "
              + ", ".join(f"{k}={'on' if v else 'off'}" for k, v in ch.items())
              + f" | replay barrier: {data.get('replay_barrier', '?')}")
        sys.exit(0)
    except Exception as exc:            # not up yet (or died)
        last = str(exc)
        try:
            os.kill(pid, 0)             # child gone — stop waiting
        except OSError:
            break
        time.sleep(0.5)
print(f"  service did not answer {url}: {last}", file=sys.stderr)
sys.exit(1)
PY
then
  info "console: $URL/"
else
  wait "$PID" 2>/dev/null || true
  echo "  startup failed — see the output above." >&2
  exit 1
fi

wait "$PID"
