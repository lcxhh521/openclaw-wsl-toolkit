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

export OPENCLAW_BACKGROUND_WORKER="${OPENCLAW_BACKGROUND_WORKER:-1}"
export OPENCLAW_BACKGROUND_SILENT_TELEGRAM="${OPENCLAW_BACKGROUND_SILENT_TELEGRAM:-1}"

GATE="$HOME/.openclaw/workspace/scripts/task_gate.py"
if [[ -f "$GATE" ]]; then
  exec python3 "$GATE" \
    --task-type "market_immersion_${PHASE}" \
    --text "market immersion ${PHASE} background worker with Notion publish/review artifacts" \
    --expected-seconds 900 \
    --model-calls 3 \
    --external-side-effect \
    --allow-routes worker,review-required \
    --low-priority \
    -- \
    "$PYTHON_BIN" \
    "$MODULE_DIR/scripts/market_immersion.py" \
    --phase "$PHASE" \
    "$@"
fi

exec "$PYTHON_BIN" \
  "$MODULE_DIR/scripts/market_immersion.py" \
  --phase "$PHASE" \
  "$@"
