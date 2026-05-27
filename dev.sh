#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# If venv python or node_modules missing, run build
if [ ! -x "$ROOT_DIR/backend/.venv/bin/python" ] || [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  echo "Dependencies missing; running build.sh"
  "$ROOT_DIR/build.sh"
fi

exec "$ROOT_DIR/run.sh"
