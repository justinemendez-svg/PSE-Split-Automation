/* Japan Overlay AE Review — static viewer.
 * Reads data.json (produced by refresh.py, flags already baked in) and renders
 * the OK / Needs Mari / Someone-else worklist. Rendering only — no writes,
 * no re-classification (refresh.py already did that; the App Foundry flavor is
 * the one that re-derives).
 */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";

const state = { rows: [], bucket: "", type: "", segment: "", q: "", sortKey: "closeDate", sortDir: -1 };

// When served by serve_local.py the button can WRITE to Salesforce (preview →
// confirm → write). On plain GitHub Pages there's no server, so it stays a
// deep link. Detected once at load via /api/health.
const writeApi = { enabled: false, org: null, person: null, splitReady: false };

async function detectWriteApi() {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    if (!res.ok) return;
    const h = await res.json();
    writeApi.enabled = !!h.writeEnabled;
    writeApi.org = h.org;
    writeApi.person = h.person;
    writeApi.splitReady = !!h.splitReady;
  } catch (e) { /* no local server — deep-link mode */ }
}

const $ = (id) => document.getElementById(id);

function fmtAmount(n) {
  if (n == null || n === "") return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}
function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s + "T00:00:00");
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" });
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function load() {
  try {
    await detectWriteApi();
    const res = await fetch("data.json?_=" + Date.now());
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    hydrate(data);
  } catch (e) {
    const s = $("status");
    s.className = "status err";
    s.textContent =
      "Couldn't load data.json — " + e.message +
      ". Run `python3 refresh.py`, then serve this folder (open serve.command) rather than opening the file directly.";
  }
}

function hydrate(data) {
  state.rows = data.rows || [];
  const sm = data.summary || {};
  $("cTotal").textContent = sm.total ?? state.rows.length;
  $("cOk").textContent = sm.ok ?? 0;
  $("cMissing").textContent = sm.missing ?? 0;
  if ($("cSomeone")) $("cSomeone").textContent = sm.someoneElse ?? 0;
  $("cPeriod").textContent = (data.period && data.period.label) || "—";
  if (data.config && data.config.overlayPerson) {
    window.__overlayPerson = data.config.overlayPerson;
    if ($("hPerson")) $("hPerson").textContent = data.config.overlayPerson;
  }
  if ($("tabActionCount")) $("tabActionCount").textContent = state.rows.filter((r) => r.bucket === "missing").length;

  const gen = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = gen ? "Refreshed " + gen.toLocaleString("en-US") : "";

  // Populate the type filter from the data actually present.
  const types = [...new Set(state.rows.map((r) => r.type).filter(Boolean))].sort();
  const sel = $("typeFilter");
  for (const t of types) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t; sel.appendChild(o);
  }

  const segments = [...new Set(state.rows.map((r) => r.segment).filter(Boolean))].sort();
  const segSel = $("segmentFilter");
  for (const s of segments) {
    const o = document.createElement("option");
    o.value = s; o.textContent = s; segSel.appendChild(o);
  }

  // Admin "Refresh data" — only when served by the local server (which can run
  // refresh.py). Never shows on the static GitHub Pages copy.
  if (writeApi.enabled && $("refreshBtn")) {
    $("refreshBtn").classList.remove("hidden");
  }

  $("status").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  wire();
  render();
}

