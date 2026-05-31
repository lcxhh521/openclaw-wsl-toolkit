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

load_env_exports() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    export "$key=$value"
  done < "$file"
}

load_env_exports "${OPENCLAW_VOLCENGINE_ENV:-$HOME/.openclaw/secrets/volcengine.env}"
load_env_exports "${OPENCLAW_DEEPSEEK_ENV:-$HOME/.openclaw/secrets/agent-room-deepseek.env}"

export OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK="${OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK:-1}"
export OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS="${OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS:-deepseek-v4-pro}"
export OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL="${OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL:-deepseek-v4-pro}"
export ARK_QC_V3_COOLDOWN_FALLBACK_MODEL="${ARK_QC_V3_COOLDOWN_FALLBACK_MODEL:-deepseek-v4-pro}"

export OPENCLAW_BACKGROUND_WORKER="${OPENCLAW_BACKGROUND_WORKER:-1}"
export OPENCLAW_BACKGROUND_SILENT_TELEGRAM="${OPENCLAW_BACKGROUND_SILENT_TELEGRAM:-0}"

GATE="$HOME/.openclaw/workspace/scripts/task_gate.py"

if [[ -f "$GATE" ]]; then
  python3 "$GATE" \
    --task-type "market_immersion_${PHASE}" \
    --text "market immersion ${PHASE} background worker with Notion publish/review artifacts" \
    --expected-seconds 900 \
    --model-calls 3 \
    --external-side-effect \
    --allow-routes worker,review-required \
    --print-decision
fi

run_market_once() {
  local cmd=(
    "$PYTHON_BIN"
    "$MODULE_DIR/scripts/market_immersion.py" \
    --phase "$PHASE" \
    "$@"
  )
  if [[ -f "$GATE" ]]; then
    if command -v ionice >/dev/null 2>&1; then
      ionice -c3 nice -n 10 "${cmd[@]}"
    else
      nice -n 10 "${cmd[@]}"
    fi
    return $?
  fi

  "${cmd[@]}"
}

is_retryable_failure() {
  local code="$1"
  case "$code" in
    2|5|6|7|75)
      return 0
      ;;
    3)
      if [[ "$LAST_MARKET_OUTPUT" == *"notion_validation_error"* ]] \
        || [[ "$LAST_MARKET_OUTPUT" == *"body failed validation"* ]] \
        || [[ "$LAST_MARKET_OUTPUT" == *"empty_report_blocked"* ]] \
        || [[ "$LAST_MARKET_OUTPUT" == *"no_market_items_collected"* ]]; then
        return 1
      fi
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

MAX_ATTEMPTS="${MARKET_IMMERSION_MAX_ATTEMPTS:-3}"
RETRY_SLEEP_SECONDS="${MARKET_IMMERSION_RETRY_SLEEP_SECONDS:-120}"
if [[ ! "$MAX_ATTEMPTS" =~ ^[0-9]+$ ]] || (( MAX_ATTEMPTS < 1 )); then
  MAX_ATTEMPTS=1
fi
if [[ ! "$RETRY_SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  RETRY_SLEEP_SECONDS=120
fi

attempt=1
while (( attempt <= MAX_ATTEMPTS )); do
  export MARKET_IMMERSION_ATTEMPT_INDEX="$attempt"
  export MARKET_IMMERSION_MAX_ATTEMPTS="$MAX_ATTEMPTS"
  if (( attempt < MAX_ATTEMPTS )); then
    export MARKET_IMMERSION_FINAL_ATTEMPT=0
    export MARKET_IMMERSION_SUPPRESS_FAILURE_TELEGRAM=1
  else
    export MARKET_IMMERSION_FINAL_ATTEMPT=1
    unset MARKET_IMMERSION_SUPPRESS_FAILURE_TELEGRAM
  fi

  set +e
  LAST_MARKET_OUTPUT="$(run_market_once "$@" 2>&1)"
  status=$?
  set -e
  printf '%s\n' "$LAST_MARKET_OUTPUT"

  if (( status == 0 )); then
    exit 0
  fi
  if (( attempt >= MAX_ATTEMPTS )) || ! is_retryable_failure "$status"; then
    exit "$status"
  fi

  echo "market-immersion: attempt ${attempt}/${MAX_ATTEMPTS} failed with exit ${status}; retrying in ${RETRY_SLEEP_SECONDS}s without Telegram failure spam." >&2
  sleep "$RETRY_SLEEP_SECONDS"
  attempt=$((attempt + 1))
done

exit "$status"
