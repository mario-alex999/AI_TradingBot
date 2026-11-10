#!/bin/bash
# AURUM AI - run the whole project on a Mac and open it in the browser.
#   Start:   bash ~/AI_TradingBot/AURUM_AI/scripts/start_mac.sh          (shortcut: aurum-start)
#   Stop:    bash ~/AI_TradingBot/AURUM_AI/scripts/start_mac.sh --stop   (shortcut: aurum-stop)
#   Status:  bash ~/AI_TradingBot/AURUM_AI/scripts/start_mac.sh --status
#
# Starts, in order, and only what is not already running:
#   1. the database (TimescaleDB in Docker, docker-compose.yml)
#   2. the Python environment (.venv, Python 3.11+) and the database tables (init-db)
#   3. the API on http://127.0.0.1:8000  (docs: /docs)
#   4. the dashboard on http://localhost:5173
# Logs: AURUM_AI/logs/api.txt and AURUM_AI/logs/frontend.txt.
#
# MT5 is not started here: it runs on Windows only. Without it, the API and the dashboard work on the data already in
# the database, and the dashboard can show its labelled demo data (Settings -> Data).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AURUM_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$AURUM_DIR")"
FRONTEND_DIR="$REPO_ROOT/aurum-frontend"
LOG_DIR="$AURUM_DIR/logs"
API_PID="$LOG_DIR/api.pid"
WEB_PID="$LOG_DIR/frontend.pid"
API_URL="http://127.0.0.1:8000"
WEB_URL="http://localhost:5173"
mkdir -p "$LOG_DIR"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }

running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

stop_pid() {
  local file="$1" name="$2"
  if running "$file"; then
    local pid; pid="$(cat "$file")"
    pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -KILL "$pid" 2>/dev/null
    ok "$name stopped"
  else
    ok "$name was not running"
  fi
  rm -f "$file"
}

case "${1:-}" in
  --stop)
    say "Stopping AURUM AI"
    stop_pid "$WEB_PID" "Dashboard"
    stop_pid "$API_PID" "API"
    (cd "$AURUM_DIR" && docker compose --env-file .env stop >/dev/null 2>&1) && ok "Database stopped (data is kept)"
    exit 0 ;;
  --status)
    running "$API_PID" && ok "API running ($API_URL)" || warn "API not running"
    running "$WEB_PID" && ok "Dashboard running ($WEB_URL)" || warn "Dashboard not running"
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^aurum-timescaledb$' && ok "Database running" \
      || warn "Database not running"
    exit 0 ;;
  ""|--start) ;;
  *) echo "Usage: $0 [--start|--stop|--status]"; exit 2 ;;
esac

say "Starting AURUM AI from $REPO_ROOT"

# ---- 0. tools ----
# Docker Desktop puts its command in one of these folders; a new Terminal may not search them yet.
export PATH="$PATH:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin"
if ! command -v docker >/dev/null 2>&1; then
  if [ -d /Applications/Docker.app ]; then
    warn "Docker Desktop is installed but has not finished its first start; opening it…"
    open -a Docker 2>/dev/null || true
    for _ in $(seq 1 60); do command -v docker >/dev/null 2>&1 && break; sleep 2; done
  fi
  command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/ (then open it once)"
fi
if ! docker info >/dev/null 2>&1; then
  warn "Docker Desktop is not running; opening it…"
  open -a Docker 2>/dev/null || true
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || die "Docker did not start. Open Docker Desktop, wait for it to say Running, then try again."
fi
ok "Docker running"

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "Python 3.11 or newer is needed. Install it with:  brew install python@3.12"
command -v npm >/dev/null 2>&1 || die "Node.js is not installed. Install it with:  brew install node"

# ---- 1. settings + database ----
cd "$AURUM_DIR" || exit 1
if [ ! -f .env ]; then
  cp .env.example .env
  # A random local database password, kept only in this Mac's .env (never committed).
  DBPW="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
  sed -i '' "s/^DB_PASSWORD=.*/DB_PASSWORD=$DBPW/" .env
  ok "Created AURUM_AI/.env with a random local database password"