// Admin: re-pull fresh data from Salesforce (local server runs refresh.py),
// then reload data.json in place.
async function refreshData() {
  const btn = $("refreshBtn");
  const msg = $("refreshMsg");
  const ok = window.confirm(
    "Re-pull the latest Japan overlay opps from Salesforce (" + (writeApi.org || "prod") + ")?\n\n" +
    "This reads Salesforce (no writes) and rebuilds the worklist. Takes up to a minute.");
  if (!ok) return;

  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Refreshing…";
  msg.className = "sub";
  msg.textContent = "Pulling from Salesforce…";
  try {
    const res = await fetch("/api/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const out = await res.json();
    if (!res.ok || !out.ok) throw new Error(out.error || ("HTTP " + res.status));
    const s = out.summary || {};
    const per = (out.period && out.period.label) ? " · " + out.period.label : "";
    msg.className = "sub ok";
    msg.textContent = "Updated: " + (s.total ?? "?") + " opps · " + (s.ok ?? 0) +
      " OK · " + (s.missing ?? 0) + " need Mari" + per;
    await reloadData();
  } catch (e) {
    msg.className = "sub err";
    msg.textContent = "Refresh failed — " + esc(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// Re-fetch data.json and re-render the current view without a full page reload.
async function reloadData() {
  const res = await fetch("data.json?_=" + Date.now(), { cache: "no-store" });
  if (!res.ok) throw new Error("couldn’t reload data.json (HTTP " + res.status + ")");
  const data = await res.json();
  state.rows = data.rows || [];
  const sm = data.summary || {};
  $("cTotal").textContent = sm.total ?? state.rows.length;
  $("cOk").textContent = sm.ok ?? 0;
  $("cMissing").textContent = sm.missing ?? 0;
  if ($("cSomeone")) $("cSomeone").textContent = sm.someoneElse ?? 0;
  $("cPeriod").textContent = (data.period && data.period.label) || "—";
  if ($("tabActionCount")) $("tabActionCount").textContent =
    state.rows.filter((r) => r.bucket === "missing").length;
  const gen = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = gen ? "Refreshed " + gen.toLocaleString("en-US") : "";
  refreshView();
}

// Re-render whichever tab is currently showing (filters are shared across both).
function refreshView() {
  const onAction = !$("panelAction").classList.contains("hidden");
  if (onAction) renderAction(); else render();
}

function wire() {
  $("search").addEventListener("input", (e) => { state.q = e.target.value.toLowerCase().trim(); refreshView(); });
  $("typeFilter").addEventListener("change", (e) => { state.type = e.target.value; refreshView(); });
  $("segmentFilter").addEventListener("change", (e) => { state.segment = e.target.value; refreshView(); });
  $("csvBtn").addEventListener("click", exportCsv);
  if ($("refreshBtn")) $("refreshBtn").addEventListener("click", refreshData);

  document.querySelectorAll(".card.click").forEach((card) => {
    card.addEventListener("click", () => {
      state.bucket = card.dataset.filter || "";
      document.querySelectorAll(".card.click").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      render();
    });
  });

  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = 1; }
      render();
    });
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  $("panelReview").classList.toggle("hidden", name !== "review");
  $("panelAction").classList.toggle("hidden", name !== "action");
  if (name === "action") renderAction();
}

// Opps still needing Mari — the actionable worklist with the exact SFDC steps.
// Applies the shared type/segment/search filters (bucket is forced to "missing").
function toUpdate() {
  let rows = state.rows.filter((r) => r.bucket === "missing");
  if (state.type) rows = rows.filter((r) => r.type === state.type);
  if (state.segment) rows = rows.filter((r) => r.segment === state.segment);
  if (state.q) {
    rows = rows.filter((r) =>
      [r.name, r.account, r.owner, r.id, r.reason, (r.overlayTeamMembers || []).join(" ")]
        .some((v) => String(v == null ? "" : v).toLowerCase().includes(state.q)));
  }
  return rows;
}

function renderAction() {
  const rows = toUpdate();
  const list = $("actionList");
  const person = (window.__overlayPerson) || "Mari Takiyama";
  list.innerHTML = rows.map((r) => {
    const link = r.id ? SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + r.id + "/view" : null;
    const title = link
      ? '<a class="opp-link" href="' + link + '" target="_blank" rel="noopener">' + esc(r.name) + "</a>"
      : esc(r.name);
    // Which place(s) are missing — team role drives the "Add Opportunity Team Member" action.
    const needTeam = !r.mariOnTeam;
    const needSplit = !r.mariOnSplit;
    const teamStep =
      '<div class="step"><b>1 · Add Opportunity Team Member</b>' +
      '<div class="meta">Opportunity → Opportunity Team → Add Members</div>' +
      '<table class="kv"><tbody>' +
      '<tr><td class="k2">User</td><td class="v2">' + esc(person) + "</td></tr>" +
      '<tr><td class="k2">Team Role</td><td class="v2">AE Overlay</td></tr>' +
      '<tr><td class="k2">Opportunity Access</td><td class="v2">Read Only</td></tr>' +
      "</tbody></table>" +
      (needTeam ? "" : '<div class="also">Already tagged on the team — no team change needed.</div>') +
      "</div>";
    const splitStep =
      '<div class="step"><b>2 · Add Overlay split</b>' +
      '<div class="meta">Opportunity → Opportunity Splits → Edit Splits</div>' +
      '<table class="kv"><tbody>' +
      '<tr><td class="k2">Split Owner</td><td class="v2">' + esc(person) + "</td></tr>" +
      '<tr><td class="k2">Split Type</td><td class="v2">Overlay</td></tr>' +
      "</tbody></table>" +
      (needSplit ? "" : '<div class="also">Overlay split already set — no split change needed.</div>') +
      "</div>";
    // Two modes for the button:
    //  • local server present → auto-write (preview → confirm → write)
    //  • static GitHub Pages  → deep link to the Opportunity Team related list
    let updateBtn = "";
    if (r.id && writeApi.enabled) {
      updateBtn =
        '<button class="btn-sf" data-write="' + esc(r.id) + '" ' +
        'title="Preview, then write Mari to this opp in ' + esc(writeApi.org) + '">' +
        "Update in Salesforce →</button>";
    } else if (r.id) {
      const teamUrl = SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + r.id +
        "/related/OpportunityTeamMembers/view";
      updateBtn =
        '<a class="btn-sf" href="' + teamUrl + '" target="_blank" rel="noopener" ' +
        'title="Opens this opp’s Opportunity Team in Salesforce so you can add Mari">Update in Salesforce →</a>';
    }
    return (
      '<div class="action-card" data-card="' + esc(r.id || "") + '">' +
      '<div class="action-head">' +
        '<div class="head-main">' +
          '<div class="opp-title">' + title + "</div>" +
          '<div class="opp-id">' + esc(r.id || "") + "</div>" +
          '<div class="meta">' + esc(r.account || "—") + " · " + esc(r.owner || "—") +
            " · " + esc(r.segment || "—") + " · " + esc(r.type || "—") + "</div>" +
          '<div class="reason">' + esc(r.reason) + "</div>" +
          '<div class="write-msg" data-msg="' + esc(r.id || "") + '"></div>' +
        "</div>" +
        updateBtn +
      "</div>" +
      '<div class="action-body">' + teamStep + splitStep + "</div>" +
      "</div>"
    );
  }).join("");

  // Wire the auto-write buttons (only present in local-server mode).
  list.querySelectorAll("button[data-write]").forEach((btn) => {
    btn.addEventListener("click", () => startWrite(btn.dataset.write, btn));
  });

  // "Update all" bar — only in local-server (write) mode, and only when the
  // current filtered view actually has opps to update.
  const bar = $("bulkBar");
  if (bar) {
    const showBar = writeApi.enabled && rows.length > 0;
    bar.classList.toggle("hidden", !showBar);
    if (showBar) {
      $("bulkCount").textContent = rows.length;
      const allBtn = $("updateAllBtn");
      allBtn.disabled = false;
      allBtn.textContent = "Update all " + rows.length + " →";
      $("bulkMsg").textContent = "";
      // Snapshot the exact ids in this view so filters scope the bulk write.
      allBtn.onclick = () => updateAll(rows.map((r) => r.id).filter(Boolean));
    }
  }
  $("actionEmpty").classList.toggle("hidden", rows.length !== 0);
  list.classList.toggle("hidden", rows.length === 0);
}

// --- Auto-write flow (local server only): preview → confirm → write ----------
function setMsg(oppId, html, cls) {
  const el = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
  if (el) { el.className = "write-msg" + (cls ? " " + cls : ""); el.innerHTML = html; }
}

async function startWrite(oppId, btn) {
  btn.disabled = true;
  setMsg(oppId, "Checking Salesforce…", "");
  let plan;
  try {
    const res = await fetch("/api/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ oppId }),
    });
    plan = await res.json();
    if (!res.ok) throw new Error(plan.error || ("HTTP " + res.status));
  } catch (e) {
    setMsg(oppId, "Couldn’t reach Salesforce — " + esc(e.message), "err");
    btn.disabled = false;
    return;
  }

  if (plan.nothingToDo) {
    setMsg(oppId, "Already tagged in both places — nothing to write. Reload to refresh the list.", "ok");
    btn.disabled = false;
    return;
  }

  // Build a plain-language confirm from the dry-run plan.
  const lines = plan.steps.map((s) => s.blocked ? "⚠️ " + s.blocked : "• " + s.label);
  const ok = window.confirm(
    "This will write to Salesforce (" + writeApi.org + "):\n\n" +
    lines.join("\n") + "\n\nProceed?");
  if (!ok) { setMsg(oppId, "Cancelled — nothing was written.", ""); btn.disabled = false; return; }

  setMsg(oppId, "Writing to Salesforce…", "");
  try {
    const res = await fetch("/api/write", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ oppId, confirm: "CONFIRM" }),
    });
    const out = await res.json();
    if (!res.ok && !out.results) throw new Error(out.error || ("HTTP " + res.status));
    const parts = (out.results || []).map((r) =>
      (r.ok ? "✓ " : "✗ ") + r.object + (r.ok ? " created" : " failed: " + esc(r.error || "")));
    if (out.done) {
      setMsg(oppId, "Done — Mari is now in both. " + parts.join(" · "), "ok");
      // Reflect it locally so the row drops out of the worklist on next render.
      const row = state.rows.find((x) => x.id === oppId);
      if (row) { row.mariOnTeam = true; row.mariOnSplit = true; row.bucket = "ok"; }
      const card = document.querySelector('[data-card="' + CSS.escape(oppId) + '"]');
      if (card) card.style.opacity = ".55";
      if ($("tabActionCount")) $("tabActionCount").textContent =
        state.rows.filter((r) => r.bucket === "missing").length;
    } else {
      setMsg(oppId, parts.join(" · ") || "No changes made.", "err");
      btn.disabled = false;
    }
  } catch (e) {
    setMsg(oppId, "Write failed — " + esc(e.message), "err");
    btn.disabled = false;
  }
}

