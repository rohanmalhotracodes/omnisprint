#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/coral/data"

CSV_PATH="$ROOT_DIR/coral/data/oppia_roadmap_snapshot.csv"
JSONL_PATH="$ROOT_DIR/coral/data/oppia_roadmap_snapshot.jsonl"
LINKS_JSONL_PATH="$ROOT_DIR/coral/data/oppia_roadmap_project_links.jsonl"
TEAM_CSV_PATH="$ROOT_DIR/coral/data/oppia_team_snapshot.csv"
TEAM_JSONL_PATH="$ROOT_DIR/coral/data/oppia_team_snapshot.jsonl"

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

# Fallback URLs from sheet ID + gid when direct CSV URLs are not provided.
if [ -z "${OPPIA_ROADMAP_CSV_URL-}" ] && [ -n "${OPPIA_ROADMAP_SHEET_ID-}" ] && [ -n "${OPPIA_ROADMAP_GID-}" ]; then
  OPPIA_ROADMAP_CSV_URL="https://docs.google.com/spreadsheets/d/${OPPIA_ROADMAP_SHEET_ID}/export?format=csv&gid=${OPPIA_ROADMAP_GID}"
fi
if [ -z "${OPPIA_TEAM_CSV_URL-}" ] && [ -n "${OPPIA_TEAM_SHEET_ID-}" ] && [ -n "${OPPIA_TEAM_GID-}" ]; then
  OPPIA_TEAM_CSV_URL="https://docs.google.com/spreadsheets/d/${OPPIA_TEAM_SHEET_ID}/export?format=csv&gid=${OPPIA_TEAM_GID}"
fi

if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  PYTHON_CMD=("$ROOT_DIR/backend/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
else
  echo "No python interpreter found to build roadmap JSONL snapshot"
  exit 1
fi

if [ -z "${OPPIA_ROADMAP_CSV_URL-}" ]; then
  echo "OPPIA_ROADMAP_CSV_URL not set. Export URL can be placed in .env or environment."
else
  echo "Downloading roadmap snapshot..."
  curl -sfL "$OPPIA_ROADMAP_CSV_URL" -o "$CSV_PATH" || { echo "Failed to download roadmap snapshot"; exit 1; }
  echo "Saved to coral/data/oppia_roadmap_snapshot.csv"
fi

if [ -f "$CSV_PATH" ]; then
  echo "Building roadmap JSONL snapshot from CSV..."
  "${PYTHON_CMD[@]}" "$ROOT_DIR/backend/roadmap_snapshot_builder.py" \
    --input "$CSV_PATH" \
    --output "$JSONL_PATH" \
    --links-output "$LINKS_JSONL_PATH" || { echo "Failed to build JSONL snapshot"; exit 1; }
  echo "Saved to coral/data/oppia_roadmap_snapshot.jsonl"
  echo "Saved to coral/data/oppia_roadmap_project_links.jsonl"
else
  echo "Roadmap CSV snapshot not found at $CSV_PATH"
fi

if [ -z "${OPPIA_TEAM_CSV_URL-}" ]; then
  echo "OPPIA_TEAM_CSV_URL not set. Team directory snapshot not refreshed."
else
  echo "Downloading contributor directory snapshot..."
  curl -sfL "$OPPIA_TEAM_CSV_URL" -o "$TEAM_CSV_PATH" || { echo "Failed to download team snapshot"; exit 1; }
  echo "Saved to coral/data/oppia_team_snapshot.csv"
fi

if [ -f "$TEAM_CSV_PATH" ]; then
  echo "Building team JSONL snapshot from CSV..."
  "${PYTHON_CMD[@]}" "$ROOT_DIR/backend/team_snapshot_builder.py" \
    --input "$TEAM_CSV_PATH" \
    --output "$TEAM_JSONL_PATH" || { echo "Failed to build team JSONL snapshot"; exit 1; }
  echo "Saved to coral/data/oppia_team_snapshot.jsonl"
fi

echo "Snapshot script complete"