fi
docker compose --env-file .env up -d >/dev/null 2>&1 || die "Could not start the database (run: docker compose --env-file .env up -d)"
for _ in $(seq 1 45); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' aurum-timescaledb 2>/dev/null)" = "healthy" ] && break; sleep 2
done
[ "$(docker inspect -f '{{.State.Health.Status}}' aurum-timescaledb 2>/dev/null)" = "healthy" ] \
  || die "The database did not become ready. See: docker logs aurum-timescaledb"
ok "Database ready (localhost:5432)"

# ---- 2. Python environment + tables ----
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  rm -rf .venv
  "$PY" -m venv .venv || die "Could not create .venv"
  ok "Created .venv with $($PY --version)"
fi
REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.req_hash 2>/dev/null)" != "$REQ_HASH" ]; then
  say "Installing Python packages (first time takes a few minutes)…"
  .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt \
    || die "pip install failed (see the output above)"
  echo "$REQ_HASH" > .venv/.req_hash
fi
ok "Python packages installed"
.venv/bin/python -m aurum.cli init-db >"$LOG_DIR/init_db.txt" 2>&1 || die "Creating the tables failed; see AURUM_AI/logs/init_db.txt"
ok "Database tables up to date"

# ---- 3. API ----
if running "$API_PID"; then
  ok "API already running"
else
  nohup .venv/bin/python -m aurum.api >"$LOG_DIR/api.txt" 2>&1 &
  echo $! > "$API_PID"
  for _ in $(seq 1 40); do curl -sf "$API_URL/api/v1/health" >/dev/null 2>&1 && break; sleep 1; done
  curl -sf "$API_URL/api/v1/health" >/dev/null 2>&1 || die "The API did not start; see AURUM_AI/logs/api.txt"
  ok "API running"
fi

# ---- 4. dashboard ----
cd "$FRONTEND_DIR" || die "aurum-frontend folder not found"
[ -f .env ] || cp .env.example .env
PKG_HASH="$(cat package.json package-lock.json 2>/dev/null | shasum | cut -d' ' -f1)"
if [ ! -d node_modules ] || [ "$(cat node_modules/.pkg_hash 2>/dev/null)" != "$PKG_HASH" ]; then
  say "Installing dashboard packages…"
  npm install --no-fund --no-audit --silent || die "npm install failed"
  echo "$PKG_HASH" > node_modules/.pkg_hash
fi
if running "$WEB_PID"; then
  ok "Dashboard already running"
else
  nohup ./node_modules/.bin/vite --port 5173 --strictPort >"$LOG_DIR/frontend.txt" 2>&1 &
  echo $! > "$WEB_PID"
  for _ in $(seq 1 40); do curl -sf "$WEB_URL" >/dev/null 2>&1 && break; sleep 1; done
  curl -sf "$WEB_URL" >/dev/null 2>&1 || die "The dashboard did not start; see AURUM_AI/logs/frontend.txt"
  ok "Dashboard running"
fi

echo
say "AURUM AI is running"
echo "  Dashboard   $WEB_URL"
echo "  API docs    $API_URL/docs"
echo "  API health  $API_URL/api/v1/health"
echo "  Stop        aurum-stop   (or: bash $SCRIPT_DIR/start_mac.sh --stop)"
if ! (cd "$AURUM_DIR" && .venv/bin/python -m aurum.cli users list 2>/dev/null | grep -qiE 'admin'); then
  echo
  warn "No admin user yet. Create one (it asks for a password):"
  echo "      cd $AURUM_DIR && .venv/bin/python -m aurum.cli users create <your-name> --role admin"
fi
open "$WEB_URL" 2>/dev/null || true
open "$API_URL/docs" 2>/dev/null || true
