#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Building Sprint Tracker in $ROOT_DIR"

if ! command -v coral >/dev/null 2>&1; then
  echo "Coral CLI not found. Install with: brew install withcoral/coral/coral" >&2
  exit 1
fi

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

# Return 0 if a Mach-O binary advertises an arm64 slice.
binary_supports_arm64() {
  file "$1" 2>/dev/null | grep -qi 'arm64'
}

# Ensure .env exists
if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "Creating .env from .env.example"
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

# Export .env variables for registration steps
load_env_file "$ROOT_DIR/.env"

# Create or repair virtualenv.
if [ ! -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  echo "Creating or repairing Python virtualenv..."
  if [ -d "$ROOT_DIR/backend/.venv" ]; then
    rm -rf "$ROOT_DIR/backend/.venv" 2>/dev/null || true
  fi
  if [ -d "$ROOT_DIR/backend/.venv" ]; then
    # Fallback if the directory could not be removed cleanly.
    python3 -m venv --clear "$ROOT_DIR/backend/.venv"
  else
    python3 -m venv "$ROOT_DIR/backend/.venv"
  fi
fi

PYTHON="$ROOT_DIR/backend/.venv/bin/python"
PIP="$ROOT_DIR/backend/.venv/bin/pip"

PYTHON_CMD=("$PYTHON")
PIP_CMD=("$PIP")
NPM_CMD=(npm)

# In Rosetta/x86 shells on Apple Silicon, prefer arm64 execution only when
# the target binary actually supports arm64.
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ] && command -v arch >/dev/null 2>&1 && arch -arm64 /usr/bin/true >/dev/null 2>&1; then
  if binary_supports_arm64 "$PYTHON"; then
    PYTHON_CMD=(arch -arm64 "$PYTHON")
    PIP_CMD=(arch -arm64 "$PIP")
  fi
  if command -v node >/dev/null 2>&1 && binary_supports_arm64 "$(command -v node)"; then
    NPM_CMD=(arch -arm64 npm)
  fi
fi

echo "Upgrading pip and installing backend requirements..."
"${PYTHON_CMD[@]}" -m pip install --upgrade pip
"${PIP_CMD[@]}" install -r "$ROOT_DIR/backend/requirements.txt"

echo "Installing frontend dependencies (npm)..."
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Please install Node.js and npm." >&2
  exit 1
fi

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  "${NPM_CMD[@]}" --prefix "$ROOT_DIR/frontend" install
else
  echo "Frontend node_modules already present"
fi

echo "Registering Coral sources (best-effort)..."
chmod +x "$ROOT_DIR/scripts/register_coral_sources.sh" || true
"$ROOT_DIR/scripts/register_coral_sources.sh" || echo "Coral source registration completed with warnings"

echo "Checking Python code syntax..."
"${PYTHON_CMD[@]}" -m py_compile "$ROOT_DIR/backend"/*.py

echo "Building frontend..."
"${NPM_CMD[@]}" --prefix "$ROOT_DIR/frontend" run build

echo "Verifying backend imports..."
"${PYTHON_CMD[@]}" - <<PY
import importlib,sys
mods = ['fastapi','uvicorn','pydantic','requests']
errs=[]
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        errs.append((m,str(e)))
if errs:
    print('Import errors:')
    for m,e in errs:
        print(m, e)
    sys.exit(2)
print('Backend imports ok')
PY

echo "Build finished successfully. You can now run ./run.sh or make run"
