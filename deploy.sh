#!/usr/bin/env bash
# deploy.sh — one-command deploy for the whole aiops stack.
#
#   bash deploy.sh           # install (if needed) + start everything
#   bash deploy.sh --start   # skip install, just (re)start services
#   bash deploy.sh --update  # after `git pull`: refresh deps + frontend, restart
#   bash deploy.sh --stop    # stop all services started by this script
#   bash deploy.sh --status  # show health of every service
#
# Services started:
#   SearXNG (docker, :4000)  log-ml/A1-IF (:3050)  Perplexica/Vane (:3001)
#   aiops backend (:8200)    aiops frontend (:3002)
#
# Env overrides: OLLAMA_BASE_URL, PYTHON, PORT_* (see below).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Make nvm-installed node available in non-login shells.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1090
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true

# sudo helper — empty if already root, else "sudo" when available.
if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo >/dev/null; then SUDO="sudo"; else SUDO=""; fi
# Real invoking user (under `sudo` $USER is root — use $SUDO_USER instead).
REAL_USER="${SUDO_USER:-${USER:-$(id -un)}}"

# Add the real user to the docker group so future runs don't need sudo. Takes
# effect on next login only — the current run keeps using `sudo docker`.
add_to_docker_group() {
  [ "$REAL_USER" = "root" ] && return
  id -nG "$REAL_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker && return  # already a member
  if $SUDO usermod -aG docker "$REAL_USER" 2>/dev/null; then
    warn "added '$REAL_USER' to the 'docker' group — log out/in (or run: newgrp docker)"
    warn "after that, run deploy.sh WITHOUT sudo"
  fi
}
# docker may need sudo until the user's docker-group membership takes effect.
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="$SUDO docker"; fi

# ── Ports / config ──────────────────────────────────────────────────────────
PORT_BACKEND="${PORT_BACKEND:-8200}"
PORT_LOG_ML="${PORT_LOG_ML:-3050}"
PORT_VANE="${PORT_VANE:-3001}"
PORT_UI="${PORT_UI:-3002}"
PORT_SEARXNG="${PORT_SEARXNG:-4000}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

LOG_DIR="$SCRIPT_DIR/logs"
RUN_DIR="$SCRIPT_DIR/.run"
VENV="$SCRIPT_DIR/.venv"
PYTHON="${PYTHON:-$(command -v python3.14 || command -v python3 || true)}"
# Use venv python if already set up (subsequent --start / --status runs)
[[ -x "$VENV/bin/python" ]] && PYTHON="$VENV/bin/python"

mkdir -p "$LOG_DIR" "$RUN_DIR"

c_info='\033[1;36m'; c_ok='\033[1;32m'; c_warn='\033[1;33m'; c_err='\033[1;31m'; c_off='\033[0m'
log()  { printf "${c_info}[deploy]${c_off} %s\n" "$*"; }
ok()   { printf "${c_ok}[deploy]${c_off} %s\n" "$*"; }
warn() { printf "${c_warn}[deploy]${c_off} %s\n" "$*"; }
die()  { printf "${c_err}[deploy] ERROR:${c_off} %s\n" "$*" >&2; exit 1; }

port_up() {
  # lsof can't see ports owned by other users (e.g. root docker-proxy); fall back to ss.
  lsof -i ":$1" -sTCP:LISTEN -t >/dev/null 2>&1 && return 0
  ss -ltn 2>/dev/null | grep -qE "[:.]$1[[:space:]]"
}

# Kill whatever is listening on a port — including untracked processes (a
# manually-started `next dev`, a stale run) that our pidfiles don't know about.
kill_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then $SUDO fuser -k "${port}/tcp" >/dev/null 2>&1 || true; fi
  local pids; pids="$(lsof -ti ":$port" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
}

wait_health() { # url, name, tries
  local url="$1" name="$2" tries="${3:-30}"
  for _ in $(seq 1 "$tries"); do
    if curl -s -o /dev/null --max-time 3 "$url"; then ok "$name is up"; return 0; fi
    sleep 1
  done
  warn "$name did not answer at $url yet (check $LOG_DIR)"
}

