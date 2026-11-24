/* Partner → PSE field → Opportunity Team → Opportunity Splits. */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";
const TEAM_ROLE = "Partner Account Executive";
const SPLIT_TYPE = "Overlay";
const SPLIT_PERCENTAGE = 100;
const PAGE_SIZE = 100;
const OPPORTUNITY_TYPES = ["Renewal", "New Business", "Expansion"];
const SCOPE_VERSION = 4;
const CLOSED_STAGE = "08 - Closed";
const state = {
  rows: [], rowsById: new Map(), summary: {}, type: new Set(), segment: new Set(), region: new Set(), pseStatus: new Set(), fiscalQuarter: new Set(),
  filterOptions: { type: OPPORTUNITY_TYPES, segment: [], region: [], pseStatus: [], fiscalQuarter: [] }, query: "",
  sortKey: "closeDate", sortDir: -1, updatePage: 1, pseCategory: "all", busy: false,
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
function fiscalQuarterFromDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return "Close date unavailable";
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "Close date unavailable";
  const fiscalYear = month >= 2 ? year + 1 : year;
  const quarter = Math.floor(((month + 10) % 12) / 3) + 1;
  return "FY" + fiscalYear + " Q" + quarter;
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
      const details = await response.json().catch(() => ({}));
      await detectApi();
      const status = $("status");
      if (details.error) {
        status.className = "status err";
        status.textContent = "Dashboard data could not be loaded: " + details.error + " (HTTP " + response.status + "). Check the server configuration, then reload.";
        return;
      }
      status.className = state.writeApi.refreshEnabled ? "status" : "status err";
      status.textContent = state.writeApi.refreshEnabled
        ? "No Salesforce snapshot yet. Click Refresh to load data."
        : "Dashboard data is unavailable (HTTP " + response.status + "). Check the server configuration, then reload.";
      return;
    }
    const data = await response.json();
    hydrate(data);
    await detectApi();
  } catch (error) {
    await detectApi();
    const status = $("status");
    status.className = state.writeApi.refreshEnabled ? "status" : "status err";
    status.textContent = state.writeApi.refreshEnabled
      ? "Dashboard snapshot could not be loaded. Click Refresh to fetch it from Salesforce. (" + error.message + ")"
      : window.location.protocol === "file:"
        ? "Open the dashboard through its local server. Double-click serve.command, then open http://localhost:8773 in Chrome."
        : "Dashboard data could not be loaded. Check the server connection, then reload. (" + error.message + ")";
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
    ? component.dataset.filterTitle
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
  state.generatedAt = data.generatedAt;
  if ((data.config || {}).scopeVersion !== SCOPE_VERSION) {
    state.rows = [];
    state.rowsById.clear();
    $("dashboard").classList.add("hidden");
    const status = $("status");
    status.className = "status";
    status.textContent = "The closed Opportunity flow (08 - Closed) needs a fresh Salesforce snapshot. Load a current export using Upload the data, or click Refresh in the local app.";
    return false;
  }
  state.rows = (data.rows || []).map((row) => ({ ...row, fiscalQuarter: fiscalQuarterFromDate(row.closeDate) }));
  state.rowsById = new Map(state.rows.map((row) => [row.id, row]));
  state.summary = data.summary || {};
  const generated = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = generated ? "Refreshed " + generated.toLocaleString() : "";
  renderFilter("pseStatusFilter", "pseStatus", ["Active", "Inactive", ...(state.rows.some((row) => row.partnerRepActive !== true && row.partnerRepActive !== false) ? ["Status unavailable"] : [])]);
  renderFilter("quarterFilter", "fiscalQuarter", [...new Set(state.rows.map((row) => row.fiscalQuarter))].sort((a, b) => b.localeCompare(a)));
  renderFilter("typeFilter", "type", OPPORTUNITY_TYPES);
  renderFilter("segmentFilter", "segment", [...new Set(state.rows.map((row) => row.segment).filter(Boolean))].sort());
  renderFilter("regionFilter", "region", [...new Set(state.rows.map((row) => row.region).filter(Boolean))].sort());
  $("status").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  render();
  return true;
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
    $("refreshBtn").classList.toggle("hidden", !health.refreshEnabled);
    state.writeApi.uploadEnabled = !!health.uploadEnabled;
    $("uploadDataBtn").classList.toggle("hidden", !health.uploadEnabled);
    document.querySelectorAll("[data-hosted-upload]").forEach(node => node.classList.toggle("hidden", !health.uploadEnabled));
    document.querySelectorAll("[data-local-actions]").forEach(node => node.classList.toggle("hidden", !!health.uploadEnabled));
    $("updateAllBtn").classList.toggle("hidden", !!health.uploadEnabled);
    if (health.uploadEnabled && !state.generatedAt) {
      $("status").className = "status";
      $("status").textContent = "No data uploaded yet. Click Upload the data to load your Salesforce export.";
      $("dashboard").classList.add("hidden");
    }
    $("who").textContent = $("who").textContent.split(" · Salesforce org:")[0];
    if (state.writeApi.enabled) $("who").textContent += " · Salesforce org: " + state.writeApi.org;
    renderUpdate();
  } catch (_) { /* Static hosting has no local Salesforce API. */ }
}

