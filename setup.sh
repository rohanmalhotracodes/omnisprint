#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Setup wrapper: delegating to build.sh in $ROOT_DIR"

exec "$ROOT_DIR/build.sh"
