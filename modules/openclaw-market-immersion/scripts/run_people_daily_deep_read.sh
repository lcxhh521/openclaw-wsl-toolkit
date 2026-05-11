#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export OPENCLAW_BACKGROUND_WORKER="${OPENCLAW_BACKGROUND_WORKER:-1}"
export OPENCLAW_BACKGROUND_SILENT_TELEGRAM="${OPENCLAW_BACKGROUND_SILENT_TELEGRAM:-1}"

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
