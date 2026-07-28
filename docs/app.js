/* Japan Overlay AE Review — static viewer.
 * Reads data.json (produced by refresh.py, flags already baked in) and renders
 * the OK / Needs Mari / Someone-else worklist. Rendering only — no writes,
 * no re-classification (refresh.py already did that; the App Foundry flavor is
 * the one that re-derives).
 */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";

const state = { rows: [], bucket: "", type: "", q: "", sortKey: "closeDate", sortDir: -1 };

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
  $("cSomeone").textContent = sm.someoneElse ?? 0;
  $("cPeriod").textContent = (data.period && data.period.label) || "—";
  if (data.config && data.config.overlayPerson) $("hPerson").textContent = data.config.overlayPerson;

  const gen = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = gen ? "Refreshed " + gen.toLocaleString("en-US") : "";

  // Populate the type filter from the data actually present.
  const types = [...new Set(state.rows.map((r) => r.type).filter(Boolean))].sort();
  const sel = $("typeFilter");
  for (const t of types) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t; sel.appendChild(o);
  }

  $("status").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  wire();
  render();
}

function wire() {
  $("search").addEventListener("input", (e) => { state.q = e.target.value.toLowerCase().trim(); render(); });
  $("typeFilter").addEventListener("change", (e) => { state.type = e.target.value; render(); });
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
}

function filtered() {
  let rows = state.rows;
  if (state.bucket) rows = rows.filter((r) => r.bucket === state.bucket);
  if (state.type) rows = rows.filter((r) => r.type === state.type);
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