function pseStatus(row) { return row.partnerRepActive === true ? "Active" : row.partnerRepActive === false ? "Inactive" : "Status unavailable"; }

function rowComplete(row) { return !row.partnerRepNeedsPopulate && row.partnerRepOnTeam && row.partnerRepOnSplit; }

function matchesCategory(row, category) {
  const missingMembership = !row.partnerRepOnTeam || !row.partnerRepOnSplit;
  if (category === "tagged") return !row.partnerRepNeedsPopulate && missingMembership;
  if (category === "untagged") return !!row.partnerRepNeedsPopulate && missingMembership;
  return missingMembership;
}

function filteredRows(category = state.pseCategory) {
  let rows = state.rows.filter((row) => row.inScope !== false && row.nonCommissionable === false && row.stage === CLOSED_STAGE &&
    OPPORTUNITY_TYPES.includes(row.type) && row.partnerId && !rowComplete(row));
  ["type", "segment", "region", "pseStatus", "fiscalQuarter"].forEach((key) => {
    const selected = state[key];
    const options = state.filterOptions[key];
    if (selected.size && selected.size < options.length) rows = rows.filter((row) => selected.has(key === "pseStatus" ? pseStatus(row) : row[key]));
  });
  if (state.query) {
    const q = state.query;
    rows = rows.filter((row) => [row.name, row.account, row.id, row.accountOwner, row.partnerRepName]
      .some((value) => String(value == null ? "" : value).toLowerCase().includes(q)));
  }
  rows = rows.filter((row) => matchesCategory(row, category));
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
    ? '<button class="btn-update" data-update="' + esc(row.id) + '"' + ((state.busy || row.blockedReason) ? ' disabled' : '') + '>Update in Salesforce →</button>'
    : '<a class="btn-update" href="' + esc(href) + '" target="_blank" rel="noopener">Open in Salesforce →</a>';
  const teamAlready = row.partnerRepOnTeam
    ? '<div class="also">Already on the Opportunity Team — no change needed.</div>' : '';
  const splitAlready = row.partnerRepOnSplit
    ? '<div class="also">Rep already on Opportunity Splits — existing entry preserved.</div>' : '';
  const userStatus = row.partnerRepActive === true ? 'Active' : row.partnerRepActive === false ? 'Inactive' : 'Status unavailable';
  return '<article class="action-card needs-update" data-card="' + esc(row.id) + '">' +
    '<div class="action-head"><div class="head-main">' +
    '<div class="opp-title"><a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(row.name || "Unnamed Opportunity") + '</a></div>' +
    '<div class="details-line">' + esc(detailsLine(row)) + '</div>' +
    '<div class="details-line account-name">' + esc(accountLine(row)) + '</div>' +
    '<div class="details-line">Partner: ' + esc(row.partnerName || "—") + ' · Partner Owner: ' + esc(row.partnerOwnerName || "—") + '</div>' +
    '<div class="pse-tag"><b>' + (row.partnerRepNeedsPopulate ? 'PSE to populate' : 'PSE Tagged') + '</b><span class="pse-name">' + esc(row.partnerRepName || "—") + '</span><span class="pill' + (row.partnerRepActive !== true ? ' warn' : '') + '">' + userStatus + '</span></div>' +
    (row.blockedReason ? '<div class="msg err">' + esc(row.blockedReason) + '</div>' : '') +
    '<div class="msg" data-msg="' + esc(row.id) + '"></div></div>' + button + '</div>' +
    '<div class="action-steps">' +
      '<section class="step"><div class="step-title">1 · Partner Sales Rep field</div>' +
        '<div class="step-path">' + (row.partnerRepNeedsPopulate ? 'Populate from the tagged Partner’s Owner' : 'Preserve the existing PSE') + '</div>' +
        '<table class="step-values"><tbody><tr><td>PSE</td><td>' + esc(row.partnerRepName) + '</td></tr><tr><td>Action</td><td>' +
        (row.partnerRepNeedsPopulate ? 'Populate blank field' : 'No field change') + '</td></tr></tbody></table></section>' +
      '<section class="step"><div class="step-title">2 · Check Opportunity Team</div>' +
        '<div class="step-path">Opportunity → Opportunity Team → Add Members</div>' +
        '<table class="step-values"><tbody>' +
          '<tr><td>User</td><td>' + esc(row.partnerRepName || "—") + '</td></tr>' +
          '<tr><td>Team Role</td><td>' + esc(TEAM_ROLE) + '</td></tr>' +
          '<tr><td>Opportunity Access</td><td>Read Only</td></tr>' +
        '</tbody></table>' + teamAlready + '</section>' +
      '<section class="step"><div class="step-title">3 · Check Opportunity Splits</div>' +
        '<div class="step-path">Opportunity → Opportunity Splits → Edit Splits</div>' +
        '<table class="step-values"><tbody>' +
          '<tr><td>Split Owner</td><td>' + esc(row.partnerRepName || "—") + '</td></tr>' +
          '<tr><td>Split Type</td><td>' + esc(SPLIT_TYPE) + '</td></tr>' +
          '<tr><td>Team Role</td><td>' + esc(TEAM_ROLE) + '</td></tr>' +
          '<tr><td>Percent</td><td>100.00%</td></tr>' +
        '</tbody></table>' + splitAlready + '</section>' +
    '</div></article>';
}

