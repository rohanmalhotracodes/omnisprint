#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Starting OmniSprint for Hugging Face Spaces in $ROOT_DIR"

if ! command -v coral >/dev/null 2>&1; then
  echo "ERROR: Coral CLI is not installed in this container. OmniSprint requires Coral for data retrieval." >&2
  exit 1
fi

echo "Coral CLI version:"
coral --version

if [ -x "$ROOT_DIR/scripts/register_coral_sources.sh" ]; then
  echo "Registering Coral sources..."
  "$ROOT_DIR/scripts/register_coral_sources.sh"
else
  echo "ERROR: scripts/register_coral_sources.sh not found or not executable." >&2
  exit 1
fi

echo "Refreshing planning/team snapshots (best effort)..."
if [ -x "$ROOT_DIR/scripts/sync_roadmap_sheet.sh" ]; then
  "$ROOT_DIR/scripts/sync_roadmap_sheet.sh" || true
elif [ -x "$ROOT_DIR/scripts/snapshot_google_sheets.sh" ]; then
  "$ROOT_DIR/scripts/snapshot_google_sheets.sh" || true
elif [ -x "$ROOT_DIR/scripts/snapshot_planning_source.sh" ]; then
  "$ROOT_DIR/scripts/snapshot_planning_source.sh" || true
else
  echo "No planning snapshot script found; continuing with existing data."
fi

echo "Launching FastAPI on port ${PORT:-7860}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-7860}"
