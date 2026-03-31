#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_PORT="${BACKEND_PORT:-5000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

BACKEND_PID=""

cleanup() {
  local exit_code=$?

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo "Stopping backend..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi

  exit "$exit_code"
}

trap cleanup EXIT INT TERM

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
}

port_in_use() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sys.exit(0)
    sys.exit(1)
PY
}

resolve_available_port() {
  local requested_port="$1"
  local label="$2"
  local port="$requested_port"

  while port_in_use "$port"; do
    if [[ "$port" == "$requested_port" ]]; then
      echo "Port $port is already in use, searching for the next available $label port..." >&2
    fi
    port=$((port + 1))
  done

  echo "$port"
}

echo "===================================="
echo "Banana Slides local startup"
echo "===================================="

require_cmd uv
require_cmd npm
require_cmd python3

cd "$ROOT_DIR"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Creating .env from .env.example ..."
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Please review .env and fill in your API credentials before using AI features."
fi

echo ""
echo "[1/4] Sync Python dependencies..."
uv sync

echo ""
echo "[2/4] Install frontend dependencies..."
if [[ -f "$FRONTEND_DIR/package-lock.json" ]]; then
  npm --prefix "$FRONTEND_DIR" ci
else
  npm --prefix "$FRONTEND_DIR" install
fi

echo ""
echo "[3/4] Run backend migrations..."
(
  cd "$BACKEND_DIR"
  uv run alembic upgrade head
)

BACKEND_PORT="$(resolve_available_port "$BACKEND_PORT" "backend")"
FRONTEND_PORT="$(resolve_available_port "$FRONTEND_PORT" "frontend")"

echo ""
echo "[4/4] Start backend and frontend..."
(
  cd "$BACKEND_DIR"
  BACKEND_PORT="$BACKEND_PORT" FLASK_ENV=development FLASK_USE_RELOADER=1 uv run python app.py
) &
BACKEND_PID=$!

echo "Backend starting at http://localhost:$BACKEND_PORT"
echo "Frontend starting at http://localhost:$FRONTEND_PORT"
echo "Backend auto-reload: enabled"
echo "Frontend hot reload: enabled"
echo "Press Ctrl+C to stop both services."
echo ""

cd "$FRONTEND_DIR"
BACKEND_PORT="$BACKEND_PORT" FRONTEND_PORT="$FRONTEND_PORT" npm run dev
