#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
export BACKEND_PORT=${BACKEND_PORT:-8000}
export FRONTEND_PORT=${FRONTEND_PORT:-5173}

# Load KEY=VALUE lines from .env without evaluating shell syntax.
load_env_file() {
  env_file="$1"
  [ -f "$env_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|\#*) continue
        ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    export "$key=$val"
  done < "$env_file"
}

load_env_file "$ROOT_DIR/.env"

echo "Starting backend and frontend"

if [ -f "$ROOT_DIR/backend/.venv/bin/activate" ]; then
  # Use venv python to run uvicorn as a module to ensure virtualenv packages are used
  . "$ROOT_DIR/backend/.venv/bin/activate"
fi

PYTHON="$ROOT_DIR/backend/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Virtualenv python not found at $PYTHON. Did you run ./build.sh?" >&2
  exit 1
fi

binary_supports_arm64() {
  file "$1" 2>/dev/null | grep -qi 'arm64'
}

PYTHON_CMD=("$PYTHON")
NPM_CMD=(npm)

# In Rosetta/x86 shells on Apple Silicon, prefer arm64 execution only when
# the target binary actually supports arm64.
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ] && command -v arch >/dev/null 2>&1 && arch -arm64 /usr/bin/true >/dev/null 2>&1; then
  if binary_supports_arm64 "$PYTHON"; then
    PYTHON_CMD=(arch -arm64 "$PYTHON")
  fi
  if command -v node >/dev/null 2>&1 && binary_supports_arm64 "$(command -v node)"; then
    NPM_CMD=(arch -arm64 npm)
  fi
fi

echo "Starting backend (uvicorn) using ${PYTHON_CMD[*]}"
"${PYTHON_CMD[@]}" -m uvicorn backend.main:app --host 0.0.0.0 --port $BACKEND_PORT &
BACKEND_PID=$!

# Wait for backend health before starting frontend. This avoids launching a UI
# that can only show "backend unreachable" when backend failed to start.
echo "Waiting for backend health..."
backend_ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "Backend process exited before becoming healthy." >&2
    wait $BACKEND_PID 2>/dev/null || true
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
      backend_ready=1
      break
    fi
  fi
  sleep 1
done

if [ "$backend_ready" -ne 1 ]; then
  echo "Backend health check timed out at http://localhost:$BACKEND_PORT/api/health" >&2
  kill $BACKEND_PID 2>/dev/null || true
  wait $BACKEND_PID 2>/dev/null || true
  exit 1
fi

cd "$ROOT_DIR/frontend" || exit 1
if [ "${OMNISPRINT_FRONTEND_MODE:-dev}" = "preview" ] && { [ -d "$ROOT_DIR/frontend/dist" ] || [ -d "$ROOT_DIR/frontend/build" ]; }; then
  echo "Starting frontend in preview mode"
  "${NPM_CMD[@]}" run preview -- --port $FRONTEND_PORT &
else
  echo "Starting frontend in dev mode"
  "${NPM_CMD[@]}" run dev -- --port $FRONTEND_PORT &
fi
FRONTEND_PID=$!
cd - >/dev/null

echo "Frontend: http://localhost:$FRONTEND_PORT"
echo "Backend: http://localhost:$BACKEND_PORT"
echo "Health: http://localhost:$BACKEND_PORT/api/health"

cleanup() {
  echo "Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  # Give processes a brief grace period, then force-stop to avoid hanging shutdowns.
  for _ in 1 2 3 4 5; do
    sleep 0.3
    kill -0 $BACKEND_PID 2>/dev/null || break
  done
  if kill -0 $BACKEND_PID 2>/dev/null; then
    kill -9 $BACKEND_PID 2>/dev/null || true
  fi
  if kill -0 $FRONTEND_PID 2>/dev/null; then
    kill -9 $FRONTEND_PID 2>/dev/null || true
  fi
  wait $BACKEND_PID 2>/dev/null || true
  wait $FRONTEND_PID 2>/dev/null || true
  exit 0
}

trap cleanup INT TERM

wait $BACKEND_PID $FRONTEND_PID
