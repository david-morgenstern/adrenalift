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
  const deep = $("#deep-scan").checked;
  const { data } = await postJSON("/api/scan", { workers, deep });
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
// Performance tab (power limit, GFX offset, OD PPT) — all job-based
// ---------------------------------------------------------------------------
// Build a logger that appends to a given <pre> log element.
function mkLog(selector) {
  return (msg) => {
    const el = $(selector);
    if (!el) return;
    el.textContent += (el.textContent ? "\n" : "") + msg;
    el.scrollTop = el.scrollHeight;
  };
}
const perfLog = mkLog("#perf-log");

// Generic "start a background apply job and track it". `progressKey` is an
// optional id prefix for a progress bar; `logFn` defaults to the Performance log.
async function runApplyJob({ url, body, btn, statusEl, busyMsg, progressKey, logFn, onDone }) {
  const log = logFn || perfLog;
  const button = btn ? $(btn) : null;
  const status = statusEl ? $(statusEl) : null;
  if (button) button.disabled = true;
  if (status) status.textContent = busyMsg;
  if (progressKey) {
    $(`#${progressKey}-progress-wrap`).classList.remove("hidden");
    setProgress(progressKey, 0, "");
  }
  log(busyMsg);
  let resp;
  try {
    resp = await postJSON(url, body);
  } catch (e) {
    if (button) button.disabled = false;
    if (status) status.textContent = "Network error.";
    log("Network error.");
    return;
  }
  if (!resp.data.job_id) {
    if (button) button.disabled = false;
    const err = resp.data.error || "Could not start.";
    if (status) status.textContent = err;
    log(err);
    return;
  }
  pollJob(resp.data.job_id, {
    onProgress: (pct, msg) => progressKey && setProgress(progressKey, pct, msg),
    onLog: (l) => log(l),
    onDone: (result) => {
      if (button) button.disabled = false;
      if (progressKey) setProgress(progressKey, 100, "");
      const m = (result && result.message) || "Done.";
      if (status) status.textContent = m;
      log(m);
      if (onDone) onDone(result);
    },
    onError: (err) => {
      if (button) button.disabled = false;
      if (status) status.textContent = err;
      log("Failed: " + err);
    },
  });
}

$("#power-btn").addEventListener("click", () => {
  const watts = parseInt($("#power-watts").value, 10);
  if (!watts) {
    perfLog("Enter a power limit in watts first.");
    return;
  }
  runApplyJob({
    url: "/api/apply/power_limit",
    body: { watts },
    btn: "#power-btn",
    statusEl: "#power-status",
    busyMsg: `Setting power limit to ${watts} W…`,
    progressKey: "power",
  });
});

$("#power-template-btn").addEventListener("click", () => {
  $("#power-watts").value = 340;
  $("#power-btn").click();
});

$("#gfx-offset-btn").addEventListener("click", () => {
  const offset = parseInt($("#gfx-offset").value, 10);
  if (Number.isNaN(offset)) {
    perfLog("Enter a GFX offset in MHz first.");
    return;
  }
  runApplyJob({
    url: "/api/apply/gfx_offset",
    body: { offset },
    btn: "#gfx-offset-btn",
    statusEl: "#gfx-offset-status",
    busyMsg: `Applying GFX offset ${offset >= 0 ? "+" : ""}${offset} MHz…`,
    progressKey: "gfxoff",
  });
});

$("#od-ppt-btn").addEventListener("click", () => {
  const pct = parseInt($("#od-ppt").value, 10);
  if (Number.isNaN(pct)) {
    perfLog("Enter an OD PPT percentage first.");
    return;
  }
  runApplyJob({
    url: "/api/apply/od_ppt",
    body: { pct },
    btn: "#od-ppt-btn",
    statusEl: "#od-ppt-status",
    busyMsg: `Applying OD PPT ${pct >= 0 ? "+" : ""}${pct}%…`,
  });
});

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
// Profiles (ephemeral recipes)
// ---------------------------------------------------------------------------
function gatherPerfSettings() {
  const s = {};
  const pw = parseInt($("#power-watts").value, 10);
  if (pw) s.power_limit_w = pw;
  const bc = parseInt($("#clock").value, 10);
  if (bc) s.boost_clock_mhz = bc;
  const go = parseInt($("#gfx-offset").value, 10);
  if (!Number.isNaN(go) && go !== 0) s.gfx_offset_mhz = go;
  const op = parseInt($("#od-ppt").value, 10);
  if (!Number.isNaN(op) && op !== 0) s.od_ppt_pct = op;
  return s;
}

