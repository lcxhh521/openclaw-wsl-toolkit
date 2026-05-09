#!/usr/bin/env bash
set -euo pipefail

MODULE_DIR="${MARKET_IMMERSION_DIR:-$HOME/.openclaw/workspace/market-immersion-module}"
PHASE="${1:-morning}"
shift || true
CONFIG_PATH="$MODULE_DIR/config/market_immersion_config.json"
PYTHON_BIN="$HOME/.openclaw/workspace/.venv-mxskills/bin/python"

cd "$MODULE_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "market-immersion: missing python runtime: $PYTHON_BIN" >&2
  exit 10
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "market-immersion: missing config: $CONFIG_PATH" >&2
  exit 11
fi

exec "$PYTHON_BIN" \
  "$MODULE_DIR/scripts/market_immersion.py" \
  --phase "$PHASE" \
  "$@"