// Bulk write for every opp in the current filtered view.
async function updateAll(oppIds) {
  const allBtn = $("updateAllBtn");
  const msg = $("bulkMsg");
  if (!oppIds.length) return;

  const ok = window.confirm(
    "This will write to Salesforce (" + writeApi.org + ") for " + oppIds.length +
    " opportunit" + (oppIds.length === 1 ? "y" : "ies") + ".\n\n" +
    "Each one gets Mari added as AE Overlay (team, Read access) and a 100% " +
    "Overlay split — skipping whatever’s already there.\n\nProceed?");
  if (!ok) { msg.className = "write-msg"; msg.textContent = "Cancelled — nothing was written."; return; }

  allBtn.disabled = true;
  msg.className = "write-msg";
  msg.textContent = "Writing " + oppIds.length + " opps… this can take a minute.";
  try {
    const res = await fetch("/api/write-all", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ oppIds, confirm: "CONFIRM" }),
    });
    const out = await res.json();
    if (!res.ok && !out.items) throw new Error(out.error || ("HTTP " + res.status));

    // Reflect successes locally so the worklist shrinks.
    let failed = 0;
    for (const item of out.items || []) {
      if (item.done) {
        const row = state.rows.find((x) => x.id === item.oppId);
        if (row) { row.mariOnTeam = true; row.mariOnSplit = true; row.bucket = "ok"; }
      } else {
        failed++;
      }
    }
    if ($("tabActionCount")) $("tabActionCount").textContent =
      state.rows.filter((r) => r.bucket === "missing").length;

    msg.className = "write-msg " + (failed ? "err" : "ok");
    msg.textContent = "Done — " + (out.done || 0) + " of " + out.count +
      " updated" + (failed ? " · " + failed + " had errors (see writes.log.jsonl)" : "") + ".";
    renderAction();  // re-render: updated opps drop out of the view
  } catch (e) {
    msg.className = "write-msg err";
    msg.textContent = "Bulk write failed — " + esc(e.message);
    allBtn.disabled = false;
  }
}

