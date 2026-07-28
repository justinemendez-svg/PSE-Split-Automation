/* Japan Overlay AE Review — static viewer.
 * Reads data.json (produced by refresh.py, flags already baked in) and renders
 * the OK / Needs Mari / Someone-else worklist. Rendering only — no writes,
 * no re-classification (refresh.py already did that; the App Foundry flavor is
 * the one that re-derives).
 */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";

const state = { rows: [], bucket: "", type: "", segment: "", q: "", sortKey: "closeDate", sortDir: -1 };

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

  $("status").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  wire();
  render();
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
    // Deep-link straight to this opp's Opportunity Team related list to add Mari.
    const teamUrl = r.id
      ? SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + r.id + "/related/OpportunityTeamMembers/view"
      : null;
    const updateBtn = teamUrl
      ? '<a class="btn-sf" href="' + teamUrl + '" target="_blank" rel="noopener" ' +
        'title="Opens this opp’s Opportunity Team in Salesforce so you can add Mari">Update in Salesforce →</a>'
      : "";
    return (
      '<div class="action-card">' +
      '<div class="action-head">' +
        '<div class="head-main">' +
          '<div class="opp-title">' + title + "</div>" +
          '<div class="opp-id">' + esc(r.id || "") + "</div>" +
          '<div class="meta">' + esc(r.account || "—") + " · " + esc(r.owner || "—") +
            " · " + esc(r.segment || "—") + " · " + esc(r.type || "—") + "</div>" +
          '<div class="reason">' + esc(r.reason) + "</div>" +
        "</div>" +
        updateBtn +
      "</div>" +
      '<div class="action-body">' + teamStep + splitStep + "</div>" +
      "</div>"
    );
  }).join("");
  $("actionEmpty").classList.toggle("hidden", rows.length !== 0);
  list.classList.toggle("hidden", rows.length === 0);
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
