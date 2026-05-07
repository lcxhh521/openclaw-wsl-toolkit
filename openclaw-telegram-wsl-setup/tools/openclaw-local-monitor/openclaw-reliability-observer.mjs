#!/usr/bin/env node
import fs from "fs";
import os from "os";
import path from "path";
import { spawnSync } from "child_process";

const home = os.homedir();
const cacheDir = path.join(home, ".openclaw", "monitor-cache");
const cachePath = path.join(cacheDir, "reliability-status.json");
const stabilityDir = path.join(home, ".openclaw", "logs", "stability");
const now = Date.now();
const windowMinutes = Math.max(15, Number(process.env.OPENCLAW_RELIABILITY_WINDOW_MINUTES || 120));
const windowMs = windowMinutes * 60 * 1000;
const maxBytes = Math.max(65536, Number(process.env.OPENCLAW_RELIABILITY_LOG_BYTES || 524288));

const out = {
  generatedAt: new Date(now).toISOString(),
  status: "ok",
  source: "local-log-observer",
  windowMinutes,
  summary: "最近未发现可靠性问题。",
  latest: null,
  counts: {},
  events: [],
  filesScanned: [],
  notes: [
    "read-only; no Telegram send/retry",
    "does not connect to gateway",
    "read by OpenClaw local monitor"
  ]
};