function filtered() {
  let rows = state.rows;
  if (state.bucket) rows = rows.filter((r) => r.bucket === state.bucket);
  if (state.type) rows = rows.filter((r) => r.type === state.type);
  if (state.segment) rows = rows.filter((r) => r.segment === state.segment);
  if (state.q) {
    rows = rows.filter((r) =>
      [r.name, r.account, r.owner, r.id, r.reason, (r.overlayTeamMembers || []).join(" ")]
        .some((v) => String(v == null ? "" : v).toLowerCase().includes(state.q)));
  }
  const k = state.sortKey, dir = state.sortDir;
  return [...rows].sort((a, b) => {
    let x = a[k], y = b[k];
    if (typeof x === "boolean") { x = x ? 1 : 0; y = y ? 1 : 0; }
    if (k === "amount") { x = Number(x) || 0; y = Number(y) || 0; }
    if (x == null) x = ""; if (y == null) y = "";
    if (x < y) return -1 * dir;
    if (x > y) return 1 * dir;
    return 0;
  });
}

function yn(v) {
  return v ? '<span class="yn y">Yes</span>' : '<span class="yn n">No</span>';
}

function statusCell(r) {
  if (r.bucket === "ok") return '<span class="chip ok">OK</span>';
  if (r.bucket === "someone_else") {
    return '<span class="chip bad">Someone else</span>' +
      '<div class="status-line meta">' + esc(r.reason) + "</div>";
  }
  return '<span class="chip warn">Needs Mari</span>' +
    '<div class="status-line meta">' + esc(r.reason) + "</div>";
}