function fillFormFromProfile(settings) {
  if (!settings) return;
  if (settings.power_limit_w != null) $("#power-watts").value = settings.power_limit_w;
  if (settings.boost_clock_mhz != null) $("#clock").value = settings.boost_clock_mhz;
  if (settings.gfx_offset_mhz != null) $("#gfx-offset").value = settings.gfx_offset_mhz;
  if (settings.od_ppt_pct != null) $("#od-ppt").value = settings.od_ppt_pct;
}

async function refreshProfiles() {
  let d;
  try {
    d = await getJSON("/api/profiles");
  } catch (e) {
    return;
  }
  const sel = $("#profile-select");
  const current = sel.value;
  sel.innerHTML = '<option value="">— saved profiles —</option>';
  (d.profiles || []).forEach((p) => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.name + " (" + (p.sections || []).join(", ") + ")";
    sel.appendChild(o);
  });
  sel.value = current;
}

function wireProfiles() {
  $("#profile-save-btn").addEventListener("click", async () => {
    const name = $("#profile-name").value.trim();
    if (!name) {
      $("#profile-status").textContent = "Enter a profile name first.";
      return;
    }
    const { data } = await postJSON("/api/profiles", { name, settings: gatherPerfSettings() });
    $("#profile-status").textContent = data.error
      ? data.error
      : `Saved profile “${name}”.`;
    if (!data.error) {
      $("#profile-name").value = "";
      await refreshProfiles();
      $("#profile-select").value = name;
    }
  });

  $("#profile-apply-btn").addEventListener("click", () => {
    const name = $("#profile-select").value;
    if (!name) {
      $("#profile-status").textContent = "Pick a saved profile first.";
      return;
    }
    runApplyJob({
      url: "/api/apply/profile",
      body: { name },
      btn: "#profile-apply-btn",
      statusEl: "#profile-status",
      busyMsg: `Applying profile “${name}”…`,
      logFn: perfLog,
    });
  });

  $("#profile-load-btn").addEventListener("click", async () => {
    const name = $("#profile-select").value;
    if (!name) return;
    const prof = await getJSON("/api/profiles/" + encodeURIComponent(name));
    fillFormFromProfile(prof.settings);
    $("#profile-status").textContent = `Loaded “${name}” into the form. Press Apply on each control or use Apply above.`;
  });

  $("#profile-delete-btn").addEventListener("click", async () => {
    const name = $("#profile-select").value;
    if (!name) return;
    await fetch("/api/profiles/" + encodeURIComponent(name), { method: "DELETE" });
    $("#profile-status").textContent = `Deleted “${name}”.`;
    await refreshProfiles();
  });

  $("#profile-export-btn").addEventListener("click", () => {
    const name = $("#profile-select").value;
    if (!name) {
      $("#profile-status").textContent = "Pick a profile to export.";
      return;
    }
    window.location = "/api/profiles/" + encodeURIComponent(name) + "/export";
  });

  $("#profile-import-btn").addEventListener("click", () => $("#profile-import-file").click());
  $("#profile-import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const { data } = await postJSON("/api/profiles/import", payload);
      $("#profile-status").textContent = data.error
        ? data.error
        : `Imported “${data.name}”.`;
      if (!data.error) {
        await refreshProfiles();
        $("#profile-select").value = data.name;
      }
    } catch (err) {
      $("#profile-status").textContent = "Could not read that file as a profile.";
    }
    e.target.value = "";
  });
}

// ---------------------------------------------------------------------------
// OverDrive table editor
// ---------------------------------------------------------------------------
const odLog = mkLog("#od-log");
let odLayout = [];

async function odReadAndRender() {
  const box = $("#od-result");
  box.innerHTML = '<p class="muted">Reading OverDrive table…</p>';
  if (!odLayout.length) {
    try {
      odLayout = (await getJSON("/api/od/layout")).fields || [];
    } catch (e) {
      odLayout = [];
    }
  }
  let r;
  try {
    r = await fetch("/api/od");
  } catch (e) {
    box.innerHTML = errorBox("Network error.");
    return;
  }
  const data = await r.json();
  if (!data.ok) {
    box.innerHTML = errorBox(data.error || "OverDrive table unavailable.");
    return;
  }
  if (!odLayout.length) {
    box.innerHTML = errorBox("OverDrive field list unavailable on this host.");
    return;
  }
  const vals = data.values || {};
  const groups = {};
  odLayout.forEach((f) => {
    (groups[f.group] = groups[f.group] || []).push(f);
  });
  let html = "";
  Object.keys(groups).forEach((g) => {
    html += `<div class="section-title">${escapeHtml(g)}</div><table class="editor">`;
    groups[g].forEach((f) => {
      const cur = vals[f.key];
      html +=
        `<tr data-row="${escapeHtml(f.key)}"><th>${escapeHtml(f.label)}</th>` +
        `<td class="cur">${cur == null ? "—" : cur}${f.unit ? " " + escapeHtml(f.unit) : ""}</td>` +
        `<td><input type="number" class="od-input" value="${cur == null ? 0 : cur}" /></td>` +
        `<td><button class="set-btn od-set" data-key="${escapeHtml(f.key)}">Set</button></td></tr>`;
    });
    html += "</table>";
  });
  box.innerHTML = html;
}

