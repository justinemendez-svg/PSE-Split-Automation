/* PSE Split Automation — active PSEs missing from Opportunity Teams. */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";
const TEAM_ROLE = "Partner Account Executive";
const SPLIT_TYPE = "Overlay";
const SPLIT_PERCENTAGE = 100;
const PAGE_SIZE = 100;
const OPPORTUNITY_TYPES = ["Renewal", "New Business", "Expansion"];
const SCOPE_VERSION = 2;
const state = {
  rows: [], summary: {}, type: new Set(), segment: new Set(), region: new Set(),
  filterOptions: { type: OPPORTUNITY_TYPES, segment: [], region: [] }, query: "",
  sortKey: "closeDate", sortDir: -1, updatePage: 1,
  writeApi: { enabled: false, refreshEnabled: false, org: null, authMode: "local", loggedIn: true },
};
const $ = (id) => document.getElementById(id);
let wired = false;
let searchTimer = null;

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value + "T00:00:00");
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" });
}
function fmtCount(value) { return Number(value || 0).toLocaleString("en-US"); }
function detailsLine(row) {
  return [row.id, row.stage, fmtDate(row.closeDate), row.type].filter(Boolean).join(" · ");
}
function accountLine(row) {
  return [row.account, row.accountOwner, row.segment, row.region].filter(Boolean).join(" · ") || "—";
}

async function load() {
  wire();
  try {
    const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });
    if (!response.ok) {
      await detectApi();
      const status = $("status");
      status.className = state.writeApi.refreshEnabled ? "status" : "status err";
      status.textContent = state.writeApi.refreshEnabled
        ? "No Salesforce snapshot yet. Click Refresh data to load the first dashboard snapshot."
        : "Dashboard data is not available. Run `python3 refresh.py --org prod`, then reload. (HTTP " + response.status + ")";
      return;
    }
    hydrate(await response.json());
    await detectApi();
  } catch (error) {
    await detectApi();
    const status = $("status");
    status.className = state.writeApi.refreshEnabled ? "status" : "status err";
    status.textContent = state.writeApi.refreshEnabled
      ? "Dashboard snapshot could not be loaded. Click Refresh data to fetch it from Salesforce. (" + error.message + ")"
      : window.location.protocol === "file:"
        ? "Open the dashboard through its local server. Double-click serve.command, then open http://localhost:8773 in Chrome."
        : "Dashboard data is not available. Run `python3 refresh.py --org prod`, then reload. (" + error.message + ")";
  }
}

function renderFilter(id, key, values) {
  const component = $(id);
  const selected = state[key];
  const available = new Set(values);
  state[key] = new Set([...selected].filter((value) => available.has(value)));
  state.filterOptions[key] = values;
  const choices = state[key];
  const label = component.dataset.filterLabel;
  const button = $(id + "Button");
  button.textContent = !choices.size || choices.size === values.length
    ? "All " + label
    : choices.size === 1 ? [...choices][0] : choices.size + " " + label + " selected";
  const menu = $(id + "Menu");
  menu.innerHTML = values.map((value) =>
    '<label class="filter-option"><input type="checkbox" data-filter-option value="' + esc(value) + '"' +
    (choices.has(value) ? " checked" : "") + '><span>' + esc(value) + '</span></label>').join("") +
    '<div class="filter-actions"><button type="button" data-filter-select-all>Select all</button>' +
    '<button type="button" data-filter-clear>Clear</button></div>';
}

function hydrate(data) {
  wire();
  if ((data.config || {}).scopeVersion !== SCOPE_VERSION) {
    state.rows = [];
    const status = $("status");
    status.className = "status err";
    status.textContent = "This snapshot predates the commissionable opportunity scope. Run `python3 refresh.py --org prod` or use Refresh data locally to load the current Renewal, New Business, and Expansion worklist.";
    return;
  }
  state.rows = data.rows || [];
  state.summary = data.summary || {};
  $("cTotal").textContent = fmtCount(state.summary.total ?? state.rows.length);
  $("cTagged").textContent = fmtCount(state.summary.tagged);
  $("cNeedsUpdate").textContent = fmtCount(state.summary.needsUpdate);
  const generated = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = generated ? "Refreshed " + generated.toLocaleString() : "";
  renderFilter("typeFilter", "type", OPPORTUNITY_TYPES);
  renderFilter("segmentFilter", "segment", [...new Set(state.rows.map((row) => row.segment).filter(Boolean))].sort());
  renderFilter("regionFilter", "region", [...new Set(state.rows.map((row) => row.region).filter(Boolean))].sort());
  $("status").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  render();
}