function redact(text) {
  return String(text || "")
    .replace(/https:\/\/api\.telegram\.org\/bot[^/"'\\\s]+/gi, "https://api.telegram.org/bot[REDACTED]")
    .replace(/(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}/gi, "$1[REDACTED]")
    .replace(/\b(bot|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*["']?[^"',\s]+/gi, "$1=[REDACTED]")
    .replace(/\b(gho|ghp|github_pat|sk|rk|vk)-?[A-Za-z0-9_]{20,}\b/g, "[REDACTED]")
    .replace(/\b\d{8,12}:[A-Za-z0-9_-]{30,}\b/g, "[REDACTED]");
}

function safeReaddir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function localDateName(offsetDays = 0) {
  const d = new Date(now - offsetDays * 24 * 60 * 60 * 1000);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function readTail(filePath) {
  try {
    const stat = fs.statSync(filePath);
    const size = stat.size;
    const start = Math.max(0, size - maxBytes);
    const fd = fs.openSync(filePath, "r");
    try {
      const buffer = Buffer.alloc(size - start);
      fs.readSync(fd, buffer, 0, buffer.length, start);
      out.filesScanned.push(filePath);
      return buffer.toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

function journalText() {
  try {
    const result = spawnSync(
      "journalctl",
      ["--user", "-u", "openclaw-gateway.service", "--since", `${windowMinutes} minutes ago`, "--no-pager", "-o", "short-iso"],
      { encoding: "utf8", timeout: 3000, maxBuffer: maxBytes }
    );
    if (result.stdout) {
      out.filesScanned.push("journalctl:openclaw-gateway.service");
      return result.stdout;
    }
  } catch {
  }
  return "";
}

function parseTimeMs(line) {
  const m = String(line).match(/(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)/);
  if (!m) return 0;
  let text = m[1].replace(" ", "T");
  text = text.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function classify(line) {
  const raw = redact(line).replace(/\s+/g, " ").trim();
  const lower = raw.toLowerCase();
  if (!raw) return null;

  if (lower.includes("server_is_overloaded") || lower.includes("service_unavailable_error") || lower.includes("servers are currently overloaded")) {
    return { kind: "model_overloaded", severity: "risk", summary: "模型服务过载，回复生成失败。" };
  }
  if (lower.includes("context overflow") || lower.includes("context_length_exceeded") || lower.includes("maximum context")) {
    return { kind: "context_overflow", severity: "risk", summary: "上下文溢出，运行被阻断。" };
  }
  if (lower.includes("session file locked") || lower.includes("session lock")) {
    return { kind: "session_lock", severity: "risk", summary: "会话锁阻塞或拖慢运行。" };
  }
  if (lower.includes("final reply failed") || lower.includes("sendmessage failed") || lower.includes("delivery failed")) {
    return { kind: "telegram_delivery_failed", severity: "risk", summary: "Telegram 最终回复发送失败。" };
  }
  if (lower.includes("message processing failed")) {
    return { kind: "telegram_processing_failed", severity: "risk", summary: "Telegram 消息处理失败。" };
  }
  if (lower.includes("sendchataction failed")) {
    return { kind: "telegram_action_failed", severity: "warn", summary: "Telegram 输入状态/动作请求失败。" };
  }
  if (lower.includes("[fetch-timeout]") || lower.includes("fetch timeout") || lower.includes("fetch-timeout") || lower.includes("network request") && lower.includes("failed")) {
    return { kind: "network_or_provider_failure", severity: "warn", summary: "网络或模型供应商请求失败/超时。" };
  }
  if (lower.includes("gateway.stop_shutdown_timeout") || lower.includes("stop_shutdown_timeout")) {
    return { kind: "gateway_shutdown_timeout", severity: "risk", summary: "Gateway 优雅停机超时。" };
  }
  if (lower.includes("startup_failed")) {
    return { kind: "gateway_startup_failed", severity: "risk", summary: "Gateway 启动失败。" };
  }
  if (lower.includes("sigkill") || lower.includes("sigterm") || lower.includes("signal") && lower.includes("killed")) {
    return { kind: "gateway_lifecycle_signal", severity: "warn", summary: "Gateway 进程收到生命周期信号。" };
  }
  return null;
}

function addEvent(line, source, fallbackMs = 0) {
  const item = classify(line);
  if (!item) return;
  const atMs = parseTimeMs(line) || fallbackMs || now;
  if (now - atMs > windowMs) return;
  const raw = redact(line).replace(/\s+/g, " ").trim();
  out.events.push({
    at: new Date(atMs).toISOString(),
    ageMs: Math.max(0, now - atMs),
    source,
    kind: item.kind,
    severity: item.severity,
    summary: item.summary,
    line: raw.slice(0, 260)
  });
  out.counts[item.kind] = (out.counts[item.kind] || 0) + 1;
}

function collectTextLogs() {
  const texts = [];
  const direct = [
    path.join("/tmp", "openclaw", `openclaw-${localDateName(0)}.log`),
    path.join("/tmp", "openclaw", `openclaw-${localDateName(1)}.log`)
  ];
  for (const file of direct) {
    const text = readTail(file);
    if (text) texts.push({ source: file, text });
  }
  for (const entry of safeReaddir(path.join("/tmp", "openclaw"))) {
    if (!entry.isFile() || !/^openclaw-.*\.log$/.test(entry.name)) continue;
    const file = path.join("/tmp", "openclaw", entry.name);
    if (direct.includes(file)) continue;
    try {
      const stat = fs.statSync(file);
      if (now - stat.mtimeMs > windowMs) continue;
      const text = readTail(file);
      if (text) texts.push({ source: file, text });
    } catch {
    }
  }
  const journal = journalText();
  if (journal) texts.push({ source: "journalctl", text: journal });
  return texts;
}

function collectStabilityFiles() {
  const files = safeReaddir(stabilityDir)
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => {
      const file = path.join(stabilityDir, entry.name);
      try {
        const stat = fs.statSync(file);
        return { file, name: entry.name, mtimeMs: stat.mtimeMs };
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => b.mtimeMs - a.mtimeMs)
    .slice(0, 5);

  for (const item of files) {
    const name = item.name;
    const reason = name.match(/gateway\.([^.]+)\.json$/)?.[1] || name;
    const text = `${new Date(item.mtimeMs).toISOString()} ${reason} ${name}`;
    addEvent(text, "stability", item.mtimeMs);
  }
}

try {
  fs.mkdirSync(cacheDir, { recursive: true });
  for (const block of collectTextLogs()) {
    for (const line of block.text.split(/\r?\n/)) addEvent(line, block.source);
  }
  collectStabilityFiles();

  out.events.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const unique = [];
  const seen = new Set();
  for (const event of out.events) {
    const key = `${event.kind}|${event.at.slice(0, 16)}|${event.summary}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(event);
  }
  out.events = unique.slice(0, 24);
  out.latest = out.events[0] || null;

  const hasRisk = out.events.some((event) => event.severity === "risk");
  const hasWarn = out.events.some((event) => event.severity === "warn");
  const model = out.counts.model_overloaded || 0;
  const delivery = (out.counts.telegram_delivery_failed || 0) + (out.counts.telegram_processing_failed || 0);
  if (model && delivery) {
    out.status = "risk";
    out.summary = "最近同时观察到模型过载和 Telegram 回传失败，用户侧可能表现为静默不回。";
  } else if (hasRisk) {
    out.status = "risk";
    out.summary = out.latest ? out.latest.summary : "最近观察到可靠性高风险事件。";
  } else if (hasWarn) {
    out.status = "warn";
    out.summary = out.latest ? out.latest.summary : "最近观察到可靠性提醒。";
  }

  const tempPath = cachePath + ".tmp";
  fs.writeFileSync(tempPath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tempPath, cachePath);
} catch (error) {
  out.status = "error";
  out.summary = "可靠性 observer 执行失败。";
  out.error = error && error.message ? error.message : String(error);
  try {
    fs.mkdirSync(cacheDir, { recursive: true });
    fs.writeFileSync(cachePath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  } catch {
    process.stderr.write(out.error + "\n");
    process.exitCode = 1;
  }
}