function wireCardUpdates(containerId) {
  $(containerId).querySelectorAll("button[data-update]").forEach((button) =>
    button.addEventListener("click", () => updateOne(button.dataset.update, button)));
}

function renderUpdate() {
  const allRows = filteredRows("all");
  const rows = allRows.filter((row) => matchesCategory(row, state.pseCategory));
  $("cWithPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "tagged")).length);
  $("cWithoutPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "untagged")).length);
  $("cNeedsUpdate").textContent = fmtCount(allRows.length);
  document.querySelectorAll("[data-pse-category]").forEach((button) => {
    const selected = button.dataset.pseCategory === state.pseCategory;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("categoryLabel").textContent = {
    all: "Showing all Opportunities to update",
    tagged: "Showing Partner Opportunities with a tagged PSE · missing Team or Split",
    untagged: "Showing Partner Opportunities without a tagged PSE · missing Team or Split",
  }[state.pseCategory];
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.updatePage = Math.min(state.updatePage, pageCount);
  const start = (state.updatePage - 1) * PAGE_SIZE;
  $("bulkCount").textContent = rows.length.toLocaleString();
  $("updateAllBtn").textContent = "Update all " + rows.length.toLocaleString() + " in Salesforce →";
  $("updateAllBtn").disabled = state.busy || !state.writeApi.enabled || rows.length === 0;
  $("actionList").innerHTML = rows.slice(start, start + PAGE_SIZE).map((row) => opportunityCard(row)).join("");
  wireCardUpdates("actionList");
  $("actionPageInfo").textContent = rows.length ? `Page ${state.updatePage} of ${pageCount}` : "";
  $("filterCount").textContent = fmtCount(rows.length) + " Opportunities to update";
  $("actionPrev").disabled = state.busy || state.updatePage <= 1;
  $("actionNext").disabled = state.busy || state.updatePage >= pageCount;
  $("actionEmpty").classList.toggle("hidden", rows.length > 0);
  $("actionList").classList.toggle("hidden", rows.length === 0);
}

function render() {
  renderUpdate();
}

async function postJson(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error(path === "/api/upload"
    ? "The upload response could not be read."
    : "The server response could not be read. Check Salesforce status before retrying an update."); }
  if (response.status === 401 || result.needsLogin) state.writeApi.loggedIn = false;
  if (!response.ok) {
    const error = new Error(result.error || ("HTTP " + response.status));
    error.status = response.status;
    throw error;
  }
  return result;
}

function ensureWriteLogin() { return Promise.resolve(true); }

let selectedUpload = null;
let uploadPreviewVersion = 0;
async function previewUpload(file) {
  if (state.busy) return;
  const version = ++uploadPreviewVersion;
  selectedUpload = null;
  $("confirmUploadBtn").disabled = true;
  const message = $("uploadMsg");
  if (!file) { message.textContent = "Choose pse-data.json from the PSE_Split_Automation folder."; return; }
  try {
    if (file.size > 25 * 1024 * 1024) throw new Error("Maximum file size is 25 MB.");
    const value = JSON.parse(await file.text());
    if (version !== uploadPreviewVersion) return;
    if (!value || value.config?.scopeVersion !== SCOPE_VERSION || !Array.isArray(value.rows) ||
        !value.generatedAt || Number.isNaN(new Date(value.generatedAt).getTime()))
      throw new Error("Choose pse-data.json generated by the latest local Salesforce export command.");
    selectedUpload = value;
    message.className = "msg";
    message.textContent = fmtCount(value.rows.length) + " Opportunities in file · Extracted " + new Date(value.generatedAt).toLocaleString() + ". Click Upload data to dashboard to replace the saved data.";
    $("confirmUploadBtn").disabled = false;
  } catch (error) {
    if (version !== uploadPreviewVersion) return;
    message.className = "msg err"; message.textContent = error.message;
  }
}

async function uploadData() {
  if (!selectedUpload || state.busy) return;
  setBusy(true);
  $("confirmUploadBtn").disabled = true;
  $("uploadFile").disabled = true;
  $("uploadMsg").className = "msg";
  $("uploadMsg").textContent = "Validating and saving the uploaded data…";
  try {
    const result = await postJson("/api/upload", { manual: true, snapshot: selectedUpload });
    hydrate(result.snapshot);
    $("uploadMsg").className = "msg ok";
    $("uploadMsg").textContent = "Upload complete. The dashboard now shows this Salesforce export. Close this page to view it.";
  } catch (error) {
    $("uploadMsg").className = "msg err";
    $("uploadMsg").textContent = error.message + ([400, 413].includes(error.status)
      ? " The file was rejected; the previous saved data was kept."
      : " Upload status could not be confirmed. Close this page and reload the dashboard to check the saved extraction date before retrying.");
  } finally {
    setBusy(false);
    $("confirmUploadBtn").disabled = false;
    $("uploadFile").disabled = false;
  }
}

function setRowState(oppId, value) {
  const row = state.rowsById.get(oppId);
  if (!row) return;
  const wasComplete = !!rowComplete(row);
  Object.assign(row, value);
  const change = Number(!!rowComplete(row)) - Number(wasComplete);
  state.summary.tagged = (state.summary.tagged || 0) + change;
  state.summary.needsUpdate = (state.summary.needsUpdate || 0) - change;

}

function setBusy(value) {
  state.busy = value;
  document.querySelectorAll('#search, .filter-trigger, .filter-menu input, .filter-actions button, [data-pse-category], #actionPrev, #actionNext, #csvBtn, #refreshBtn, #updateAllBtn, button[data-update]').forEach((control) => { control.disabled = value; });
  if (!value) renderUpdate();
}

async function updateOne(oppId, button) {
  if (state.busy) return;
  setBusy(true);
  button.disabled = true;
  let message = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
  message.textContent = "Checking Salesforce…";
  message.className = "msg";
  try {
    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");
    const preview = await postJson("/api/preview", { oppId });
    setRowState(oppId, preview);
    if (preview.alreadyComplete) {
      setRowState(oppId, preview); renderUpdate(); return;
    }
    if (!preview.eligible) throw new Error(preview.reason || "This PSE is no longer eligible.");
    const okay = window.confirm(
      "Apply the Partner → PSE → Team → Splits process for:\n\n" + preview.oppName +
      "\n\nPSE: " + preview.partnerRepName +
      "\nPSE field: " + (preview.partnerRepNeedsPopulate ? "Populate from Partner Owner" : "Keep existing value") +
      "\nTeam role: " + TEAM_ROLE + " (Opportunity Access: Read Only)" +
      "\nSplit: " + SPLIT_PERCENTAGE + "% " + SPLIT_TYPE +
      "\nSalesforce org: " + state.writeApi.org + "\n\nOnly blank PSE fields and missing Team/Split entries are added. Existing entries are preserved. Proceed?"
    );
    if (!okay) { message.textContent = "Cancelled — no Salesforce change was made."; button.disabled = false; return; }
    message.textContent = "Updating Salesforce…";
    const result = await postJson("/api/write", { oppId, confirm: "CONFIRM" });
    setRowState(oppId, result);
    if (!result.done) { renderUpdate(); throw new Error(result.error || "Salesforce did not confirm both the team and split updates."); }
    renderUpdate();
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;
    const current = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
    if (current) { current.textContent = error.message; current.className = "msg err"; }
    button.disabled = false;
  } finally {
    const text = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]')?.textContent;
    const error = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]')?.classList.contains('err');
    setBusy(false);
    const current = document.querySelector('[data-msg="' + CSS.escape(oppId) + '"]');
    if (current && text) { current.textContent = text; current.className = error ? 'msg err' : 'msg'; }
  }
}

async function updateAll() {
  if (state.busy) return;
  const rows = filteredRows();
  const ids = rows.map((row) => row.id).filter(Boolean);
  if (!ids.length) return;
  setBusy(true);
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
        setRowState(id, status));
      renderUpdate();
      message.textContent = "No eligible updates remain. The records are complete, changed scope, or have an unavailable Partner Owner. Refresh for current status.";
      return;
    }
    Object.entries(preview.states || {}).forEach(([id, status]) =>
      setRowState(id, status));
    renderUpdate();
    const okay = window.confirm(
      "Update " + preview.idsToAdd.length.toLocaleString() + " Opportunities in " + state.writeApi.org +
      "?\n\nKeep the existing PSE. If blank, populate it from the tagged Partner’s Owner. Then add the PSE to the Opportunity Team (" + TEAM_ROLE + ", Read Only) if missing, followed by a 100% Overlay Split (" + TEAM_ROLE + ") if the rep has no existing Split.\n\nExisting field values and Team/Split entries are preserved. This applies to all results across all pages using your current filters and rechecks Salesforce before each stage."
    );
    if (!okay) { message.textContent = "Cancelled — no Salesforce changes were made."; button.disabled = false; return; }
    message.textContent = "Updating Salesforce… this may take several minutes.";
    const result = await postJson("/api/write-all", { oppIds: preview.idsToAdd, confirm: "CONFIRM" });
    Object.entries(result.states || {}).forEach(([id, status]) =>
      setRowState(id, status));
    message.className = result.failed ? "msg err" : "msg ok";
    message.textContent = fmtCount(result.succeeded) + " fully updated · " + fmtCount(result.fieldPopulated) + " PSE fields populated · " + fmtCount(result.teamAdded) + " team adds · " +
      fmtCount(result.splitAdded) + " split adds" + (result.failed ? " · " + fmtCount(result.failed) + " incomplete" : "") +
      ((result.inactive || result.changed || result.excluded) ? " · " + fmtCount((result.inactive || 0) + (result.changed || 0) + (result.excluded || 0)) + " skipped" : "");
    if ((result.errors || []).length) message.textContent += " · " + result.errors.map((item) => (item.oppId ? item.oppId + ": " : "") + item.message).join("; ");
    render();
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;
    message.className = "msg err";
    message.textContent = "Update failed: " + error.message;
  } finally {
    setBusy(false);
  }
}