async function detectApi() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) return;
    const health = await response.json();
    state.writeApi.enabled = !!health.writeEnabled;
    state.writeApi.refreshEnabled = !!health.refreshEnabled;
    state.writeApi.org = health.org || "default";
    state.writeApi.authMode = health.authMode || "local";
    state.writeApi.loggedIn = state.writeApi.authMode !== "oauth" || !!health.loggedIn;
    if (health.refreshEnabled) $("refreshBtn").classList.remove("hidden");
    if (state.writeApi.enabled) $("who").textContent += " · Salesforce org: " + state.writeApi.org;
    renderUpdate();
  } catch (_) { /* Static hosting has no local Salesforce API. */ }
}

function filteredRows() {
  let rows = state.rows.filter((row) => row.nonCommissionable === false &&
    OPPORTUNITY_TYPES.includes(row.type) && (!row.partnerRepOnTeam || !row.partnerRepOnSplit));
  ["type", "segment", "region"].forEach((key) => {
    const selected = state[key];
    const options = state.filterOptions[key];
    if (selected.size && selected.size < options.length) rows = rows.filter((row) => selected.has(row[key]));
  });
  if (state.query) {
    const q = state.query;
    rows = rows.filter((row) => [row.name, row.account, row.id, row.accountOwner, row.partnerRepName]
      .some((value) => String(value == null ? "" : value).toLowerCase().includes(q)));
  }
  const key = state.sortKey;
  return [...rows].sort((a, b) => {
    const av = a[key] == null ? "" : a[key];
    const bv = b[key] == null ? "" : b[key];
    const compare = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
    return compare * state.sortDir;
  });
}

function opportunityCard(row) {
  const href = SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + encodeURIComponent(row.id) + "/view";
  const button = state.writeApi.enabled
    ? '<button class="btn-update" data-update="' + esc(row.id) + '">Update in Salesforce →</button>'
    : '<a class="btn-update" href="' + esc(href) + '" target="_blank" rel="noopener">Open in Salesforce →</a>';
  const teamAlready = row.partnerRepOnTeam
    ? '<div class="also">Already on the Opportunity Team — no change needed.</div>' : '';
  const splitAlready = row.partnerRepOnSplit
    ? '<div class="also">Overlay split already set — no split change needed.</div>' : '';
  return '<article class="action-card needs-update" data-card="' + esc(row.id) + '">' +
    '<div class="action-head"><div class="head-main">' +
    '<div class="opp-title"><a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(row.name || "Unnamed Opportunity") + '</a></div>' +
    '<div class="details-line">' + esc(detailsLine(row)) + '</div>' +
    '<div class="details-line account-name">' + esc(accountLine(row)) + '</div>' +
    '<div class="pse-tag"><b>PSE Tagged</b><span class="pse-name">' + esc(row.partnerRepName || "—") + '</span><span class="pill">Active</span></div>' +
    '<div class="msg" data-msg="' + esc(row.id) + '"></div></div>' + button + '</div>' +
    '<div class="action-steps">' +
      '<section class="step"><div class="step-title">1 · Add Opportunity Team Member</div>' +
        '<div class="step-path">Opportunity → Opportunity Team → Add Members</div>' +
        '<table class="step-values"><tbody>' +
          '<tr><td>User</td><td>' + esc(row.partnerRepName || "—") + '</td></tr>' +
          '<tr><td>Team Role</td><td>' + esc(TEAM_ROLE) + '</td></tr>' +
          '<tr><td>Opportunity Access</td><td>Read Only</td></tr>' +
        '</tbody></table>' + teamAlready + '</section>' +
      '<section class="step"><div class="step-title">2 · Add Overlay Split</div>' +
        '<div class="step-path">Opportunity → Opportunity Splits → Edit Splits</div>' +
        '<table class="step-values"><tbody>' +
          '<tr><td>Split Owner</td><td>' + esc(row.partnerRepName || "—") + '</td></tr>' +
          '<tr><td>Split Type</td><td>' + esc(SPLIT_TYPE) + '</td></tr>' +
        '</tbody></table>' + splitAlready + '</section>' +
    '</div></article>';
}

