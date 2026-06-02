"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return { status: r.status, data: await r.json() };
}

let TOOLTIPS = {};
let scanReady = false;

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("#tab-" + tab.dataset.tab).classList.add("active");
  });
});

// ---------------------------------------------------------------------------
// Tooltip popovers (the "extend tooltips & descriptions" feature)
// ---------------------------------------------------------------------------
const popover = $("#popover");
const popoverBody = popover.querySelector(".popover-body");

function showPopover(anchor) {
  const key = anchor.dataset.help;
  const t = TOOLTIPS[key];
  if (!t) return;
  popoverBody.innerHTML =
    (t.tip ? `<p><b>${t.tip}</b></p>` : "") +
    (t.description ? `<p>${t.description}</p>` : "");
  popover.classList.remove("hidden");
  const rect = anchor.getBoundingClientRect();
  let left = rect.left;
  let top = rect.bottom + 8;
  // keep on-screen
  const pw = popover.offsetWidth;
  if (left + pw > window.innerWidth - 12) left = window.innerWidth - pw - 12;
  if (left < 12) left = 12;
  popover.style.left = left + "px";
  popover.style.top = top + "px";
}
function hidePopover() {
  popover.classList.add("hidden");
}
popover.querySelector(".popover-close").addEventListener("click", hidePopover);

