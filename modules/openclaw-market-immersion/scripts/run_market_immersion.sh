#!/usr/bin/env bash
set -euo pipefail

MODULE_DIR="${MARKET_IMMERSION_DIR:-$HOME/.openclaw/workspace/market-immersion-module}"
PHASE="morning"
EXTRA_ARGS=()
PYTHON_BIN="${MARKET_IMMERSION_PYTHON:-$HOME/.openclaw/workspace/.venv-mxskills/bin/python}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)
      [[ $# -ge 2 ]] || { echo "market-immersion: missing value for --phase" >&2; exit 2; }
      PHASE="$2"
      shift 2
      ;;
    --config|--timeout)
      [[ $# -ge 2 ]] || { echo "market-immersion: missing value for $1" >&2; exit 2; }
      EXTRA_ARGS+=("$1" "$2")
      shift 2
      ;;
    --dry-run)
      EXTRA_ARGS+=("$1")
      shift
      ;;
    --no-publish)
      # Backward-compatible no-op. Smoke runs do not publish by config; formal publish is controlled in config.
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Usage:
  run_market_immersion.sh [phase]
  run_market_immersion.sh --phase <morning|midday|close|night|smoke> [--dry-run] [--config PATH] [--timeout SEC]

Publishing is controlled by config. --no-publish is accepted as a compatibility no-op.
EOF
      exit 0
      ;;
    --*)
      echo "market-immersion: unknown option: $1" >&2
      exit 2
      ;;
    *)
      PHASE="$1"
      shift
      ;;
  esac
done

CONFIG_PATH="$MODULE_DIR/config/market_immersion_config.json"

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
  "${EXTRA_ARGS[@]}"