function wireCardUpdates(containerId) {
  $(containerId).querySelectorAll("button[data-update]").forEach((button) =>
    button.addEventListener("click", () => updateOne(button.dataset.update, button)));
}

function renderUpdate() {
  const rows = filteredRows();
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.updatePage = Math.min(state.updatePage, pageCount);
  const start = (state.updatePage - 1) * PAGE_SIZE;
  $("bulkCount").textContent = rows.length.toLocaleString();
  $("updateAllBtn").textContent = "Update all " + rows.length.toLocaleString() + " in Salesforce →";
  $("updateAllBtn").disabled = !state.writeApi.enabled || rows.length === 0;
  $("actionList").innerHTML = rows.slice(start, start + PAGE_SIZE).map((row) => opportunityCard(row)).join("");
  wireCardUpdates("actionList");
  $("actionPageInfo").textContent = rows.length ? `Page ${state.updatePage} of ${pageCount}` : "";
  $("filterCount").textContent = rows.length.toLocaleString() + " PSEs to update";
  $("actionPrev").disabled = state.updatePage <= 1;
  $("actionNext").disabled = state.updatePage >= pageCount;
  $("actionEmpty").classList.toggle("hidden", rows.length > 0);
  $("actionList").classList.toggle("hidden", rows.length === 0);
}

function render() {
  renderUpdate();
}

async function postJson(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || ("HTTP " + response.status));
  return result;
}

function ensureWriteLogin() {
  if (state.writeApi.authMode !== "oauth" || state.writeApi.loggedIn) return Promise.resolve(true);
  return new Promise((resolve) => {
    const popup = window.open("/oauth/login?next=/", "sfLogin", "width=620,height=720");
    if (!popup) { window.alert("Please allow the Salesforce login popup, then click again."); resolve(false); return; }
    let finished = false;
    const finish = (okay) => {
      if (finished) return;
      finished = true;
      clearInterval(timer);
      window.removeEventListener("message", onMessage);
      state.writeApi.loggedIn = !!okay;
      resolve(!!okay);
    };
    const onMessage = (event) => {
      if (event.origin !== window.location.origin || !event.data || !event.data.sfAuth) return;
      if (event.data.error) { window.alert("Salesforce login failed: " + event.data.error); finish(false); }
      else finish(true);
    };
    window.addEventListener("message", onMessage);
    const timer = setInterval(() => { if (popup.closed) finish(state.writeApi.loggedIn); }, 500);
  });
}

function setRowState(oppId, onTeam, onSplit) {
  const row = state.rows.find((item) => item.id === oppId);
  if (!row) return;
  const wasComplete = row.partnerRepOnTeam && row.partnerRepOnSplit;
  row.partnerRepOnTeam = !!onTeam;
  row.partnerRepOnSplit = !!onSplit;
  const isComplete = row.partnerRepOnTeam && row.partnerRepOnSplit;
  row.bucket = isComplete ? "complete" : "needs_call";
  if (!wasComplete && isComplete) {
    state.summary.tagged = (state.summary.tagged || 0) + 1;
    state.summary.needsUpdate = Math.max(0, (state.summary.needsUpdate || 0) - 1);
  } else if (wasComplete && !isComplete) {
    state.summary.tagged = Math.max(0, (state.summary.tagged || 0) - 1);
    state.summary.needsUpdate = (state.summary.needsUpdate || 0) + 1;
  }
  $("cTagged").textContent = fmtCount(state.summary.tagged);
  $("cNeedsUpdate").textContent = fmtCount(state.summary.needsUpdate);
}

