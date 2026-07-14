#!/usr/bin/env bash
# Start full app (frontend + backend) locally and expose a free public HTTPS URL.
# Requires: cloudflared (brew install cloudflared)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
export MAX_JOBS_PER_RUN="${MAX_JOBS_PER_RUN:-5}"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Creating venv and installing deps..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install with: brew install cloudflared"
  exit 1
fi

# Kill previous listeners on this port (best-effort)
lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
pkill -f "cloudflared tunnel --url http://127.0.0.1:${PORT}" 2>/dev/null || true
sleep 1

LOG_DIR="$ROOT/.tunnel-logs"
mkdir -p "$LOG_DIR"
UV_LOG="$LOG_DIR/uvicorn.log"
CF_LOG="$LOG_DIR/cloudflared.log"
URL_FILE="$LOG_DIR/public-url.txt"

echo "Starting FastAPI (UI + API) on :$PORT ..."
nohup .venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port "$PORT" \
  >"$UV_LOG" 2>&1 &
echo $! > "$LOG_DIR/uvicorn.pid"

for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
    break
  fi
  sleep 0.5
done

if ! curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
  echo "App failed to start. See $UV_LOG"
  exit 1
fi

echo "Starting Cloudflare free tunnel..."
nohup cloudflared tunnel --url "http://127.0.0.1:${PORT}" >"$CF_LOG" 2>&1 &
echo $! > "$LOG_DIR/cloudflared.pid"

PUBLIC_URL=""
for i in $(seq 1 40); do
  PUBLIC_URL="$(grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$CF_LOG" | tail -1 || true)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 0.5
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Tunnel URL not found yet. Tail $CF_LOG"
  exit 1
fi

echo "$PUBLIC_URL" > "$URL_FILE"

echo ""
echo "============================================"
echo "  FULL APP IS LIVE (frontend + backend)"
echo "============================================"
echo "  Local:  http://127.0.0.1:${PORT}"
echo "  Public: $PUBLIC_URL"
echo "  Health: $PUBLIC_URL/health"
echo "  Demo:   $PUBLIC_URL/"
echo "============================================"
echo "Keep this terminal session / machine awake."
echo "Stop with:  scripts/stop-public.sh"
echo ""