function render() {
  const rows = filtered();
  const tb = $("tbody");
  tb.innerHTML = rows.map((r) => {
    const link = r.id ? SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + r.id + "/view" : null;
    const nameCell = link
      ? '<a class="opp-link" href="' + link + '" target="_blank" rel="noopener">' + esc(r.name) + "</a>"
      : '<span class="opp-link">' + esc(r.name) + "</span>";
    return (
      '<tr class="b-' + r.bucket + '">' +
      "<td>" + nameCell +
        '<div class="opp-id">' + esc(r.id || "") + "</div>" +
        '<div class="meta">Owner: ' + esc(r.owner || "—") + " · " + esc(r.segment || "—") + "</div></td>" +
      "<td>" + esc(r.account || "—") + "</td>" +
      "<td>" + esc(r.type || "—") + "</td>" +
      "<td>" + fmtDate(r.closeDate) + "</td>" +
      "<td>" + fmtAmount(r.amount) + "</td>" +
      "<td>" + yn(r.mariOnTeam) + "</td>" +
      "<td>" + yn(r.mariOnSplit) + "</td>" +
      "<td>" + statusCell(r) + "</td>" +
      "</tr>"
    );
  }).join("");

  const empty = $("emptyRows");
  empty.classList.toggle("hidden", rows.length !== 0);
  $("tbl").classList.toggle("hidden", rows.length === 0);

  const note = $("filterNote");
  note.textContent = rows.length + " of " + state.rows.length + " opps shown";
}

function exportCsv() {
  const rows = filtered();
  const head = ["opp_id", "opp_name", "account", "owner", "segment", "dir_team",
    "type", "close_date", "amount", "mari_on_team", "mari_on_split",
    "other_overlay", "status", "reason"];
  const lines = [head.join(",")];
  for (const r of rows) {
    const cells = [
      r.id, r.name, r.account, r.owner, r.segment, r.dirTeam, r.type,
      r.closeDate, r.amount, r.mariOnTeam, r.mariOnSplit,
      (r.otherOverlay || []).join("; "), r.bucket, r.reason,
    ].map((v) => {
      const s = String(v == null ? "" : v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    });
    lines.push(cells.join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "japan_overlay_ae_review.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

load();
