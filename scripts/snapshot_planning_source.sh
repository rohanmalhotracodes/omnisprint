#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/coral/data"

CSV_PATH="$ROOT_DIR/coral/data/planning_snapshot.csv"
JSONL_PATH="$ROOT_DIR/coral/data/planning_snapshot.jsonl"
LINKS_JSONL_PATH="$ROOT_DIR/coral/data/planning_project_links.jsonl"
TEAM_CSV_PATH="$ROOT_DIR/coral/data/team_snapshot.csv"
TEAM_JSONL_PATH="$ROOT_DIR/coral/data/team_snapshot.jsonl"

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

# Backward-compatible env aliases from older demos.
if [ -z "${PLANNING_CSV_URL-}" ] && [ -n "${OPPIA_ROADMAP_CSV_URL-}" ]; then
  PLANNING_CSV_URL="${OPPIA_ROADMAP_CSV_URL}"
fi
if [ -z "${PLANNING_SHEET_ID-}" ] && [ -n "${OPPIA_ROADMAP_SHEET_ID-}" ]; then
  PLANNING_SHEET_ID="${OPPIA_ROADMAP_SHEET_ID}"
fi
if [ -z "${PLANNING_GID-}" ] && [ -n "${OPPIA_ROADMAP_GID-}" ]; then
  PLANNING_GID="${OPPIA_ROADMAP_GID}"
fi
if [ -z "${TEAM_CSV_URL-}" ] && [ -n "${OPPIA_TEAM_CSV_URL-}" ]; then
  TEAM_CSV_URL="${OPPIA_TEAM_CSV_URL}"
fi
if [ -z "${TEAM_SHEET_ID-}" ] && [ -n "${OPPIA_TEAM_SHEET_ID-}" ]; then
  TEAM_SHEET_ID="${OPPIA_TEAM_SHEET_ID}"
fi
if [ -z "${TEAM_GID-}" ] && [ -n "${OPPIA_TEAM_GID-}" ]; then
  TEAM_GID="${OPPIA_TEAM_GID}"
fi

# Fallback URLs from sheet ID + gid when direct CSV URLs are not provided.
if [ -z "${PLANNING_CSV_URL-}" ] && [ -n "${PLANNING_SHEET_ID-}" ] && [ -n "${PLANNING_GID-}" ]; then
  PLANNING_CSV_URL="https://docs.google.com/spreadsheets/d/${PLANNING_SHEET_ID}/export?format=csv&gid=${PLANNING_GID}"
fi
if [ -z "${TEAM_CSV_URL-}" ] && [ -n "${TEAM_SHEET_ID-}" ] && [ -n "${TEAM_GID-}" ]; then
  TEAM_CSV_URL="https://docs.google.com/spreadsheets/d/${TEAM_SHEET_ID}/export?format=csv&gid=${TEAM_GID}"
fi

if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  PYTHON_CMD=("$ROOT_DIR/backend/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
else
  echo "No python interpreter found to build planning JSONL snapshot"
  exit 1
fi

if [ -z "${PLANNING_CSV_URL-}" ]; then
  echo "PLANNING_CSV_URL not set. Skipping planning snapshot refresh."
else
  echo "Downloading planning snapshot..."
  curl -sfL "$PLANNING_CSV_URL" -o "$CSV_PATH" || { echo "Failed to download planning snapshot"; exit 1; }
  echo "Saved to coral/data/planning_snapshot.csv"
fi

if [ -n "${PLANNING_CSV_URL-}" ] && [ -f "$CSV_PATH" ]; then
  echo "Building planning JSONL snapshot from CSV..."
  "${PYTHON_CMD[@]}" "$ROOT_DIR/backend/planning_snapshot_builder.py" \
    --input "$CSV_PATH" \
    --output "$JSONL_PATH" \
    --links-output "$LINKS_JSONL_PATH" || { echo "Failed to build JSONL snapshot"; exit 1; }
  echo "Saved to coral/data/planning_snapshot.jsonl"
  echo "Saved to coral/data/planning_project_links.jsonl"
else
  echo "Planning JSONL rebuild skipped."
fi

if [ -z "${TEAM_CSV_URL-}" ]; then
  echo "TEAM_CSV_URL not set. Team directory snapshot refresh skipped."
else
  echo "Downloading contributor directory snapshot..."
  curl -sfL "$TEAM_CSV_URL" -o "$TEAM_CSV_PATH" || { echo "Failed to download team snapshot"; exit 1; }
  echo "Saved to coral/data/team_snapshot.csv"
fi

if [ -n "${TEAM_CSV_URL-}" ] && [ -f "$TEAM_CSV_PATH" ]; then
  echo "Building team JSONL snapshot from CSV..."
  "${PYTHON_CMD[@]}" "$ROOT_DIR/backend/team_snapshot_builder.py" \
    --input "$TEAM_CSV_PATH" \
    --output "$TEAM_JSONL_PATH" || { echo "Failed to build team JSONL snapshot"; exit 1; }
  echo "Saved to coral/data/team_snapshot.jsonl"
else
  echo "Team JSONL rebuild skipped."
fi

echo "Snapshot script complete"
