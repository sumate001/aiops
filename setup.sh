#!/usr/bin/env bash
# setup.sh — install and start all aiops services
# Run once after cloning: bash setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-$(command -v python3.14 || command -v python3)}"
NODE_PORT_VANE=3001
NODE_PORT_UI=3002
PORT_BACKEND=8200
PORT_LOG_ML=3050
PORT_SEARXNG=4000

echo "=== [1/5] Python dependencies ==="
"$PYTHON" -m pip install -r requirements.txt -q

echo "=== [2/5] Perplexica (Vane) ==="
# Vane builds with the Next.js standalone output and requires Node >= 18
# (Node 22 recommended). Older Node (e.g. 15) fails the build with
# "Cannot find module '../server/require-hook'" / "Cannot find module 'node:crypto'".
NODE_MAJOR="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
if [ -z "$NODE_MAJOR" ]; then
  echo "  ERROR: node not found. Install Node 22 (e.g. nvm install 22 && nvm use 22)." >&2
  exit 1
elif [ "$NODE_MAJOR" -lt 18 ]; then
  echo "  ERROR: Node $NODE_MAJOR detected — Vane build needs Node >= 18 (22 recommended)." >&2
  echo "         Run 'nvm use 22' (or install it) before re-running setup.sh." >&2
  exit 1
fi
echo "  using Node $(node -v)"

if [ ! -d perplexica-src ]; then
  git clone https://github.com/sumate001/Vane perplexica-src
  cd perplexica-src
  npm install
  npm run build
  cd ..
else
  echo "  perplexica-src already exists, skipping clone"
fi

# copy data/config.json if not present
if [ ! -f perplexica-src/data/config.json ] && [ -f perplexica-src/data/config.json.example ]; then
  cp perplexica-src/data/config.json.example perplexica-src/data/config.json
  echo "  created perplexica-src/data/config.json from example"
fi

echo "=== [3/5] SearXNG (Docker) ==="
if docker ps --format '{{.Names}}' | grep -q "aiops-searxng"; then
  echo "  aiops-searxng already running"
elif docker ps -a --format '{{.Names}}' | grep -q "aiops-searxng"; then
  docker start aiops-searxng
else
  docker run -d --name aiops-searxng \
    -p ${PORT_SEARXNG}:8080 \
    -e SEARXNG_SECRET="$(openssl rand -hex 32)" \
    searxng/searxng:latest
fi

echo "=== [4/5] Frontend (aiops UI) ==="
cd frontend
if [ ! -d node_modules ]; then npm install; fi
cd ..

echo "=== [5/5] Config ==="
if [ ! -f config.yaml ] && [ -f config.yaml.example ]; then
  cp config.yaml.example config.yaml
  echo "  created config.yaml from example — edit before starting"
fi

echo ""
echo "Done! Start services with:"
echo ""
echo "  # Backend (port $PORT_BACKEND)"
echo "  uvicorn app.main:app --host 0.0.0.0 --port $PORT_BACKEND"
echo ""
echo "  # log-ml — Isolation Forest (port $PORT_LOG_ML)"
echo "  cd log-ml && uvicorn app.main:app --host 0.0.0.0 --port $PORT_LOG_ML"
echo ""
echo "  # Perplexica / Vane (port $NODE_PORT_VANE)"
echo "  cd perplexica-src/.next/standalone   # needs Node >= 18"
echo "  PORT=$NODE_PORT_VANE SEARXNG_API_URL=http://localhost:$PORT_SEARXNG OLLAMA_BASE_URL=http://localhost:11434 DATA_DIR=\$(pwd)/../.. node server.js"
echo ""
echo "  # aiops frontend (port $NODE_PORT_UI)"
echo "  cd frontend && PORT=$NODE_PORT_UI npm run dev"
