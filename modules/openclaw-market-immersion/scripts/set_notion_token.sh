#!/usr/bin/env bash
set -euo pipefail

secret_dir="$HOME/.openclaw/secrets"
secret_file="$secret_dir/notion.env"
parent_page_id="3548c93dc6b480a5b6ebe997305a30ff"

mkdir -p "$secret_dir"
chmod 700 "$secret_dir"

echo "Paste Notion Installation access token, then press Enter."
echo "Input is hidden. Use the Copy button from Notion."
printf "NOTION_TOKEN: "
IFS= read -r -s token
printf "\n"

token="$(printf "%s" "$token" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [[ -z "$token" ]]; then
  echo "Empty token, nothing written." >&2
  exit 1
fi
if [[ "$token" != ntn_* ]]; then
  echo "Token does not start with ntn_. Check that you copied the Installation access token only." >&2
  exit 1
fi

{
  printf "NOTION_TOKEN=%s\n" "$token"
  printf "NOTION_PARENT_PAGE_ID=%s\n" "$parent_page_id"
} > "$secret_file"
chmod 600 "$secret_file"

echo "Saved token length: ${#token}"
echo "Testing Notion authentication from inside WSL..."

http_code="$(
  curl -sS -o /tmp/openclaw_notion_me.json -w "%{http_code}" \
    -H "Authorization: Bearer $token" \
    -H "Notion-Version: 2022-06-28" \
    https://api.notion.com/v1/users/me
)"

if [[ "$http_code" == "200" ]]; then
  echo "Notion authentication: OK"
  python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("/tmp/openclaw_notion_me.json").read_text(encoding="utf-8"))
print("Bot name:", data.get("name"))
print("Bot id:", data.get("id"))
PY
else
  echo "Notion authentication failed: HTTP $http_code" >&2
  cat /tmp/openclaw_notion_me.json >&2
  exit 1
fi

echo "Done. Press Enter to close this window."
IFS= read -r _