# Start a backgrounded service unless its port is already listening.
# start_svc <name> <port> <logfile> <command...>
start_svc() {
  local name="$1" port="$2" logf="$3"; shift 3
  if port_up "$port"; then ok "$name already running on :$port"; return; fi
  log "starting $name on :$port"
  nohup "$@" >"$LOG_DIR/$logf" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
}

# ── Prerequisite checks (auto-install what's missing, no manual steps) ───────
# Whether SearXNG (Docker) is usable. Set false when Docker can't be provided.
SEARXNG_ENABLED=1

ensure_python() {
  [ -n "$PYTHON" ] && command -v "$PYTHON" >/dev/null || die "python3 not found (need 3.11+)"
  # venv must be able to bootstrap pip (Debian splits this into python3-venv).
  if "$PYTHON" -Im ensurepip --version >/dev/null 2>&1; then return; fi
  warn "python venv/pip missing — installing python3-venv"
  if [ -n "$SUDO$([ "$(id -u)" -eq 0 ] && echo root)" ] && command -v apt-get >/dev/null; then
    local pyver; pyver="$("$PYTHON" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "python${pyver}-venv" python3-pip \
      || warn "apt install python venv failed — will fall back to get-pip.py"
  fi
}

ensure_node() {
  local nmaj=0
  command -v node >/dev/null && nmaj="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
  if [ "$nmaj" -ge 20 ]; then return; fi
  warn "Node >=20 not found (have: ${nmaj:-none}) — installing Node 22 via nvm"
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    curl -fsSL -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash >/dev/null 2>&1 \
      || die "nvm install failed (no network?)"
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"
  fi
  nvm install 22 >/dev/null 2>&1 && nvm alias default 22 >/dev/null 2>&1
  nvm use 22 >/dev/null 2>&1
  command -v node >/dev/null || die "node still unavailable after nvm install"
}

