#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# A release bundle contains runtime/python. The .venv fallback keeps this
# launcher convenient when running directly from a development checkout.
PYTHON=""
for candidate in \
  "$APP_DIR/runtime/python/bin/python3" \
  "$APP_DIR/runtime/python/bin/python" \
  "$APP_DIR/runtime/bin/python3" \
  "$APP_DIR/.venv/bin/python3" \
  "$APP_DIR/.venv/bin/python"; do
  if [[ -x "$candidate" ]]; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "JSON Forge bundled Python runtime was not found." >&2
  echo "Please use a complete release package, or create .venv and install requirements.txt." >&2
  exit 1
fi

exec "$PYTHON" "$APP_DIR/app.py" "$@"
