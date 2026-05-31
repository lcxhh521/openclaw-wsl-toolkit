#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export OPENCLAW_BACKGROUND_WORKER="${OPENCLAW_BACKGROUND_WORKER:-1}"
export OPENCLAW_BACKGROUND_SILENT_TELEGRAM="${OPENCLAW_BACKGROUND_SILENT_TELEGRAM:-1}"

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

load_env_exports "${OPENCLAW_DEEPSEEK_ENV:-$HOME/.openclaw/secrets/agent-room-deepseek.env}"
export OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK="${OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK:-1}"
export OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS="${OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS:-deepseek-v4-pro}"
export OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL="${OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL:-deepseek-v4-pro}"

GATE="$HOME/.openclaw/workspace/scripts/task_gate.py"
if [[ -f "$GATE" ]]; then
  exec python3 "$GATE" \
    --task-type "people_daily_deep_read" \
    --text "People Daily deep read background worker with Notion publish/review artifacts" \
    --expected-seconds 21600 \
    --model-calls 8 \
    --external-side-effect \
    --allow-routes worker,review-required \
    --low-priority \
    -- \
    python3 "$SCRIPT_DIR/people_daily_workflow.py" "$@"
fi

python3 "$SCRIPT_DIR/people_daily_workflow.py" "$@"
