#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
SECRET_DIR="$OPENCLAW_HOME/secrets"
SECRET_FILE="$SECRET_DIR/volcengine.env"
SYSTEMD_DROPIN_DIR="$HOME/.config/systemd/user/openclaw-gateway.service.d"
SYSTEMD_DROPIN="$SYSTEMD_DROPIN_DIR/volcengine.conf"

if [ "${VOLCANO_ENGINE_API_KEY:-}" ]; then
  key="$VOLCANO_ENGINE_API_KEY"
else
  printf 'Ark Coding Plan API key: ' >&2
  stty_state="$(stty -g 2>/dev/null || true)"
  if [ -n "$stty_state" ]; then stty -echo; fi
  IFS= read -r key
  if [ -n "$stty_state" ]; then stty "$stty_state"; fi
  printf '\n' >&2
fi

key="${key#"${key%%[![:space:]]*}"}"
key="${key%"${key##*[![:space:]]}"}"
key="${key#VOLCANO_ENGINE_API_KEY=}"
key="${key#VOLCENGINE_API_KEY=}"
key="${key%\"}"
key="${key#\"}"
key="${key%\'}"
key="${key#\'}"

if [ -z "$key" ]; then
  echo "No API key provided." >&2
  exit 1
fi

mkdir -p "$SECRET_DIR" "$SYSTEMD_DROPIN_DIR"
{
  printf 'VOLCANO_ENGINE_API_KEY=%s\n' "$key"
  printf 'VOLCENGINE_API_KEY=%s\n' "$key"
} > "$SECRET_FILE"
chmod 600 "$SECRET_FILE"

printf '%s\n' '[Service]' "EnvironmentFile=$SECRET_FILE" > "$SYSTEMD_DROPIN"

API_KEY="$key" OPENCLAW_HOME="$OPENCLAW_HOME" node <<'JS'
const fs = require('fs');
const path = require('path');

const openclawHome = process.env.OPENCLAW_HOME;
const apiKey = process.env.API_KEY;

const codingProvider = {
  baseUrl: 'https://ark.cn-beijing.volces.com/api/coding/v3',
  apiKey,
  api: 'openai-completions',
  models: [
    { id: 'ark-code-latest', name: 'ark-code-latest', contextWindow: 256000, maxTokens: 32000, input: ['text', 'image'] },
    { id: 'doubao-seed-code', name: 'doubao-seed-code', contextWindow: 256000, maxTokens: 32000, input: ['text', 'image'] },
    { id: 'glm-4.7', name: 'glm-4.7', contextWindow: 200000, maxTokens: 128000, input: ['text'] },
    { id: 'deepseek-v3.2', name: 'deepseek-v3.2', contextWindow: 128000, maxTokens: 32000 },
    { id: 'doubao-seed-2.0-code', name: 'doubao-seed-2.0-code', contextWindow: 256000, maxTokens: 128000, input: ['text', 'image'] },
    { id: 'doubao-seed-2.0-pro', name: 'doubao-seed-2.0-pro', contextWindow: 256000, maxTokens: 128000, input: ['text', 'image'] },
    { id: 'doubao-seed-2.0-lite', name: 'doubao-seed-2.0-lite', contextWindow: 256000, maxTokens: 128000, input: ['text', 'image'] },
    { id: 'minimax-m2.5', name: 'minimax-m2.5', contextWindow: 200000, maxTokens: 128000, input: ['text'] },
    { id: 'kimi-k2.5', name: 'kimi-k2.5', contextWindow: 256000, maxTokens: 32000, input: ['text', 'image'] }
  ]
};

const modelRefs = Object.fromEntries(
  codingProvider.models.map((model) => [`volcengine-plan/${model.id}`, {}])
);

function readJson(file) {
  if (!fs.existsSync(file)) return {};
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (fs.existsSync(file)) {
    fs.copyFileSync(file, `${file}.bak-codingplan-${Date.now()}`);
  }
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
}

const openclawPath = path.join(openclawHome, 'openclaw.json');
const openclaw = readJson(openclawPath);
openclaw.models ??= {};
openclaw.models.providers ??= {};
openclaw.models.providers['volcengine-plan'] = codingProvider;
openclaw.agents ??= {};
openclaw.agents.defaults ??= {};
openclaw.agents.defaults.model = {
  ...(openclaw.agents.defaults.model ?? {}),
  primary: 'volcengine-plan/ark-code-latest'
};
openclaw.agents.defaults.models = {
  ...(openclaw.agents.defaults.models ?? {}),
  ...modelRefs
};
writeJson(openclawPath, openclaw);

const modelsPath = path.join(openclawHome, 'agents', 'main', 'agent', 'models.json');
const models = readJson(modelsPath);
models.providers ??= {};
models.providers['volcengine-plan'] = codingProvider;
delete models.models;
delete models.agents;
writeJson(modelsPath, models);

console.log(`Configured volcengine-plan with key len=${apiKey.length}, prefix=${apiKey.slice(0, 8)}, suffix=${apiKey.slice(-6)}`);
JS

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
  if systemctl --user list-unit-files openclaw-gateway.service >/dev/null 2>&1; then
    systemctl --user restart openclaw-gateway.service || true
  fi
fi

if command -v "$OPENCLAW_BIN" >/dev/null 2>&1; then
  "$OPENCLAW_BIN" config validate >/dev/null
fi

echo "Volcengine Ark Coding Plan configuration updated."