function wireHelpAnchors() {
  $$(".help-anchor").forEach((a) => {
    const t = TOOLTIPS[a.dataset.help];
    if (t && t.tip) a.title = t.tip; // native hover tooltip too
    a.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!popover.classList.contains("hidden") && popover.dataset.key === a.dataset.help) {
        hidePopover();
        return;
      }
      popover.dataset.key = a.dataset.help;
      showPopover(a);
    });
  });
}
document.addEventListener("click", (e) => {
  if (!popover.contains(e.target) && !e.target.classList.contains("help-anchor")) {
    hidePopover();
  }
});

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
function logLine(msg) {
  const el = $("#log");
  el.textContent += (el.textContent ? "\n" : "") + msg;
  el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------------------
// Job polling
// ---------------------------------------------------------------------------
function pollJob(jobId, { onProgress, onLog, onDone, onError }) {
  let since = 0;
  const timer = setInterval(async () => {
    let snap;
    try {
      snap = await getJSON(`/api/job/${jobId}?since=${since}`);
    } catch (e) {
      return; // transient; try again
    }
    // 404 / unknown-job response: {"error": ...} with no job fields at all.
    // (A finished-but-failed job instead has status === "error", handled below.)
    if (snap.status === undefined) {
      clearInterval(timer);
      onError && onError(snap.error || "Job not found.");
      return;
    }
    since = snap.log_total || since;
    (snap.log || []).forEach((l) => onLog && onLog(l));
    onProgress && onProgress(snap.progress, snap.message);
    if (snap.status === "done") {
      clearInterval(timer);
      onDone && onDone(snap.result);
    } else if (snap.status === "error") {
      clearInterval(timer);
      onError && onError(snap.error || "Operation failed.");
    }
  }, 1000);
}

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------
$("#scan-btn").addEventListener("click", async () => {
  const btn = $("#scan-btn");
  btn.disabled = true;
  $("#scan-status").textContent = "Scanning\u2026";
  $("#scan-progress-wrap").classList.remove("hidden");
  setProgress("scan", 0, "");
  const workers = parseInt($("#workers").value, 10) || 0;
  const { data } = await postJSON("/api/scan", { workers });
  if (!data.job_id) {
    $("#scan-status").textContent = data.error || "Could not start scan.";
    btn.disabled = false;
    return;
  }
  logLine("Scan started\u2026");
  pollJob(data.job_id, {
    onProgress: (pct, msg) => setProgress("scan", pct, msg),
    onLog: (l) => logLine(l),
    onDone: (result) => {
      btn.disabled = false;
      scanReady = result && result.ready_to_apply;
      $("#apply-btn").disabled = !scanReady;
      $("#scan-status").textContent = (result && result.message) || "Scan complete.";
      setProgress("scan", 100, "");
    },
    onError: (err) => {
      btn.disabled = false;
      $("#scan-status").textContent = "Scan failed.";
      logLine("Scan failed: " + err);
    },
  });
});

// ---------------------------------------------------------------------------
// Apply boost clock
// ---------------------------------------------------------------------------
$("#apply-btn").addEventListener("click", async () => {
  const btn = $("#apply-btn");
  const clock = parseInt($("#clock").value, 10);
  if (!clock) {
    logLine("Enter a boost clock in MHz first.");
    return;
  }
  btn.disabled = true;
  $("#apply-progress-wrap").classList.remove("hidden");
  setProgress("apply", 0, "");
  logLine(`Applying ${clock} MHz\u2026`);
  const { data } = await postJSON("/api/apply/simple", { clock });
  if (!data.job_id) {
    logLine(data.error || "Could not start apply.");
    btn.disabled = false;
    return;
  }
  pollJob(data.job_id, {
    onProgress: (pct, msg) => setProgress("apply", pct, msg),
    onLog: (l) => logLine(l),
    onDone: (result) => {
      btn.disabled = !scanReady;
      setProgress("apply", 100, "");
      logLine((result && result.message) || "Apply complete.");
    },
    onError: (err) => {
      btn.disabled = !scanReady;
      logLine("Apply failed: " + err);
    },
  });
});

function setProgress(which, pct, msg) {
  $(`#${which}-progress`).style.width = (pct || 0) + "%";
  $(`#${which}-progress-label`).textContent = msg || "";
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------
$("#status-btn").addEventListener("click", async () => {
  const box = $("#status-result");
  box.innerHTML = '<p class="muted">Reading\u2026</p>';
  let r;
  try {
    r = await fetch("/api/status");
  } catch (e) {
    box.innerHTML = errorBox("Network error.");
    return;
  }
  const data = await r.json();
  if (!data.ok) {
    box.innerHTML = errorBox(data.error || "Status unavailable.");
    return;
  }
  let rows = [
    ["SMU version", data.smu_version],
    ["Driver interface", data.smu_drv_if],
    ["Power (PPT) limit", data.ppt_limit != null ? data.ppt_limit + " W" : "\u2014"],
    ["Voltage", data.voltage != null ? data.voltage + " mV" : "\u2014"],
    ["DMA buffer", data.dma_available ? "available" : "not located"],
  ];
  let html = kvTable(rows);
  if (Array.isArray(data.dpm_ranges) && data.dpm_ranges.length) {
    html += '<div class="section-title">Clock ranges (MHz)</div>';
    html += kvTable(
      data.dpm_ranges.map((d) =>
        d.error ? [d.name, "error"] : [d.name, `${d.min} \u2013 ${d.max}`]
      )
    );
  }
  box.innerHTML = html;
});

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------
let metricsTimer = null;
let metricsLayout = [];

async function loadMetricsLayout() {
  try {
    const d = await getJSON("/api/metrics/layout");
    metricsLayout = d.sections || [];
  } catch (e) {
    metricsLayout = [];
  }
}

async function readMetrics() {
  const box = $("#metrics-result");
  let r;
  try {
    r = await fetch("/api/metrics");
  } catch (e) {
    box.innerHTML = errorBox("Network error.");
    return;
  }
  const data = await r.json();
  if (!data.ok) {
    box.innerHTML = errorBox(data.error || "Metrics unavailable.");
    stopAuto();
    return;
  }
  const m = data.metrics || {};
  let html = "";
  const sections = metricsLayout.length
    ? metricsLayout
    : [{ title: "Metrics", keys: Object.keys(m) }];
  sections.forEach((sec) => {
    const rows = sec.keys
      .filter((k) => k in m)
      .map((k) => [k, formatMetric(m[k])]);
    if (!rows.length) return;
    html += `<div class="section-title">${sec.title}</div>` + kvTable(rows);
  });
  box.innerHTML = html || '<p class="muted">No metrics returned.</p>';
}

function formatMetric(v) {
  if (v == null) return "\u2014";
  if (typeof v === "number" && !Number.isInteger(v)) return v.toFixed(2);
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

$("#metrics-btn").addEventListener("click", readMetrics);
$("#metrics-auto").addEventListener("change", (e) => {
  if (e.target.checked) {
    readMetrics();
    metricsTimer = setInterval(readMetrics, 2000);
  } else {
    stopAuto();
  }
});
function stopAuto() {
  if (metricsTimer) clearInterval(metricsTimer);
  metricsTimer = null;
  const cb = $("#metrics-auto");
  if (cb) cb.checked = false;
}

// ---------------------------------------------------------------------------
// Help
// ---------------------------------------------------------------------------
async function loadHelp() {
  const d = await getJSON("/api/help");
  const list = $("#help-list");
  list.innerHTML = "";
  (d.pages || []).forEach((page, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="t">${page.title}</span><span class="s">${page.summary}</span>`;
    li.addEventListener("click", () => {
      $$("#help-list li").forEach((x) => x.classList.remove("active"));
      li.classList.add("active");
      $("#help-body").innerHTML = page.html || '<p class="muted">No content.</p>';
    });
    list.appendChild(li);
    if (i === 0) li.click();
  });
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------
function kvTable(rows) {
  return (
    '<table class="kv">' +
    rows
      .map(
        ([k, v]) =>
          `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v == null ? "\u2014" : v)}</td></tr>`
      )
      .join("") +
    "</table>"
  );
}
function errorBox(msg) {
  return `<div class="error-box">${escapeHtml(msg)}</div>`;
}
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function init() {
  try {
    const tip = await getJSON("/api/tooltips");
    TOOLTIPS = tip.tooltips || {};
  } catch (e) {
    TOOLTIPS = {};
  }
  wireHelpAnchors();

  try {
    const state = await getJSON("/api/state");
    renderBanner(state);
    if (state.vbios) {
      $("#vbios-line").textContent = state.vbios.summary || "";
    }
    if (state.last_scan && state.last_scan.ready_to_apply) {
      scanReady = true;
      $("#apply-btn").disabled = false;
      $("#scan-status").textContent = state.last_scan.message;
    }
  } catch (e) {
    /* ignore */
  }

  loadMetricsLayout();
  loadHelp();
}

function renderBanner(state) {
  const banner = $("#banner");
  if (!state.engine) return;
  if (state.engine.available) {
    banner.className = "banner ok";
    banner.textContent = "GPU engine ready. Scan, then apply your boost clock.";
  } else {
    banner.className = "banner warn";
    banner.textContent =
      "Hardware actions are unavailable on this host (" +
      (state.engine.platform || "unknown") +
      "). You can still browse the UI and help. " +
      "Run on Windows as Administrator to enable overclocking.";
  }
  banner.classList.remove("hidden");
}

init();