async function updateOne(oppId, button) {
  button.disabled = true;
  const message = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
  message.textContent = "Checking Salesforce…";
  message.className = "msg";
  try {
    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");
    const preview = await postJson("/api/preview", { oppId });
    if (preview.alreadyComplete) {
      setRowState(oppId, true, true); renderUpdate(); return;
    }
    if (!preview.eligible) throw new Error(preview.reason || "This PSE is no longer eligible.");
    const okay = window.confirm(
      "Update the Opportunity Team and Opportunity Split for:\n\n" + preview.oppName +
      "\n\nPSE: " + preview.partnerRepName +
      "\nTeam role: " + TEAM_ROLE + " (Opportunity Access: Read Only)" +
      "\nSplit: " + SPLIT_PERCENTAGE + "% " + SPLIT_TYPE +
      "\nSalesforce org: " + state.writeApi.org + "\n\nOnly missing entries will be added. Proceed?"
    );
    if (!okay) { message.textContent = "Cancelled — no Salesforce change was made."; button.disabled = false; return; }
    message.textContent = "Updating Salesforce…";
    const result = await postJson("/api/write", { oppId, confirm: "CONFIRM" });
    setRowState(oppId, result.partnerRepOnTeam, result.partnerRepOnSplit);
    if (!result.done) { renderUpdate(); throw new Error(result.error || "Salesforce did not confirm both the team and split updates."); }
    renderUpdate();
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;
    const current = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
    if (current) { current.textContent = error.message; current.className = "msg err"; }
    button.disabled = false;
  }
}

async function updateAll() {
  const rows = filteredRows();
  const ids = rows.map((row) => row.id).filter(Boolean);
  if (!ids.length) return;
  const button = $("updateAllBtn");
  const message = $("bulkMsg");
  button.disabled = true;
  message.className = "msg";
  message.textContent = "Checking current Salesforce status…";
  try {
    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");
    const preview = await postJson("/api/preview-all", { oppIds: ids });
    if (!preview.idsToAdd.length) {
      Object.entries(preview.states || {}).forEach(([id, status]) =>
        setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));
      renderUpdate();
      message.textContent = "No updates needed — the active PSEs are already on the team and Overlay split.";
      return;
    }
    Object.entries(preview.states || {}).forEach(([id, status]) =>
      setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));
    renderUpdate();
    const okay = window.confirm(
      "Update " + preview.idsToAdd.length.toLocaleString() + " Opportunities in " + state.writeApi.org +
      "?\n\nFor each Opportunity, add its currently tagged active PSE to the Opportunity Team (" + TEAM_ROLE + ", Read Only access) and create a " +
      SPLIT_PERCENTAGE + "% " + SPLIT_TYPE + " Opportunity Split where missing. Existing entries are kept without duplicates.\n\nThis uses the current filters and rechecks each assigned rep before writing."
    );
    if (!okay) { message.textContent = "Cancelled — no Salesforce changes were made."; button.disabled = false; return; }
    message.textContent = "Updating Salesforce… this may take several minutes.";
    const result = await postJson("/api/write-all", { oppIds: preview.idsToAdd, confirm: "CONFIRM" });
    Object.entries(result.states || {}).forEach(([id, status]) =>
      setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));
    message.className = result.failed ? "msg err" : "msg ok";
    message.textContent = fmtCount(result.succeeded) + " fully updated · " + fmtCount(result.teamAdded) + " team adds · " +
      fmtCount(result.splitAdded) + " split adds" + (result.failed ? " · " + fmtCount(result.failed) + " incomplete" : "") +
      ((result.inactive || result.changed || result.excluded) ? " · " + fmtCount((result.inactive || 0) + (result.changed || 0) + (result.excluded || 0)) + " skipped" : "");
    render();
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;
    message.className = "msg err";
    message.textContent = "Update failed: " + error.message;
  } finally {
    button.disabled = false;
  }
}

async function reloadSnapshot() {
  const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });
  if (!response.ok) throw new Error("Could not reload the dashboard snapshot.");
  hydrate(await response.json());
}

