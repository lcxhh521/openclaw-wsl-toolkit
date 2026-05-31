(function () {
  "use strict";

  const endpoint = "http://127.0.0.1:18793/";
  let lastSignature = "";
  let lastSentAt = 0;

  function lineText() {
    return (document.body && document.body.innerText || "").replace(/\r/g, "\n");
  }

  function pickReset(fragment) {
    const m = fragment.match(/([0-9]{1,2}[^\n]{0,20}后刷新)/);
    return m ? m[1].trim() : "";
  }

  function extract() {
    const text = lineText();
    if (!/Coding Plan|套餐用量限额|开通管理|近1周|近1月/.test(text)) return null;
    const compact = text.replace(/[ \t]+/g, " ");
    const windows = [];

    const five = compact.match(/([0-9]+(?:\.[0-9]+)?)%\s*\/\s*([^%\n]{0,80}?后刷新)(?:\s*抵达限额)?/);
    if (five) windows.push({ label: "5h", used_percent: Number(five[1]), reset_text: pickReset(five[2]) });

    const week = compact.match(/([0-9]+(?:\.[0-9]+)?)%\s*\/\s*近1周\s*[（(]?([^%\n)]{0,80}?后刷新)[）)]?/);
    if (week) windows.push({ label: "Week", used_percent: Number(week[1]), reset_text: pickReset(week[2]) });

    const month = compact.match(/([0-9]+(?:\.[0-9]+)?)%\s*\/\s*近1月\s*[（(]?([^%\n)]{0,80}?后刷新)[）)]?/);
    if (month) windows.push({ label: "Month", used_percent: Number(month[1]), reset_text: pickReset(month[2]) });

    if (!windows.length) return null;
    return {
      source: "volcengine_console_dom_bridge",
      observed_at: new Date().toISOString(),
      windows
    };
  }

  async function sendIfChanged() {
    const payload = extract();
    if (!payload) return;
    const signature = JSON.stringify(payload.windows);
    const now = Date.now();
    if (signature === lastSignature && now - lastSentAt < 60000) return;
    lastSignature = signature;
    lastSentAt = now;
    try {
      await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        mode: "cors"
      });
    } catch (_) {
      // Keep silent in the console page; the local service status records failures.
    }
  }

  setInterval(sendIfChanged, 15000);
  setTimeout(sendIfChanged, 2000);
})();
