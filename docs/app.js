/* Partner → PSE field → Opportunity Team → Opportunity Splits. */
const SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";
const TEAM_ROLE = "Partner Account Executive";
const SPLIT_TYPE = "Overlay";
const SPLIT_PERCENTAGE = 100;
const PAGE_SIZE = 100;
const SCOPE_VERSION = 6;
const EXCLUDED_PARTNER_TYPE = "Startup Program Partner";
const CLOSED_STAGE = "08 - Closed";
const state = {
  rows: [], rowsById: new Map(), summary: {}, type: new Set(), segment: new Set(), region: new Set(), pseStatus: new Set(), fiscalQuarter: new Set(), month: new Set(),
  filterOptions: { type: [], segment: [], region: [], pseStatus: [], fiscalQuarter: [], month: [] }, query: "",
  sortKey: "closeDate", sortDir: -1, updatePage: 1, pseCategory: "all", readinessFilter: "all", busy: false,
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

function fiscalWindowBounds(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {timeZone:'Asia/Manila',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
  const part = key => parts.find(item=>item.type===key).value;
  const current = quarterMonths(fiscalQuarterFromDate(part('year')+'-'+part('month')+'-'+part('day')))[0];
  const [year, month] = current.split('-').map(Number);
  const stamp = offset => {
    const date = new Date(Date.UTC(year,month-1+offset,1));
    return date.toISOString().slice(0,10);
  };
  return [stamp(-3), stamp(3)];
}
function inFiscalWindow(value, bounds = fiscalWindowBounds()) {
  return fiscalQuarterFromDate(value) !== 'Close date unavailable' && value >= bounds[0] && value < bounds[1];
}

function fmtCount(value) { return Number(value || 0).toLocaleString("en-US"); }
function monthLabel(value) {
  const [year,month] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('en-US',{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(Date.UTC(year,month-1,1)));
}
function quarterMonths(value) {
  const match=/^FY(\d{4}) Q([1-4])$/.exec(value);
  if(!match)return [];
  const year=Number(match[1])-1,start=1+(Number(match[2])-1)*3;
  return [0,1,2].map(offset=>{
    const date=new Date(Date.UTC(year,start+offset,1));
    return date.getUTCFullYear()+'-'+String(date.getUTCMonth()+1).padStart(2,'0');
  });
}
function updateMonthOptions() {
  const quarters=[...state.fiscalQuarter].sort((a,b)=>b.localeCompare(a));
  const values=quarters.length ? [...new Set(quarters.flatMap(quarterMonths))]
    : [...new Set(state.rows.map(row=>row.closeMonth).filter(Boolean))].sort((a,b)=>b.localeCompare(a));
  renderFilter('monthFilter','month',values);
}
function filterValue(row,key) {
  if(key==='pseStatus')return pseStatus(row);
  if(key==='region')return row.partnerRegion;
  if(key==='month')return row.closeMonth;
  if(key==='type')return row.type||'Type unavailable';
  return row[key];
}
function opportunityKey(id) {
  const value = String(id || "").trim();
  return /^006[A-Za-z0-9]{15}$/.test(value) ? value.slice(0, 15) : value;
}
function uniqueOpportunityRows(rows) {
  const unique = new Map();
  for (const row of rows) {
    const key = opportunityKey(row.id);
    if (!key) continue;
    const previous = unique.get(key);
    unique.set(key, { ...row, id: previous?.id?.length === 18 && String(row.id).trim().length === 15 ? previous.id : String(row.id).trim() });
  }
  return [...unique.values()];
}
function detailsLine(row) {
  return [row.id || "—", row.account || "—", row.type || "—", fmtDate(row.closeDate)].join(" · ");
}
function partnerDetailsLine(row) {
  return [row.partnerRegion, row.partnerDealSource, row.dealType, row.partnerType]
    .map((value) => value == null || value === "" ? "—" : value).join(" · ");
}

async function load() {
  wire();
  try {
    const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });
    if (!response.ok) {
      const details = await response.json().catch(() => ({}));
      await detectApi();
      const status = $("status");
      if (details.error && !(response.status === 404 && state.writeApi.refreshEnabled)) {
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
    : choices.size === 1 ? (key==='month'?monthLabel([...choices][0]):[...choices][0]) : choices.size + " " + label + " selected";
  const menu = $(id + "Menu");
  menu.innerHTML = values.map((value) =>
    '<label class="filter-option"><input type="checkbox" data-filter-option value="' + esc(value) + '"' +
    (choices.has(value) ? " checked" : "") + '><span>' + esc(key==='month'?monthLabel(value):value) + '</span></label>').join("") +
    '<div class="filter-actions"><button type="button" data-filter-select-all>Select all</button>' +
    '<button type="button" data-filter-clear>Clear</button></div>';
}

function hydrate(data) {
  wire();
  state.generatedAt = data.generatedAt;
  const scope = (data.config || {}).scopeVersion;
  if (scope !== SCOPE_VERSION) {
    state.rows = [];
    state.rowsById.clear();
    $("dashboard").classList.add("hidden");
    const status = $("status");
    status.className = "status";
    status.textContent = "This dashboard needs a current Salesforce snapshot. Click Refresh to load the latest data.";
    return false;
  }
  const fiscalBounds = fiscalWindowBounds();
  state.rows = uniqueOpportunityRows(data.rows || []).filter(row=>inFiscalWindow(row.closeDate, fiscalBounds)).map((row) => ({ ...row, fiscalQuarter: fiscalQuarterFromDate(row.closeDate), closeMonth:fiscalQuarterFromDate(row.closeDate)==='Close date unavailable'?null:row.closeDate.slice(0,7) }));
  state.rowsById = new Map(state.rows.map((row) => [row.id, row]));
  const completeCount = state.rows.filter(row=>rowComplete(row)).length;
  state.summary = { ...(data.summary || {}), total:state.rows.length, tagged:completeCount, needsUpdate:state.rows.length-completeCount,
    needsPopulate:state.rows.filter(row=>row.partnerRepNeedsPopulate).length };
  const generated = data.generatedAt ? new Date(data.generatedAt) : null;
  $("who").textContent = generated ? "Refreshed " + generated.toLocaleString() : "";
  renderFilter("pseStatusFilter", "pseStatus", ["Active", "Inactive", ...(state.rows.some((row) => row.partnerRepActive !== true && row.partnerRepActive !== false) ? ["Status unavailable"] : [])]);
  renderFilter("quarterFilter", "fiscalQuarter", [...new Set(state.rows.map((row) => row.fiscalQuarter))].sort((a, b) => b.localeCompare(a)));
  updateMonthOptions();
  renderFilter("typeFilter", "type", [...new Set(state.rows.map((row) => row.type || "Type unavailable"))].sort());
  renderFilter("segmentFilter", "segment", [...new Set(state.rows.map((row) => row.segment).filter(Boolean))].sort());
  renderFilter("regionFilter", "region", [...new Set(state.rows.map((row) => row.partnerRegion).filter(Boolean))].sort());
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
    $("refreshBtn").classList.toggle("hidden", !health.refreshEnabled);
    $("who").textContent = $("who").textContent.split(" · Salesforce org:")[0];
    if (state.writeApi.enabled) $("who").textContent += " · Salesforce org: " + state.writeApi.org;
    renderUpdate();
  } catch (_) { /* Static hosting has no local Salesforce API. */ }
}

function pseStatus(row) { return row.partnerRepActive === true ? "Active" : row.partnerRepActive === false ? "Inactive" : "Status unavailable"; }

function rowComplete(row) { return row.updatePlan ? row.updatePlan.status === "complete" : !row.partnerRepNeedsPopulate && row.partnerRepOnTeam && row.partnerRepOnSplit; }

function pseMatchesPartnerOwner(row) {
  // Salesforce IDs identify users even when different users share a name.
  if (row.partnerOwnerId && row.partnerRepId) return row.partnerOwnerId.slice(0, 15) === row.partnerRepId.slice(0, 15);
  // Older exports have names but no Partner Owner ID; keep them usable.
  const owner = String(row.partnerOwnerName || "").trim().toLowerCase();
  const rep = String(row.partnerRepName || "").trim().toLowerCase();
  if (!owner || !rep || rep === "(user unavailable)") return null;
  return owner === rep;
}

function matchesCategory(row, category) {
  const missingMembership = row.updatePlan ? row.updatePlan.status !== "complete" : !row.partnerRepOnTeam || !row.partnerRepOnSplit;
  if (category === "tagged") return !row.partnerRepNeedsPopulate && pseMatchesPartnerOwner(row) === true && missingMembership;
  if (category === "different") return !row.partnerRepNeedsPopulate && pseMatchesPartnerOwner(row) === false && missingMembership;
  if (category === "untagged") return !!row.partnerRepNeedsPopulate && missingMembership;
  return missingMembership;
}

function filteredRows(category = state.pseCategory, applyReadiness = true) {
  const fiscalBounds = fiscalWindowBounds();
  let rows = uniqueOpportunityRows(state.rows).filter((row) => row.inScope !== false && row.nonCommissionable === false && row.stage === CLOSED_STAGE &&
    Object.prototype.hasOwnProperty.call(row, "partnerType") && row.partnerType !== EXCLUDED_PARTNER_TYPE &&
    row.partnerName !== "AWS Marketplace" && inFiscalWindow(row.closeDate, fiscalBounds) && row.partnerId && !rowComplete(row));
  ["type", "segment", "region", "pseStatus", "fiscalQuarter", "month"].forEach((key) => {
    const selected = state[key];
    const options = state.filterOptions[key];
    if (selected.size && (key==='month' || key==='region' || selected.size < options.length)) rows = rows.filter((row) => selected.has(filterValue(row,key)));
  });
  if (state.query) {
    const q = state.query;
    rows = rows.filter((row) => [row.name, row.account, row.id, row.accountOwner, row.partnerRepName]
      .some((value) => String(value == null ? "" : value).toLowerCase().includes(q)));
  }
  rows = rows.filter((row) => matchesCategory(row, category));
  if (applyReadiness && state.readinessFilter !== 'all') rows = rows.filter(row=>readiness(row)===state.readinessFilter);
  const key = state.sortKey;
  return [...rows].sort((a, b) => {
    const av = a[key] == null ? "" : a[key];
    const bv = b[key] == null ? "" : b[key];
    const compare = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
    return compare * state.sortDir;
  });
}

function readiness(row) {
  return row.updatePlan?.status || "blocked";
}
function opportunityAccessLabel(value) {
  return value === 'Read' ? 'Read Only' : value || 'Access unavailable';
}
function planStep(title, current, required, action, okay) {
  return '<section class="step"><div class="step-title">' + esc(title) + '</div>' +
    '<div class="plan-label">Current</div><div class="plan-current">' + esc(current) + '</div>' +
    '<div class="plan-required"><div class="plan-label">Required</div>' + required + '</div>' +
    '<div class="plan-action' + (okay ? ' good' : '') + '">' + esc(action) + '</div></section>';
}
function opportunityCard(row) {
  const href = SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + encodeURIComponent(row.id) + "/view";
  const p = row.updatePlan || { status:"blocked", reason:"Click Refresh to load the actual Team and Split roles.", changes:[] };
  const status = readiness(row), ready = status === 'ready', review = status === 'review' || status === 'rvp';
  const names = { ready:'Ready to update', review:'Needs review', rvp:'Needs RVP confirmation', blocked:'Blocked', complete:'Complete' };
  const mismatch = !row.partnerRepNeedsPopulate && pseMatchesPartnerOwner(row) === false;
  const nameClass = mismatch ? ' mismatch-name' : '';
  const target = p.targetName || row.partnerRepName || '—', role = p.targetRole || TEAM_ROLE;
  const team = p.teamCurrent || [], splits = p.splitCurrent || [];
  const currentTeam = row.membershipDetailsLoaded ? (team.length ? team.map(item => (item.User?.Name || target) + ' · ' + (item.TeamMemberRole || 'Role blank') + ' · ' + opportunityAccessLabel(item.OpportunityAccessLevel)).join('; ') : 'Not on the Opportunity Team') : 'Not loaded — click Refresh';
  const currentSplit = row.membershipDetailsLoaded ? (splits.length ? splits.map(item => (item.SplitOwner?.Name || target) + ' · ' + (item.Opportunity_Team_Role__c || 'Role blank') + ' · ' + (item.SplitType?.MasterLabel || 'Type unavailable') + ' · ' + (item.SplitPercentage == null ? '% unavailable' : item.SplitPercentage + '%') + ' · Coverage: ' + (item.Coverage__c === true ? 'Checked' : item.Coverage__c === false ? 'Unchecked' : 'Not loaded — click Refresh')).join('; ') : 'No Split for this PSE') : 'Not loaded — click Refresh';
  const action = (object) => review ? 'On hold — confirm assignment' : status==='blocked' ? 'Review required' : (p.changes || []).find(item => item.object === object)?.action || 'Already correct · No change';
  const correct = (object) => (ready || status==='complete') && !p.changes?.some(item=>item.object===object);
  const targetName = review ? 'Awaiting confirmed PSE' : target;
  const requiredTeam = esc(targetName) + '<br>' + esc(role) + '<br><span class="sub">' + esc('Opportunity Access: ' + opportunityAccessLabel(team[0]?.OpportunityAccessLevel || 'Read')) + '</span>';
  const splitRole = p.targetSplitRole || (['Partner Account Executive','Partner Account Executive - Coverage'].includes(splits[0]?.Opportunity_Team_Role__c) ? splits[0].Opportunity_Team_Role__c : role);
  const coverageFlag = splits.some(item => item.Opportunity_Team_Role__c === 'Partner Account Executive - Coverage' && item.Coverage__c === false);
  const requiredSplit = esc(targetName) + '<br>' + esc(splitRole) + '<br><span class="sub">' + esc(splits.length ? 'Keep existing type and percentage' : 'Overlay · 100.00%') + '</span>' + (coverageFlag ? '<div class="plan-action">Flag: Coverage role has Coverage unchecked. Verify in Salesforce.</div>' : '') + '<div class="sub">Coverage is read from Salesforce. Checking or refreshing does not change it.</div>';
  const steps = planStep('1 · Partner Sales Rep field', p.fieldCurrent || (row.partnerRepNeedsPopulate ? 'Blank' : row.partnerRepName || '—'), esc(targetName), action('Opportunity'), correct('Opportunity')) +
    planStep('2 · Check Opportunity Team',currentTeam,requiredTeam,action('OpportunityTeamMember'),correct('OpportunityTeamMember')) +
    planStep('3 · Check Opportunity Splits',currentSplit,requiredSplit,action('OpportunitySplit'),correct('OpportunitySplit'));
  const reviewButton = review ? '<button class="ghost" data-review-open="' + esc(row.id) + '">' + (status==='rvp'?'Set covering PSE':'Review assignment') + '</button>' : '';
  const updateButton = '<button class="btn-update" data-update="' + esc(row.id) + '"' + ((!ready || state.busy || !state.writeApi.enabled) ? ' disabled' : '') + '>' + (ready?'Update in Salesforce →':names[status]) + '</button>';
  const form = review ? '<section class="assignment-review hidden" data-review-panel="' + esc(row.id) + '"><b>' + (status==='rvp'?'Record the RVP-approved covering PSE':'Confirm the PSE assignment') + '</b><div class="review-fields"><label>Search active Salesforce users<input type="search" data-user-query placeholder="Enter a name"></label><button class="ghost" data-user-search="' + esc(row.id) + '">Search users</button><label>Confirmed PSE<select data-review-user>' + (row.partnerRepActive===true?'<option value="'+esc(row.partnerRepId)+'">'+esc(row.partnerRepName)+'</option>':'<option value="">Choose an active PSE</option>') + '</select></label><label>Required role<select data-review-role><option>' + TEAM_ROLE + '</option><option' + (status==='rvp'?' selected':'') + '>Partner Account Executive - Coverage</option></select></label></div><label class="review-confirm"><input type="checkbox" data-review-confirm> ' + (status==='rvp'?'RVP confirmation received for this covering PSE':'I reviewed and confirmed this assignment') + '</label><div class="review-fields"><button data-review-save="' + esc(row.id) + '">Confirm assignment</button><span class="sub">Saves the review locally. Salesforce changes require Update confirmation.</span></div><div data-review-message class="msg" aria-live="polite"></div></section>' : '';
  return '<article class="action-card needs-update" data-card="' + esc(row.id) + '"><div class="action-head"><div class="head-main"><div class="opp-title"><a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(row.name || 'Unnamed Opportunity') + '</a></div><div class="details-line">' + esc(detailsLine(row)) + '</div><div class="details-line">' + esc(partnerDetailsLine(row)) + '</div><div class="details-line">Partner: ' + esc(row.partnerName || '—') + ' · Partner Owner: <span class="' + nameClass.trim() + '">' + esc(row.partnerOwnerName || '—') + '</span></div><div class="pse-tag"><b>' + (row.partnerRepNeedsPopulate?'PSE to populate':'PSE Tagged') + '</b><span class="pse-name' + nameClass + '">' + esc(row.partnerRepName || '—') + '</span><span class="pill' + (row.partnerRepActive!==true?' warn':'') + '">' + pseStatus(row) + '</span></div><div class="readiness-row"><span class="readiness-badge ' + status + '">' + names[status] + '</span><span class="sub">' + esc(p.reason || (p.changes?.length || 0) + ' changes needed') + '</span></div><div class="msg" data-msg="' + esc(row.id) + '" aria-live="polite"></div></div></div><div class="action-steps">' + steps + '</div>' + form + '<div class="action-footer"><span class="sub">Existing correct entries and split percentages are preserved.</span><div class="review-fields">' + reviewButton + updateButton + '</div></div></article>';
}

function wireCardUpdates(containerId) {
  $(containerId).querySelectorAll("button[data-update]").forEach((button) =>
    button.addEventListener("click", () => updateOne(button.dataset.update, button)));
  $(containerId).querySelectorAll('button[data-review-open]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelector('[data-review-panel="'+CSS.escape(button.dataset.reviewOpen)+'"]').classList.toggle('hidden');
  }));
  $(containerId).querySelectorAll('button[data-user-search]').forEach(button=>button.addEventListener('click',async()=>{
    const panel=document.querySelector('[data-review-panel="'+CSS.escape(button.dataset.userSearch)+'"]');
    const message=panel.querySelector('[data-review-message]');
    button.disabled=true;message.textContent='Searching active Salesforce users…';
    try {
      const result=await postJson('/api/users',{search:panel.querySelector('[data-user-query]').value});
      panel.querySelector('[data-review-user]').innerHTML='<option value="">Choose an active PSE</option>'+result.users.map(user=>'<option value="'+esc(user.id)+'">'+esc(user.name)+'</option>').join('');
      message.textContent=result.users.length+' active users found.';
    } catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  }));
  $(containerId).querySelectorAll('button[data-review-save]').forEach(button=>button.addEventListener('click',async()=>{
    const id=button.dataset.reviewSave,panel=document.querySelector('[data-review-panel="'+CSS.escape(id)+'"]');
    const message=panel.querySelector('[data-review-message]');
    const confirmed=panel.querySelector('[data-review-confirm]').checked;
    if(!confirmed){message.textContent='Confirm the assignment before saving the review.';return;}
    const payload={oppId:id,userId:panel.querySelector('[data-review-user]').value,role:panel.querySelector('[data-review-role]').value,confirmed:true,rvpConfirmed:readiness(state.rowsById.get(id))==='rvp'};
    button.disabled=true;message.textContent='Checking and saving local review…';
    try{setRowState(id,await postJson('/api/review',payload));renderUpdate();}
    catch(error){message.textContent=error.message;button.disabled=false;}
  }));
}

function renderUpdate() {
  const allRows = filteredRows("all", false);
  const categoryRows = allRows.filter((row) => matchesCategory(row, state.pseCategory));
  const rows = categoryRows.filter(row=>state.readinessFilter==='all'||readiness(row)===state.readinessFilter);
  $("cWithPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "tagged")).length);
  $("cDifferentPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "different")).length);
  $("cWithoutPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "untagged")).length);
  $("cNeedsUpdate").textContent = fmtCount(allRows.length);
  document.querySelectorAll("[data-pse-category]").forEach((button) => {
    const selected = button.dataset.pseCategory === state.pseCategory;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("categoryLabel").textContent = {
    all: "Showing all Opportunities to update",
    tagged: "Showing Opportunities with tagged correct PSE · Partner Owner and PSE are the same · needs PSE, Team or Split changes",
    different: "Showing Opportunities with a tagged PSE different from the Partner Owner · needs PSE, Team or Split changes",
    untagged: "Showing Partner Opportunities without a tagged PSE · needs PSE, Team or Split changes",
  }[state.pseCategory];
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.updatePage = Math.min(state.updatePage, pageCount);
  const start = (state.updatePage - 1) * PAGE_SIZE;
  $("bulkCount").textContent = fmtCount(categoryRows.length);
  $("readyFilterCount").textContent = fmtCount(categoryRows.filter(row=>readiness(row)==='ready').length);
  $("rvpFilterCount").textContent = fmtCount(categoryRows.filter(row=>readiness(row)==='rvp').length);
  [['readinessAll','all'],['readinessReady','ready'],['readinessRvp','rvp']].forEach(([id,status])=>{
    $(id).setAttribute('aria-pressed',String(state.readinessFilter===status));
  });
  const readyCount = rows.filter(row=>readiness(row)==='ready').length;
  $("updateAllBtn").textContent = "Update all ready — " + readyCount.toLocaleString() + " in Salesforce →";
  $("bulkReadiness").textContent = categoryRows.filter(row=>readiness(row)==='review').length.toLocaleString() + " need review · " + categoryRows.filter(row=>readiness(row)==='blocked').length.toLocaleString() + " blocked";
  $("updateAllBtn").disabled = state.busy || !state.writeApi.enabled || readyCount === 0;
  $("actionList").innerHTML = rows.slice(start, start + PAGE_SIZE).map((row) => opportunityCard(row)).join("");
  wireCardUpdates("actionList");
  $("actionPageInfo").textContent = rows.length ? `Page ${state.updatePage} of ${pageCount}` : "";
  $("filterCount").textContent = fmtCount(rows.length) + " unique Opportunities" + ({all:' to update',ready:' ready to update',rvp:' needing RVP confirmation'}[state.readinessFilter]);
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
  catch (_) { throw new Error("The server response could not be read. Check Salesforce status before retrying an update."); }
  if (response.status === 401 || result.needsLogin) state.writeApi.loggedIn = false;
  if (!response.ok) {
    const error = new Error(result.error || ("HTTP " + response.status));
    error.status = response.status;
    throw error;
  }
  return result;
}

function ensureWriteLogin() { return Promise.resolve(true); }

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
  document.querySelectorAll('#search, .filter-trigger, .filter-menu input, .filter-actions button, [data-pse-category], #actionPrev, #actionNext, #csvBtn, #refreshBtn, #updateAllBtn, [data-readiness], button[data-update],button[data-review-open],button[data-user-search],button[data-review-save],.assignment-review input,.assignment-review select').forEach((control) => { control.disabled = value; });
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
      "\nChanges:\n" + (preview.updatePlan?.changes || []).map(item=>item.action).join("\n") +
      "\nTeam role: " + (preview.updatePlan?.targetRole || TEAM_ROLE) + " (Opportunity Access: Read Only)" +
      "\nExisting split types/percentages: preserved; new Split: 100% Overlay" +
      "\nSalesforce org: " + state.writeApi.org + "\n\nApply the reviewed PSE assignment, add missing entries and correct Team/Split roles as shown. Salesforce checks Coverage automatically for the Coverage Split role. Existing split types and percentages are preserved. Proceed?"
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
  const rows = filteredRows().filter(row=>readiness(row)==="ready");
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
      "?\n\nApply only ready records after a fresh Salesforce recheck. Populate blank or explicitly reviewed PSE assignments, add missing Team/Split entries, and correct their roles. Existing split types and percentages remain unchanged. New Splits use Overlay at 100%. Salesforce checks Coverage automatically for the Coverage Split role.\n\nRecords needing review or RVP confirmation, blocked records, and records that changed are skipped. This includes all filtered pages."
    );
    if (!okay) { message.textContent = "Cancelled — no Salesforce changes were made."; button.disabled = false; return; }
    message.textContent = "Updating Salesforce… this may take several minutes.";
    const result = await postJson("/api/write-all", { oppIds: preview.idsToAdd, confirm: "CONFIRM" });
    Object.entries(result.states || {}).forEach(([id, status]) =>
      setRowState(id, status));
    message.className = result.failed ? "msg err" : "msg ok";
    message.textContent = fmtCount(result.succeeded) + " fully updated · " + fmtCount(result.fieldPopulated) + " PSE fields populated · " + fmtCount(result.teamAdded) + " team adds · " +
      fmtCount(result.splitAdded) + " split adds · " + fmtCount(result.teamRolesUpdated) + " Team roles corrected · " + fmtCount(result.splitRolesUpdated) + " Split roles corrected" + (result.failed ? " · " + fmtCount(result.failed) + " incomplete" : "") +
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
    message.className = "sub";
    message.textContent = "";
  } catch (error) {
    if (error.message.toLowerCase().includes("salesforce session expired")) state.writeApi.loggedIn = false;
    message.className = "sub err"; message.textContent = "Refresh failed: " + error.message;
  } finally { button.textContent = "Refresh"; setBusy(false); }
}

function csvCell(value) { return '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"'; }
function exportCsv() {
  const rows = filteredRows();
  const columns = ["id", "name", "stage", "closeDate", "fiscalQuarter", "type", "account", "accountOwner", "segment", "region", "partnerName", "partnerType", "partnerOwnerName", "partnerOwnerId", "partnerRepName", "partnerRepId", "partnerRepNeedsPopulate", "partnerRepActive", "partnerRepOnTeam", "partnerRepOnSplit"];
  columns.push("amount", "partnerAccountType", "partnerRegion", "partnerOwnerProfile", "taggedPseProfile", "partnerDealSource", "dealType", "totalBookingArrUsd");
  const lines = [columns.join(","), ...rows.map((row) => columns.map((key) => csvCell(row[key])).join(","))];
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = "pse_to_update.csv"; anchor.click(); URL.revokeObjectURL(url);
}

function wire() {
  if (wired) return;
  wired = true;
  [['readinessAll','all'],['readinessReady','ready'],['readinessRvp','rvp']].forEach(([id,status])=>{
    $(id).addEventListener('click',()=>{
      if(state.busy)return;
      state.readinessFilter=status;
      state.updatePage=1;
      $("bulkMsg").textContent='';
      renderUpdate();
    });
  });
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
    document.body.style.overflow = priorOverflow;
    $("processFlowBtn").focus({ preventScroll: true });
  });
  $("search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    state.query = event.target.value.trim().toLowerCase();
    state.updatePage = 1;
    searchTimer = setTimeout(render, 160);
  });
  [["quarterFilter", "fiscalQuarter"], ["monthFilter", "month"], ["typeFilter", "type"], ["segmentFilter", "segment"], ["regionFilter", "region"], ["pseStatusFilter", "pseStatus"]].forEach(([id, key]) => {
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
      if(key==='fiscalQuarter')updateMonthOptions();
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
      if(key==='fiscalQuarter')updateMonthOptions();
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
  $("refreshBtn").addEventListener("click", () => refreshData());
  $("csvBtn").addEventListener("click", exportCsv);
}

load();
