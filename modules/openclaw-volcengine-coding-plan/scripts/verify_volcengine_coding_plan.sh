#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"

if ! command -v "$OPENCLAW_BIN" >/dev/null 2>&1; then
  echo "openclaw command not found. Set OPENCLAW_BIN or install OpenClaw." >&2
  exit 1
fi

node <<'JS'
const fs = require('fs');
const path = require('path');
const openclawHome = process.env.OPENCLAW_HOME || `${process.env.HOME}/.openclaw`;

function readJson(file) {
  if (!fs.existsSync(file)) throw new Error(`${file} does not exist`);
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

const openclaw = readJson(path.join(openclawHome, 'openclaw.json'));
const models = readJson(path.join(openclawHome, 'agents', 'main', 'agent', 'models.json'));
const provider = openclaw.models?.providers?.['volcengine-plan'];
const agentProvider = models.providers?.['volcengine-plan'];

if (!provider) throw new Error('openclaw.json is missing models.providers.volcengine-plan');
if (!agentProvider) throw new Error('agent models.json is missing providers.volcengine-plan');
if (provider.baseUrl !== 'https://ark.cn-beijing.volces.com/api/coding/v3') {
  throw new Error(`wrong openclaw.json baseUrl: ${provider.baseUrl}`);
}
if (agentProvider.baseUrl !== 'https://ark.cn-beijing.volces.com/api/coding/v3') {
  throw new Error(`wrong models.json baseUrl: ${agentProvider.baseUrl}`);
}
if (!provider.apiKey || !agentProvider.apiKey) {
  throw new Error('volcengine-plan apiKey is missing from provider config');
}
console.log(JSON.stringify({
  primary: openclaw.agents?.defaults?.model?.primary,
  baseUrl: provider.baseUrl,
  modelCount: agentProvider.models?.length || 0,
  keyShape: `${String(provider.apiKey).slice(0, 8)}...${String(provider.apiKey).slice(-6)}`
}, null, 2));
JS

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user is-active openclaw-gateway.service || true
fi

"$OPENCLAW_BIN" infer model run \
  --model volcengine-plan/ark-code-latest \
  --prompt "Reply with OK only." \
  --json