async function refreshData() {
  const button = $("refreshBtn");
  const message = $("refreshMsg");
  button.disabled = true; button.textContent = "Refreshing…";
  message.className = "sub"; message.textContent = "Reading Salesforce…";
  try {
    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required to refresh the dashboard.");
    const result = await postJson("/api/refresh", {});
    if (result.jobId) {
      let complete = false;
      while (!complete) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const response = await fetch("/api/refresh-status?jobId=" + encodeURIComponent(result.jobId), { cache: "no-store" });
        const job = await response.json();
        if (!response.ok) throw new Error(job.error || ("HTTP " + response.status));
        message.textContent = job.message || "Refreshing Salesforce data…";
        if (job.status === "failed") throw new Error(job.message || "Salesforce refresh failed.");
        complete = job.status === "complete";
      }
    }
    await reloadSnapshot();
    message.className = "sub ok";
    message.textContent = "Refreshed · " + fmtCount((result.summary || state.summary).total) + " in-scope active PSE Opps";
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce session expired")) state.writeApi.loggedIn = false;
    message.className = "sub err"; message.textContent = "Refresh failed: " + error.message;
  } finally { button.disabled = false; button.textContent = "Refresh data"; }
}

function csvCell(value) { return '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"'; }
function exportCsv() {
  const rows = filteredRows();
  const columns = ["id", "name", "stage", "closeDate", "type", "account", "accountOwner", "segment", "region", "partnerRepName", "partnerRepId", "partnerRepOnTeam", "partnerRepOnSplit"];
  const lines = [columns.join(","), ...rows.map((row) => columns.map((key) => csvCell(row[key])).join(","))];
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = "pse_to_update.csv"; anchor.click(); URL.revokeObjectURL(url);
}

function wire() {
  if (wired) return;
  wired = true;
  $("search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = event.target.value.trim().toLowerCase(); state.updatePage = 1; render(); }, 160);
  });
  [["typeFilter", "type"], ["segmentFilter", "segment"], ["regionFilter", "region"]].forEach(([id, key]) => {
    const component = $(id);
    const button = $(id + "Button");
    button.addEventListener("click", () => {
      document.querySelectorAll(".multi-filter").forEach((other) => {
        if (other !== component) {
          $(other.id + "Menu").classList.add("hidden");
          $(other.id + "Button").setAttribute("aria-expanded", "false");
        }
      });
      const menu = $(id + "Menu");
      menu.classList.toggle("hidden");
      button.setAttribute("aria-expanded", String(!menu.classList.contains("hidden")));
    });
    component.addEventListener("change", (event) => {
      if (!event.target.matches("input[data-filter-option]")) return;
      const selected = new Set(state[key]);
      if (event.target.checked) selected.add(event.target.value);
      else selected.delete(event.target.value);
      state[key] = selected;
      renderFilter(id, key, state.filterOptions[key]);
      state.updatePage = 1;
      render();
    });
    component.addEventListener("click", (event) => {
      const action = event.target.closest("button[data-filter-select-all], button[data-filter-clear]");
      if (!action) return;
      event.stopPropagation();
      state[key] = action.hasAttribute("data-filter-select-all")
        ? new Set(state.filterOptions[key]) : new Set();
      renderFilter(id, key, state.filterOptions[key]);
      state.updatePage = 1;
      render();
    });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest(".multi-filter")) return;
    document.querySelectorAll(".multi-filter").forEach((component) => {
      $(component.id + "Menu").classList.add("hidden");
      $(component.id + "Button").setAttribute("aria-expanded", "false");
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".multi-filter").forEach((component) => {
      $(component.id + "Menu").classList.add("hidden");
      $(component.id + "Button").setAttribute("aria-expanded", "false");
    });
  });
  $("actionPrev").addEventListener("click", () => { state.updatePage = Math.max(1, state.updatePage - 1); renderUpdate(); });
  $("actionNext").addEventListener("click", () => { state.updatePage += 1; renderUpdate(); });
  $("updateAllBtn").addEventListener("click", updateAll);
  $("refreshBtn").addEventListener("click", refreshData);
  $("csvBtn").addEventListener("click", exportCsv);
}

load();
