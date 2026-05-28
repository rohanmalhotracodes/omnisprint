#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Running environment checks for OmniSprint"

errors=0

binary_supports_arm64() {
  file "$1" 2>/dev/null | grep -qi 'arm64'
}

check() { if ! eval "$1"; then echo "ERROR: $2"; errors=$((errors+1)); else echo "OK: $3"; fi }

echo "Checking Coral CLI..."
if command -v coral >/dev/null 2>&1; then
  echo "Coral found: $(coral --version 2>/dev/null || true)"
else
  echo "ERROR: Coral CLI not found. Install: brew install withcoral/coral/coral"
  errors=$((errors+1))
fi

echo "Checking backend virtualenv and Python packages..."
if [ -d "$ROOT_DIR/backend/.venv" ]; then
  PYTHON="$ROOT_DIR/backend/.venv/bin/python"
  if [ -x "$PYTHON" ]; then
    echo "Using venv python: $PYTHON"
    PYTHON_CMD=("$PYTHON")
    # In Rosetta/x86 shells on Apple Silicon, prefer arm64 execution only
    # when the venv python binary actually supports arm64.
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ] && command -v arch >/dev/null 2>&1 && arch -arm64 /usr/bin/true >/dev/null 2>&1; then
      if binary_supports_arm64 "$PYTHON"; then
        PYTHON_CMD=(arch -arm64 "$PYTHON")
      fi
    fi
    # Check imports with explicit failure handling.
    if "${PYTHON_CMD[@]}" - <<'PY'
import importlib
import sys

mods = ["fastapi", "uvicorn", "pydantic", "requests"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        missing.append((m, f"{type(e).__name__}: {e}"))

if missing:
    print("MISSING:" + ",".join(m for m, _ in missing))
    for m, err in missing:
        print(f"  - {m}: {err}")
    sys.exit(2)

print("BACKEND_IMPORTS_OK")
PY
    then
      echo "OK: backend imports available"
    else
      echo "ERROR: missing backend imports (run ./build.sh)"
      errors=$((errors+1))
    fi
  else
    echo "ERROR: venv python not executable at $PYTHON"
    errors=$((errors+1))
  fi
else
  echo "ERROR: backend/.venv missing (run ./build.sh)"
  errors=$((errors+1))
fi

echo "Checking frontend dependencies and build..."
if [ -f "$ROOT_DIR/frontend/package.json" ]; then
  if [ -d "$ROOT_DIR/frontend/node_modules" ]; then
    echo "OK: frontend node_modules present"
  else
    echo "ERROR: frontend/node_modules missing (run npm --prefix frontend install)"
    errors=$((errors+1))
  fi
  if [ -d "$ROOT_DIR/frontend/dist" ] || [ -d "$ROOT_DIR/frontend/build" ]; then
    echo "OK: frontend build artifacts present"
  else
    echo "WARN: frontend build not found. Run npm --prefix frontend run build"
  fi
else
  echo "ERROR: frontend/package.json missing"
  errors=$((errors+1))
fi

echo "Checking .env presence"
if [ -f "$ROOT_DIR/.env" ]; then
  echo "OK: .env present"
else
  echo "WARN: .env missing. Copy .env.example to .env"
fi

if command -v coral >/dev/null 2>&1; then
  echo "Checking Coral sources and schemas (best-effort)..."
  if coral source list >/dev/null 2>&1; then
    echo "OK: coral source list command succeeded"
  else
    echo "WARN: coral source list failed"
  fi

  # Helper to test presence of schema/table via information_schema
  check_schema() {
    schema="$1"; table="$2"; name="$3"
    out=$(coral sql "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = '$schema'" 2>/dev/null || true)
    if echo "$out" | grep -qi "$schema"; then
      echo "OK: $name available"
    else
      echo "MISSING: $name"
      errors=$((errors+1))
    fi
  }

  check_schema oppia_roadmap projects "oppia_roadmap.projects (roadmap)"
  check_schema oppia_roadmap project_links "oppia_roadmap.project_links (roadmap link index)"
  check_schema github issues "github.issues (GitHub issues)"
  check_schema github pulls "github.pulls (GitHub pulls)"
  team_out=$(coral sql "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = 'oppia_team' AND table_name = 'members' LIMIT 1;" 2>/dev/null || true)
  if echo "$team_out" | grep -qi "oppia_team"; then
    echo "OK: oppia_team.members (contributor directory) available"
  else
    echo "INFO: oppia_team.members not available (email draft enrichment will be limited)"
  fi

  # Optional: GitHub Actions/CI signals
  ci_out=$(coral sql "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = 'ci' AND table_name = 'signals' LIMIT 1;" 2>/dev/null || true)
  if echo "$ci_out" | grep -qi "ci"; then
    echo "OK: ci.signals (GitHub Actions) available"
  else
    echo "INFO: ci.signals (GitHub Actions) not available (optional)"
  fi
else
  echo "WARN: Coral CLI missing; cannot verify sources"
fi

echo "Finished checks. Errors: $errors"
if [ $errors -gt 0 ]; then
  exit 1
fi