async function reloadSnapshot() {
  const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });
  if (!response.ok) throw new Error("Could not reload the dashboard snapshot.");
  if (!hydrate(await response.json())) throw new Error("The saved data does not match the current dashboard scope. Click Refresh again.");
}

async function refreshData() {
  if (state.busy) return;
  setBusy(true);
  const button = $("refreshBtn");
  const message = $("refreshMsg");
  button.disabled = true; button.textContent = "Refreshing…";
  message.className = "sub";
  message.textContent = "Refreshing Salesforce… This can take a few minutes.";
  try {
    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required to refresh the dashboard.");
    const result = await postJson("/api/refresh", { manual: true });
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
    message.textContent = "Refreshed · " + fmtCount((result.summary || state.summary).total) + " in-scope Partner Opportunities";
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce session expired")) state.writeApi.loggedIn = false;
    message.className = "sub err"; message.textContent = "Refresh failed: " + error.message;
  } finally { button.textContent = "Refresh"; setBusy(false); }
}

function csvCell(value) { return '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"'; }
function exportCsv() {
  const rows = filteredRows();
  const columns = ["id", "name", "stage", "closeDate", "fiscalQuarter", "type", "account", "accountOwner", "segment", "region", "partnerName", "partnerOwnerName", "partnerRepName", "partnerRepId", "partnerRepNeedsPopulate", "partnerRepActive", "partnerRepOnTeam", "partnerRepOnSplit"];
  const lines = [columns.join(","), ...rows.map((row) => columns.map((key) => csvCell(row[key])).join(","))];
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = "pse_to_update.csv"; anchor.click(); URL.revokeObjectURL(url);
}