function wireOverdrive() {
  $("#od-read-btn").addEventListener("click", () => {
    $("#od-read-btn").disabled = true;
    odReadAndRender().finally(() => ($("#od-read-btn").disabled = false));
  });
  $("#od-filter").addEventListener("input", (e) => filterRows("#od-result", e.target.value));
  $("#od-result").addEventListener("click", (e) => {
    const btn = e.target.closest(".od-set");
    if (!btn) return;
    const row = btn.closest("tr");
    const value = parseInt(row.querySelector(".od-input").value, 10);
    runApplyJob({
      url: "/api/apply/od_field",
      body: { key: btn.dataset.key, value },
      btn: null,
      statusEl: "#od-status",
      busyMsg: `Setting ${btn.dataset.key} = ${value}…`,
      logFn: odLog,
    });
  });
}

// ---------------------------------------------------------------------------
// PowerPlay table editor
// ---------------------------------------------------------------------------
const ppLog = mkLog("#pp-log");

async function ppLoadAndRender() {
  const box = $("#pp-result");
  box.innerHTML = '<p class="muted">Decoding PowerPlay table…</p>';
  let d;
  try {
    d = await getJSON("/api/pp");
  } catch (e) {
    box.innerHTML = errorBox("Network error.");
    return;
  }
  if (!d.available) {
    box.innerHTML = errorBox(d.reason || "PowerPlay decoding unavailable.");
    return;
  }
  if (!d.fields.length) {
    box.innerHTML = '<p class="muted">No editable PP fields found.</p>';
    return;
  }
  const tunable = d.fields.filter((f) => f.editable).length;
  let html =
    `<p class="muted">${d.fields.length} fields total · ${tunable} editable. ` +
    `Editing patches RAM across all scanned copies (needs a Scan). A reboot restores stock.</p>` +
    `<table class="editor">`;
  d.fields.forEach((f) => {
    const v = f.vbios_value == null ? 0 : f.vbios_value;
    const ro = !f.editable;
    const desc = escapeHtml(f.description || "");
    const hint = escapeHtml(f.input_hint || "");
    html +=
      `<tr data-row="${escapeHtml(f.path)}" data-editable="${f.editable ? 1 : 0}">` +
      `<th title="${desc}">${escapeHtml(f.path)}` +
      (ro ? ' <span class="tag-ro">read-only</span>' : "") +
      `<span class="field-hint">${hint}</span></th>` +
      `<td class="cur">VBIOS: ${escapeHtml(v)} <span class="muted">(${escapeHtml(f.type_label || f.type)})</span></td>` +
      `<td><input type="number" class="pp-input" value="${escapeHtml(v)}" ${ro ? "disabled" : ""} /></td>` +
      `<td>${
        ro
          ? '<span class="muted">—</span>'
          : `<button class="set-btn pp-set" data-offset="${f.offset}" data-type="${escapeHtml(f.type)}">Set</button>`
      }</td></tr>`;
  });
  html += "</table>";
  box.innerHTML = html;
  applyPpVisibility();
}

function applyPpVisibility() {
  const tunableOnly = $("#pp-tunable-only") && $("#pp-tunable-only").checked;
  const term = ($("#pp-filter").value || "").toLowerCase();
  $$("#pp-result tr[data-row]").forEach((tr) => {
    const matchText = tr.dataset.row.toLowerCase().includes(term);
    const matchTun = !tunableOnly || tr.dataset.editable === "1";
    tr.style.display = matchText && matchTun ? "" : "none";
  });
}

function wirePowerplay() {
  $("#pp-read-btn").addEventListener("click", () => {
    $("#pp-read-btn").disabled = true;
    ppLoadAndRender().finally(() => ($("#pp-read-btn").disabled = false));
  });
  $("#pp-filter").addEventListener("input", applyPpVisibility);
  $("#pp-tunable-only").addEventListener("change", applyPpVisibility);
  $("#pp-result").addEventListener("click", (e) => {
    const btn = e.target.closest(".pp-set");
    if (!btn) return;
    const row = btn.closest("tr");
    const raw = row.querySelector(".pp-input").value;
    const value = btn.dataset.type === "f" ? parseFloat(raw) : parseInt(raw, 10);
    runApplyJob({
      url: "/api/apply/pp_field",
      body: { offset: parseInt(btn.dataset.offset, 10), value, type: btn.dataset.type },
      btn: null,
      statusEl: "#pp-status",
      busyMsg: `Patching ${row.dataset.row} = ${value}…`,
      logFn: ppLog,
    });
  });
}

