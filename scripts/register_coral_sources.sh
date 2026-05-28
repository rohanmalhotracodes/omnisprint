#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Registering Coral sources (scripts/register_coral_sources.sh)"

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

load_env_file "$ROOT_DIR/.env"

register() {
  spec="$1"
  [ -f "$spec" ] || return 0
  echo "Attempting to add source from $spec"
  data_dir="file://$ROOT_DIR/coral/data/"
  rendered_spec="$(mktemp -t omnisprint_coral_spec_XXXX.yaml)"
  sed "s|__DATA_DIR__|$data_dir|g" "$spec" > "$rendered_spec"
  # Show errors but don't exit the script
  if coral source add --file "$rendered_spec" 2>&1; then
    echo "Registered source: $spec"
    rm -f "$rendered_spec"
    return 0
  else
    echo "Failed to register $spec (continuing)"
    rm -f "$rendered_spec"
    return 1
  fi
}

echo "Adding bundled GitHub source (best-effort)"
set +e
coral source add github >/dev/null 2>&1
GITHUB_ADD_EXIT=$?
set -e
if [ $GITHUB_ADD_EXIT -eq 0 ]; then
  echo "GitHub source added or already present"
else
  echo "Warning: Could not add GitHub source automatically. If you want live GitHub data, set GITHUB_TOKEN in .env and retry."
fi

# Register only required sources for OmniSprint.
register "$ROOT_DIR/coral/sources/planning_sheet.yaml" || true
register "$ROOT_DIR/coral/sources/team_directory.yaml" || true

# Optional source: GitHub Actions/CI signals (shown only when available).
register "$ROOT_DIR/coral/sources/ci_signals.yaml" || true

echo "Final Coral source list:"
coral source list || true

echo "Coral registration script complete"