ensure_docker() {
  if [ "${DOCKER%% *}" = "docker" ] && docker info >/dev/null 2>&1; then return; fi
  if ! command -v docker >/dev/null; then
    if command -v apt-get >/dev/null && { [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; }; then
      warn "docker not installed — installing docker.io"
      $SUDO apt-get update -qq && $SUDO apt-get install -y -qq docker.io \
        && $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
      add_to_docker_group
    fi
  fi
  if ! command -v docker >/dev/null; then
    warn "docker unavailable — SearXNG/A2 external search will be SKIPPED"; SEARXNG_ENABLED=0; return
  fi
  # Daemon up? Try to start it; then decide whether plain docker or sudo docker works.
  docker info >/dev/null 2>&1 || $SUDO systemctl start docker >/dev/null 2>&1 || true
  if docker info >/dev/null 2>&1; then DOCKER="docker"
  elif [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then
    DOCKER="$SUDO docker"; warn "using 'sudo docker' for this run"
    add_to_docker_group   # so the next run won't need sudo (after re-login)
  else
    warn "docker daemon not reachable — SearXNG/A2 external search will be SKIPPED"; SEARXNG_ENABLED=0
  fi
}

check_prereqs() {
  ensure_python
  ensure_node
  ensure_docker
  ok "prereqs OK — $("$PYTHON" --version 2>&1), node $(node -v 2>&1), docker: $([ "$SEARXNG_ENABLED" = 1 ] && echo "$DOCKER" || echo skipped)"
}

# ── Install ─────────────────────────────────────────────────────────────────
install_all() {
  log "[1/4] Python dependencies (backend + log-ml)"
  if [[ ! -x "$VENV/bin/python" ]]; then
    if "$PYTHON" -Im ensurepip --version >/dev/null 2>&1; then
      "$PYTHON" -m venv "$VENV"
    else
      # No ensurepip (couldn't apt-install) — make a pip-less venv and bootstrap pip.
      "$PYTHON" -m venv --without-pip "$VENV"
      curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python" - \
        || die "failed to bootstrap pip into $VENV"
    fi
    log "  created virtualenv at $VENV"
  fi
  PYTHON="$VENV/bin/python"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r requirements.txt
  [ -f log-ml/requirements.txt ] && "$VENV/bin/pip" install -q -r log-ml/requirements.txt

  log "[2/4] Perplexica (Vane) — clone + build (Node $(node -v))"
  if [ ! -d perplexica-src ]; then
    git clone https://github.com/sumate001/Vane perplexica-src
    ( cd perplexica-src && npm install --legacy-peer-deps --engine-strict=false && npm run build )
  else
    ok "perplexica-src exists — skipping clone/build (delete it to rebuild)"
  fi
  if [ ! -f perplexica-src/data/config.json ] && [ -f perplexica-src/data/config.json.example ]; then
    cp perplexica-src/data/config.json.example perplexica-src/data/config.json
  fi

  log "[3/4] Frontend dependencies + production build"
  [ -d frontend/node_modules ] || ( cd frontend && npm install )
  # Build for production so the UI runs via `next start` (stable, no dev HMR /
  # recompile loops, no allowedDevOrigins host restriction on remote machines).
  ( cd frontend && npm run build )

  log "[4/4] config.yaml"
  if [ ! -f config.yaml ] && [ -f config.yaml.example ]; then
    cp config.yaml.example config.yaml
    warn "created config.yaml from example — set ollama.base_url / perplexica before heavy use"
  fi
  ok "install complete"
}

# ── Start ───────────────────────────────────────────────────────────────────
start_all() {
  # SearXNG (docker) — skipped gracefully when Docker isn't available.
  if [ "${SEARXNG_ENABLED:-1}" != 1 ]; then
    warn "SearXNG skipped (no Docker) — A2 external search disabled, rest of stack runs"
  elif $DOCKER ps --format '{{.Names}}' | grep -q '^aiops-searxng$'; then
    ok "SearXNG already running"
  elif $DOCKER ps -a --format '{{.Names}}' | grep -q '^aiops-searxng$'; then
    log "starting existing SearXNG container"; $DOCKER start aiops-searxng >/dev/null
  else
    log "creating SearXNG container on :$PORT_SEARXNG"
    $DOCKER run -d --name aiops-searxng -p "${PORT_SEARXNG}:8080" \
      -e SEARXNG_SECRET="$(openssl rand -hex 32)" searxng/searxng:latest >/dev/null
    # Perplexica queries SearXNG with format=json, which the default settings
    # disable (→ 403, zero sources). Enable it, then restart to apply.
    sleep 4
    $DOCKER exec aiops-searxng sh -c \
      "grep -q '^search:' /etc/searxng/settings.yml || printf '\nsearch:\n  formats:\n    - html\n    - json\n' >> /etc/searxng/settings.yml" \
      && $DOCKER restart aiops-searxng >/dev/null && ok "SearXNG json format enabled"
  fi

  start_svc log-ml "$PORT_LOG_ML" log-ml.log \
    "$PYTHON" -m uvicorn app.main:app --app-dir log-ml --host 0.0.0.0 --port "$PORT_LOG_ML"

  start_svc perplexica "$PORT_VANE" perplexica.log \
    env PORT="$PORT_VANE" SEARXNG_API_URL="http://localhost:$PORT_SEARXNG" \
        OLLAMA_BASE_URL="$OLLAMA_BASE_URL" \
        DATA_DIR="$SCRIPT_DIR/perplexica-src" \
        node "$SCRIPT_DIR/perplexica-src/.next/standalone/server.js"

  start_svc backend "$PORT_BACKEND" backend.log \
    "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT_BACKEND"

  # Production server (next start). Rebuild first if there's no build output yet
  # (e.g. after a fresh `--start` without a prior install).
  [ -d frontend/.next ] || ( cd frontend && npm run build )
  start_svc frontend "$PORT_UI" frontend.log \
    env PORT="$PORT_UI" npm --prefix frontend run start

  log "waiting for services…"
  wait_health "http://localhost:$PORT_LOG_ML/healthz"        "log-ml"
  [ "${SEARXNG_ENABLED:-1}" = 1 ] && wait_health "http://localhost:$PORT_SEARXNG/healthz" "SearXNG"
  wait_health "http://localhost:$PORT_VANE/api/providers"    "Perplexica"
  wait_health "http://localhost:$PORT_BACKEND/healthz"       "backend"
  wait_health "http://localhost:$PORT_UI"                    "frontend"
  status_all
}

# ── Stop ────────────────────────────────────────────────────────────────────
stop_all() {
  for name in frontend backend perplexica log-ml; do
    local pidf="$RUN_DIR/$name.pid"
    if [ -f "$pidf" ]; then
      local pid; pid="$(cat "$pidf")"
      if kill "$pid" 2>/dev/null; then ok "stopped $name (pid $pid)"; else warn "$name not running"; fi
      rm -f "$pidf"
    fi
  done
  # Belt-and-suspenders: free any port still held by an untracked process so the
  # next start_all actually (re)launches fresh code instead of skipping it.
  for entry in "frontend:$PORT_UI" "backend:$PORT_BACKEND" "perplexica:$PORT_VANE" "log-ml:$PORT_LOG_ML"; do
    local p="${entry##*:}"
    if port_up "$p"; then kill_port "$p"; sleep 1; port_up "$p" || ok "freed :$p (${entry%%:*})"; fi
  done
  if command -v docker >/dev/null && $DOCKER ps --format '{{.Names}}' 2>/dev/null | grep -q '^aiops-searxng$'; then
    $DOCKER stop aiops-searxng >/dev/null && ok "stopped SearXNG"
  fi
}

# ── Update ──────────────────────────────────────────────────────────────────
# Run after `git pull`: reinstall Python deps, rebuild the frontend, then
# restart everything. Does NOT touch perplexica-src/ (Vane is a separate repo)
# or config.yaml / the SQLite DB.
update_all() {
  [[ -x "$VENV/bin/python" ]] || die "no virtualenv at $VENV — run 'bash deploy.sh' first"
  PYTHON="$VENV/bin/python"

  # Stop first (frees ports incl. untracked/dev processes) so the rebuild writes
  # into a quiesced tree and the restart launches the fresh build.
  log "[1/4] stopping services"
  stop_all

  log "[2/4] refreshing Python dependencies"
  "$VENV/bin/pip" install -q -r requirements.txt
  [ -f log-ml/requirements.txt ] && "$VENV/bin/pip" install -q -r log-ml/requirements.txt

  # Wipe the previous build — stale hashed chunks left behind make the running
  # server reference JS files that 404, so the page never hydrates (all panels
  # render their default "down" state and never fetch /api/status).
  log "[3/4] clean rebuild of frontend"
  rm -rf frontend/.next
  ( cd frontend && npm install && npm run build )

  log "[4/4] starting services"
  check_prereqs
  start_all
}

# ── Status ──────────────────────────────────────────────────────────────────
status_all() {
  printf "\n${c_info}=== Service status ===${c_off}\n"
  for entry in "backend:$PORT_BACKEND" "frontend:$PORT_UI" "perplexica:$PORT_VANE" \
               "log-ml:$PORT_LOG_ML" "searxng:$PORT_SEARXNG"; do
    local name="${entry%%:*}" port="${entry##*:}"
    if port_up "$port"; then printf "  ${c_ok}● UP  ${c_off} %-11s :%s\n" "$name" "$port"
    else printf "  ${c_err}○ down${c_off} %-11s :%s\n" "$name" "$port"; fi
  done
  printf "\nOpen:  ${c_ok}http://localhost:%s${c_off} (dashboard)   logs: %s/\n\n" "$PORT_UI" "$LOG_DIR"
}

# ── Entrypoint ──────────────────────────────────────────────────────────────
case "${1:-}" in
  --stop)   stop_all ;;
  --status) status_all ;;
  --start)  check_prereqs; start_all ;;
  --update) update_all ;;
  ""|--all) check_prereqs; install_all; start_all ;;
  *) die "unknown option '$1' (use: --start | --update | --stop | --status | --all)" ;;
esac