function filterRows(boxSel, term) {
  term = (term || "").toLowerCase();
  $$(`${boxSel} tr[data-row]`).forEach((tr) => {
    tr.style.display = tr.dataset.row.toLowerCase().includes(term) ? "" : "none";
  });
}

// ---------------------------------------------------------------------------
// SMU controls (clock limits, power-saving lock)
// ---------------------------------------------------------------------------
const smuLog = mkLog("#smu-log");

function wireSmu() {
  $("#freq-btn").addEventListener("click", () => {
    runApplyJob({
      url: "/api/apply/freq_limits",
      body: {
        gfx_min: parseInt($("#gfx-min").value, 10) || 0,
        gfx_max: parseInt($("#gfx-max").value, 10) || 0,
      },
      btn: "#freq-btn",
      statusEl: "#freq-status",
      busyMsg: "Applying GFX clock limits…",
      logFn: smuLog,
    });
  });
  const lock = (val) =>
    runApplyJob({
      url: "/api/apply/power_lock",
      body: { lock: val },
      btn: val ? "#lock-btn" : "#unlock-btn",
      statusEl: "#lock-status",
      busyMsg: val ? "Locking power-saving features…" : "Restoring stock power saving…",
      logFn: smuLog,
    });
  $("#lock-btn").addEventListener("click", () => lock(true));
  $("#unlock-btn").addEventListener("click", () => lock(false));
}

// ---------------------------------------------------------------------------
// Escape (D3DKMTEscape OD8)
// ---------------------------------------------------------------------------
const escLog = mkLog("#esc-log");

function wireEscape() {
  $("#esc-btn").addEventListener("click", () => {
    runApplyJob({
      url: "/api/apply/escape",
      body: {
        clock: parseInt($("#esc-clock").value, 10) || 0,
        power: parseInt($("#esc-power").value, 10) || 0,
        offset: parseInt($("#esc-offset").value, 10) || 0,
      },
      btn: "#esc-btn",
      statusEl: "#esc-status",
      busyMsg: "Applying OD via D3DKMTEscape…",
      logFn: escLog,
    });
  });
}

// ---------------------------------------------------------------------------
// System — registry tweaks (persistent, quarantined)
// ---------------------------------------------------------------------------
const regLog = mkLog("#reg-log");

async function regRead() {
  const box = $("#reg-result");
  box.innerHTML = '<p class="muted">Reading…</p>';
  let d;
  try {
    d = await getJSON("/api/registry");
  } catch (e) {
    box.innerHTML = errorBox("Network error.");
    return;
  }
  const st = d.status || {};
  if (!st.available) {
    box.innerHTML = errorBox(st.reason || "Registry tweaks unavailable.");
    return;
  }
  let html = `<p class="muted">Adapter: ${escapeHtml(st.adapter || "AMD GPU")}</p>`;
  const v = st.values || {};
  ["patch", "verify", "extra"].forEach((grp) => {
    const entries = v[grp] || {};
    const keys = Object.keys(entries);
    if (!keys.length) return;
    html += `<div class="section-title">${escapeHtml(grp)}</div>`;
    html += kvTable(
      keys.map((k) => [
        entries[k].desc || k,
        entries[k].current == null ? "(unset)" : entries[k].current,
      ])
    );
  });
  box.innerHTML = html;
}

function wireSystem() {
  $("#reg-read-btn").addEventListener("click", () => {
    $("#reg-read-btn").disabled = true;
    regRead().finally(() => ($("#reg-read-btn").disabled = false));
  });
  $("#reg-apply-btn").addEventListener("click", () => {
    runApplyJob({
      url: "/api/registry/apply",
      body: {},
      btn: "#reg-apply-btn",
      statusEl: "#reg-status",
      busyMsg: "Applying recommended registry tweaks…",
      logFn: regLog,
      onDone: () => regRead(),
    });
  });
  $("#reg-restore-btn").addEventListener("click", () => {
    runApplyJob({
      url: "/api/registry/restore",
      body: {},
      btn: "#reg-restore-btn",
      statusEl: "#reg-status",
      busyMsg: "Restoring registry from backup…",
      logFn: regLog,
      onDone: () => regRead(),
    });
  });
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
      const summary = state.vbios.summary || "";
      $("#vbios-line").textContent = summary;
      const pv = $("#perf-vbios-line");
      if (pv) pv.textContent = summary;
    }
    if (state.last_scan && state.last_scan.ready_to_apply) {
      scanReady = true;
      $("#apply-btn").disabled = false;
      $("#scan-status").textContent = state.last_scan.message;
    }
  } catch (e) {
    /* ignore */
  }

  wireProfiles();
  wireOverdrive();
  wirePowerplay();
  wireSmu();
  wireEscape();
  wireSystem();
  refreshProfiles();
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
