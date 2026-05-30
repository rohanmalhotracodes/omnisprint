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

echo "Refreshing planning/team snapshots..."
if [ -x "$ROOT_DIR/scripts/sync_roadmap_sheet.sh" ]; then
  "$ROOT_DIR/scripts/sync_roadmap_sheet.sh"
elif [ -x "$ROOT_DIR/scripts/snapshot_google_sheets.sh" ]; then
  "$ROOT_DIR/scripts/snapshot_google_sheets.sh"
elif [ -x "$ROOT_DIR/scripts/snapshot_planning_source.sh" ]; then
  "$ROOT_DIR/scripts/snapshot_planning_source.sh"
else
  echo "ERROR: No planning snapshot script found. Expected one of:" >&2
  echo "  scripts/sync_roadmap_sheet.sh" >&2
  echo "  scripts/snapshot_google_sheets.sh" >&2
  echo "  scripts/snapshot_planning_source.sh" >&2
  exit 1
fi

if [ -x "$ROOT_DIR/scripts/register_coral_sources.sh" ]; then
  echo "Registering Coral sources..."
  "$ROOT_DIR/scripts/register_coral_sources.sh"
else
  echo "ERROR: scripts/register_coral_sources.sh not found or not executable." >&2
  exit 1
fi

# Verify core planning table is queryable; fail fast with clear guidance.
PLANNING_SCHEMA="${PLANNING_SCHEMA:-planning}"
PLANNING_PROJECTS_TABLE="${PLANNING_PROJECTS_TABLE:-projects}"
if ! coral sql --format json "SELECT * FROM ${PLANNING_SCHEMA}.${PLANNING_PROJECTS_TABLE} LIMIT 1" >/dev/null 2>&1; then
  echo "ERROR: Coral planning table ${PLANNING_SCHEMA}.${PLANNING_PROJECTS_TABLE} is not queryable." >&2
  echo "Check PLANNING_CSV_URL (or PLANNING_SHEET_ID/PLANNING_GID), then restart the Space." >&2
  echo "You can inspect available schemas with: SELECT DISTINCT schema_name FROM coral.tables" >&2
  exit 1
fi

echo "Launching FastAPI on port ${PORT:-7860}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-7860}"
