#!/usr/bin/env bash
set -euo pipefail

# Example installer for the OpenClaw main-side mailbox watcher.
# Edit env values before running. This script writes only user-level systemd files.

UNIT_NAME="${UNIT_NAME:-agent-collab-main-mailbox-watch}"
MAILBOX_DIR="${OPENCLAW_AGENT_MAILBOX_DIR:-$HOME/.openclaw/agent-mailboxes/codex-main}"
OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.local/bin/openclaw}"
MAIN_SESSION_ID="${OPENCLAW_MAIN_SESSION_ID:-}"
SCRIPT_PATH="${AGENT_COLLAB_MAIN_WATCHER:-$(pwd)/agent-collab/scripts/openclaw-main-mailbox-watch.py}"

if [[ -z "$MAIN_SESSION_ID" ]]; then
  echo "ERROR: set OPENCLAW_MAIN_SESSION_ID first" >&2
  exit 2
fi

mkdir -p "$HOME/.config/systemd/user" "$MAILBOX_DIR"

cat > "$HOME/.config/systemd/user/$UNIT_NAME.service" <<EOF
[Unit]
Description=Agent collaboration main mailbox watcher

[Service]
Type=oneshot
Environment=OPENCLAW_AGENT_MAILBOX_DIR=$MAILBOX_DIR
Environment=OPENCLAW_BIN=$OPENCLAW_BIN
Environment=OPENCLAW_MAIN_SESSION_ID=$MAIN_SESSION_ID
ExecStart=/usr/bin/env python3 $SCRIPT_PATH
EOF

cat > "$HOME/.config/systemd/user/$UNIT_NAME.timer" <<EOF
[Unit]
Description=Run agent collaboration main mailbox watcher periodically

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME.timer"
systemctl --user status "$UNIT_NAME.timer" --no-pager
