#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.tunnel-logs"

if [[ -f "$LOG_DIR/cloudflared.pid" ]]; then
  kill "$(cat "$LOG_DIR/cloudflared.pid")" 2>/dev/null || true
  rm -f "$LOG_DIR/cloudflared.pid"
fi
if [[ -f "$LOG_DIR/uvicorn.pid" ]]; then
  kill "$(cat "$LOG_DIR/uvicorn.pid")" 2>/dev/null || true
  rm -f "$LOG_DIR/uvicorn.pid"
fi
pkill -f "cloudflared tunnel --url http://127.0.0.1" 2>/dev/null || true
lsof -ti tcp:8000 | xargs kill -9 2>/dev/null || true
echo "Stopped public tunnel + app."