function wire() {
  if (wired) return;
  wired = true;
  document.querySelectorAll("[data-pse-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.pseCategory = button.dataset.pseCategory;
      state.updatePage = 1;
      $("bulkMsg").textContent = "";
      renderUpdate();
    });
  });
  const process = $("processFlow");
  let priorOverflow = "";
  $("processFlowBtn").addEventListener("click", () => {
    priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    process.showModal();
    process.scrollTop = 0;
  });
  $("closeProcessFlow").addEventListener("click", () => process.close());
  process.addEventListener("close", () => {
    document.body.style.overflow = $("uploadPage").open ? "hidden" : priorOverflow;
    if (!$("uploadPage").open) $("processFlowBtn").focus({ preventScroll: true });
  });
  $("search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    state.query = event.target.value.trim().toLowerCase();
    state.updatePage = 1;
    searchTimer = setTimeout(render, 160);
  });
  [["quarterFilter", "fiscalQuarter"], ["typeFilter", "type"], ["segmentFilter", "segment"], ["regionFilter", "region"], ["pseStatusFilter", "pseStatus"]].forEach(([id, key]) => {
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
  let uploadPriorOverflow = "";
  const openUploadPage = () => {
    if ($("uploadPage").open) return;
    uploadPriorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    $("uploadPage").showModal();
    $("uploadPage").scrollTop = 0;
  };
  $("uploadDataBtn").addEventListener("click", openUploadPage);
  const openSetupInstructions = () => {
    if (process.open) process.close();
    openUploadPage();
    $("setupInstructions").showModal();
    $("setupInstructions").scrollTop = 0;
    $("copySetupMsg").textContent = "";
  };
  $("firstTimeProcessLink").addEventListener("click", openSetupInstructions);
  $("firstTimeUploadLink").addEventListener("click", openSetupInstructions);
  $("closeSetupInstructions").addEventListener("click", () => $("setupInstructions").close());
  $("setupInstructions").addEventListener("close", () => {
    $("firstTimeUploadLink").focus({ preventScroll: true });
  });
  $("copySetupPrompt").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("setupPrompt").value);
      $("copySetupMsg").textContent = "Copied. Paste the prompt into your Codex chat and send it.";
    } catch (_) {
      $("setupPrompt").focus();
      $("setupPrompt").select();
      $("copySetupMsg").textContent = "Select and copy the highlighted prompt, then paste it into your Codex chat.";
    }
  });
  $("closeUploadPage").addEventListener("click", () => $("uploadPage").close());
  $("uploadPage").addEventListener("close", () => {
    document.body.style.overflow = uploadPriorOverflow;
    $("uploadDataBtn").focus({ preventScroll: true });
  });
  $("uploadFile").addEventListener("change", event => previewUpload(event.target.files[0]));
  $("confirmUploadBtn").addEventListener("click", () => uploadData());
  $("refreshBtn").addEventListener("click", () => refreshData());
  $("csvBtn").addEventListener("click", exportCsv);
}

load();
