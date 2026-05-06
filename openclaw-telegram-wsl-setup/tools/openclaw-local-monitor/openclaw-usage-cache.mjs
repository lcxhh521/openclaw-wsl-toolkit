#!/usr/bin/env node
import fs from "fs";
import os from "os";
import path from "path";

const home = os.homedir();
const agentsRoot = path.join(home, ".openclaw", "agents");
const cacheDir = path.join(home, ".openclaw", "monitor-cache");
const cachePath = path.join(cacheDir, "usage-summary.json");
const now = Date.now();
const dayStart = new Date();
dayStart.setHours(0, 0, 0, 0);
const dayStartMs = dayStart.getTime();
const maxFiles = Number(process.env.OPENCLAW_USAGE_CACHE_MAX_FILES || 250);

const out = {
  generatedAt: new Date(now).toISOString(),
  status: "ok",
  stale: false,
  source: "offline-session-cache",
  today: {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    estimatedCost: null
  },
  currentTelegramSession: null,
  buckets: [],
  filesScanned: 0,
  sessionsConsidered: 0,
  notes: [
    "cache-only; generated without gateway RPC",
    "read by OpenClaw local monitor"
  ]
};

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function toMs(value, fallback = 0) {
  if (value == null) return fallback;
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const n = Number(value);
  if (Number.isFinite(n) && n > 0) return n > 1e12 ? n : n * 1000;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function safeReaddir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function collectSessionFiles() {
  const files = [];
  for (const agentEntry of safeReaddir(agentsRoot)) {
    if (!agentEntry.isDirectory()) continue;
    const sessionsDir = path.join(agentsRoot, agentEntry.name, "sessions");
    for (const entry of safeReaddir(sessionsDir)) {
      if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
      if (entry.name.includes(".checkpoint.") || entry.name.includes(".trajectory.")) continue;
      const filePath = path.join(sessionsDir, entry.name);
      try {
        const stat = fs.statSync(filePath);
        files.push({
          path: filePath,
          agentId: agentEntry.name,
          name: entry.name,
          mtimeMs: stat.mtimeMs,
          size: stat.size
        });
      } catch {
      }
    }
  }
  return files
    .filter((file) => file.mtimeMs >= dayStartMs || now - file.mtimeMs <= 24 * 60 * 60 * 1000)
    .sort((a, b) => b.mtimeMs - a.mtimeMs)
    .slice(0, maxFiles);
}

function bucketFor(map, key, label) {
  if (!map.has(key)) {
    map.set(key, {
      key,
      label,
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      totalTokens: 0,
      estimatedCost: null,
      replies: 0
    });
  }
  return map.get(key);
}

function addUsageEvent(row, usage, base, seen, buckets) {
  if (!usage || typeof usage !== "object") return;
  const input = toNumber(usage.inputTokens ?? usage.input ?? usage.promptTokens);
  const output = toNumber(usage.outputTokens ?? usage.output ?? usage.completionTokens);
  const cacheRead = toNumber(usage.cacheReadTokens ?? usage.cacheRead);
  const cacheWrite = toNumber(usage.cacheWriteTokens ?? usage.cacheWrite);
  const totalTokens = input + output || toNumber(usage.totalTokens);
  const cost = toNumber(usage.cost?.total ?? usage.estimatedCost ?? usage.totalCost);
  if (!(input || output || cacheRead || cacheWrite || totalTokens || cost)) return;

  const eventMs = toMs(row.timestamp ?? row.ts ?? row.createdAt ?? row.updatedAt, base.mtimeMs);
  if (eventMs < dayStartMs) return;

  const provider = String(row.provider ?? base.provider ?? "-");
  const model = String(row.model ?? base.model ?? "-");
  const id = String(row.responseId ?? row.id ?? row.runId ?? "");
  const dedupe = id || [eventMs, provider, model, input, output, cacheRead, cacheWrite, totalTokens, cost].join("|");
  if (seen.has(dedupe)) return;
  seen.add(dedupe);

  out.today.inputTokens += input;
  out.today.outputTokens += output;
  out.today.cacheReadTokens += cacheRead;
  out.today.cacheWriteTokens += cacheWrite;
  out.today.totalTokens += totalTokens || input + output;
  if (cost > 0) out.today.estimatedCost = (out.today.estimatedCost || 0) + cost;

  const bucket = bucketFor(buckets, provider + "/" + model, provider + "/" + model);
  bucket.inputTokens += input;
  bucket.outputTokens += output;
  bucket.cacheReadTokens += cacheRead;
  bucket.cacheWriteTokens += cacheWrite;
  bucket.totalTokens += totalTokens || input + output;
  if (cost > 0) bucket.estimatedCost = (bucket.estimatedCost || 0) + cost;
  bucket.replies += 1;
}

function visitForUsage(value, base, seen, buckets, depth = 0) {
  if (!value || typeof value !== "object" || depth > 8) return;
  if (value.usage && typeof value.usage === "object") {
    addUsageEvent(value, value.usage, base, seen, buckets);
  }
  if (Array.isArray(value)) {
    for (const item of value) visitForUsage(item, base, seen, buckets, depth + 1);
    return;
  }
  for (const key of Object.keys(value)) {
    if (key === "config" || key === "secrets" || key === "redacted") continue;
    visitForUsage(value[key], base, seen, buckets, depth + 1);
  }
}

function extractSnapshot(value, session) {
  if (!value || typeof value !== "object") return;
  const key = String(value.sessionKey ?? value.key ?? value.conversationKey ?? "");
  if (key) session.sessionKey = key;
  const model = String(value.model ?? value.primaryModel ?? "");
  if (model) session.model = model;
  const total = toNumber(value.totalTokens);
  const context = toNumber(value.contextTokens);
  const limit = toNumber(value.contextLimit ?? value.maxContextTokens);
  if (total > 0) session.totalTokens = Math.max(session.totalTokens || 0, total);
  if (context > 0) session.contextTokens = Math.max(session.contextTokens || 0, context);
  if (limit > 0) session.contextLimit = Math.max(session.contextLimit || 0, limit);
  if (Array.isArray(value)) {
    for (const item of value) extractSnapshot(item, session);
    return;
  }
  for (const child of Object.values(value)) {
    if (child && typeof child === "object") extractSnapshot(child, session);
  }
}

try {
  fs.mkdirSync(cacheDir, { recursive: true });
  const files = collectSessionFiles();
  out.sessionsConsidered = files.length;
  const seen = new Set();
  const buckets = new Map();
  const sessionSnapshots = [];

  for (const file of files) {
    let text = "";
    try {
      text = fs.readFileSync(file.path, "utf8");
    } catch {
      continue;
    }
    out.filesScanned += 1;
    const session = {
      sessionKey: "",
      agentId: file.agentId,
      file: file.name,
      model: "",
      totalTokens: 0,
      contextTokens: 0,
      contextLimit: 0,
      updatedAt: file.mtimeMs
    };
    if (file.name.toLowerCase().includes("telegram")) {
      session.sessionKey = "agent:" + file.agentId + ":telegram:" + file.name.replace(/\.jsonl$/, "");
    }

    for (const line of text.split(/\r?\n/)) {
      if (!line || line[0] !== "{") continue;
      let row;
      try {
        row = JSON.parse(line);
      } catch {
        continue;
      }
      const base = {
        provider: row.provider,
        model: row.model,
        mtimeMs: file.mtimeMs
      };
      if (line.includes("usage") || line.includes("Tokens") || line.includes("cost")) {
        visitForUsage(row, base, seen, buckets);
        extractSnapshot(row, session);
      }
    }
    if (session.totalTokens || session.contextTokens || session.sessionKey) {
      sessionSnapshots.push(session);
    }
  }

  out.buckets = Array.from(buckets.values()).sort((a, b) =>
    (b.estimatedCost || 0) - (a.estimatedCost || 0) || b.totalTokens - a.totalTokens
  );

  const telegram = sessionSnapshots
    .filter((session) => (session.sessionKey || session.file).toLowerCase().includes("telegram"))
    .sort((a, b) => b.updatedAt - a.updatedAt)[0];
  if (telegram) {
    out.currentTelegramSession = {
      sessionKey: telegram.sessionKey || "agent:" + telegram.agentId + ":telegram:" + telegram.file.replace(/\.jsonl$/, ""),
      agentId: telegram.agentId,
      model: telegram.model || null,
      totalTokens: telegram.totalTokens || 0,
      contextTokens: telegram.contextTokens || 0,
      contextLimit: telegram.contextLimit || 0,
      updatedAt: new Date(telegram.updatedAt).toISOString()
    };
  }

  const tempPath = cachePath + ".tmp";
  fs.writeFileSync(tempPath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tempPath, cachePath);
} catch (error) {
  out.status = "error";
  out.error = error && error.message ? error.message : String(error);
  try {
    fs.mkdirSync(cacheDir, { recursive: true });
    fs.writeFileSync(cachePath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  } catch {
    process.stderr.write(out.error + "\n");
    process.exitCode = 1;
  }
}
