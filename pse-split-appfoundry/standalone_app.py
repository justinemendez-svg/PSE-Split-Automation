"""PSE Split Automation AppFoundry app.

The AppFoundry deployment keeps the latest Salesforce snapshot in Postgres.
Refresh reads commissionable Renewal, New Business, and Expansion opportunities
with an active Partner Sales Rep. Salesforce writes remain previewed and
confirmed, matching the local dashboard behavior.
"""

from __future__ import annotations

import datetime as dt
import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, session
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR.parent / "docs"
INDEX_HTML = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8" />\n<meta name="viewport" content="width=device-width, initial-scale=1" />\n<title>PSE Split Automation</title>\n<style>\n  :root { --bg:#f6f8f9; --panel:#fff; --line:#e2e7eb; --text:#1b2733; --muted:#68737d; --teal:#17494d; --lime:#78a300; --warn:#b26a00; --ok:#3f8c00; --chip:#eef1f3; }\n  * { box-sizing:border-box; }\n  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }\n  header { display:flex; align-items:center; gap:16px; flex-wrap:wrap; padding:16px 24px; background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:10; }\n  h1 { margin:0; font-size:18px; font-weight:680; } .sub { color:var(--muted); font-size:12px; }\n  .spacer { flex:1; }\n  button { border:0; border-radius:8px; padding:9px 15px; background:var(--teal); color:white; font:inherit; font-weight:650; cursor:pointer; }\n  button:hover { background:#23646a; } button:disabled { opacity:.5; cursor:wait; }\n  button.ghost { border:1px solid var(--line); background:white; color:var(--text); } button.ghost:hover { background:#f3f5f7; }\n  main { max-width:1500px; margin:0 auto; padding:24px; }\n  .cards { display:grid; grid-template-columns:repeat(4,minmax(150px,1fr)); gap:14px; margin-bottom:20px; }\n  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:15px 18px; min-height:96px; }\n  .card .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.45px; }\n  .card .v { font-size:28px; font-weight:720; margin-top:5px; } .card.ok .v { color:var(--ok); } .card.warn .v { color:var(--warn); }\n  .toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:12px; }\n  input[type=search],select { min-height:40px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:#f3f5f7; color:var(--text); font:inherit; }\n  input[type=search] { flex:1; min-width:250px; } select { min-width:150px; cursor:pointer; }\n  .multi-filter { position:relative; }\n  .filter-trigger { display:flex; align-items:center; justify-content:space-between; gap:16px; min-width:170px; min-height:40px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:#f3f5f7; color:var(--text); font:inherit; font-weight:500; text-align:left; }\n  .filter-trigger::after { content:"▾"; color:var(--muted); font-size:12px; }\n  .filter-menu { position:absolute; z-index:20; top:calc(100% + 5px); left:0; width:240px; max-height:300px; overflow:auto; border:1px solid var(--line); border-radius:9px; background:white; box-shadow:0 8px 24px rgba(27,39,51,.14); }\n  .filter-menu.hidden { display:none!important; }\n  .filter-option { display:flex; align-items:center; gap:9px; padding:8px 12px; cursor:pointer; }\n  .filter-option:hover { background:#f3f5f7; }\n  .filter-option input { accent-color:var(--teal); }\n  .filter-actions { position:sticky; bottom:0; display:flex; justify-content:space-between; padding:7px 10px; border-top:1px solid var(--line); background:#fff; }\n  .filter-actions button { padding:4px 6px; background:transparent; color:var(--teal); font-size:12px; }\n  .filter-actions button:hover { background:#f3f5f7; }\n  h2 { margin:16px 0 12px; font-size:17px; font-weight:680; }\n  .opp-title { font-size:15px; font-weight:680; }\n  .account-name { font-weight:630; } .pse-name { font-weight:650; color:var(--teal); }\n  .pill { display:inline-block; border-radius:999px; padding:2px 9px; background:#e9f3e9; color:var(--ok); font-size:11px; font-weight:700; white-space:nowrap; }\n  .pill.warn { background:#fff0d4; color:var(--warn); }\n  .bulk-bar { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:14px; padding:13px 17px; background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:inset 3px 0 0 var(--warn); }\n  .bulk-info { font-weight:680; }\n  .action-list { display:grid; gap:14px; }\n  .action-card { overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:inset 3px 0 0 var(--warn); }\n  .action-card.is-tagged { box-shadow:inset 3px 0 0 var(--ok); }\n  .action-head { display:flex; align-items:flex-start; gap:16px; padding:15px 18px; }\n  .head-main { flex:1; min-width:0; } .action-head .btn-update { flex-shrink:0; white-space:nowrap; }\n  a.btn-update { display:inline-flex; align-items:center; border-radius:8px; padding:9px 15px; background:var(--teal); color:#fff; font-weight:650; text-decoration:none; }\n  a.btn-update:hover { background:#23646a; }\n  .details-line { margin-top:4px; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }\n  .pse-tag { display:inline-flex; align-items:center; gap:8px; margin-top:9px; padding:4px 10px; border-radius:7px; background:#eaf2f1; color:var(--teal); }\n  .action-steps { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:28px; padding:14px 18px 16px; border-top:1px solid var(--line); }\n  .step { min-width:0; }\n  .step-title { font-size:14px; font-weight:700; }\n  .step-path { margin-top:3px; color:var(--muted); font-size:12px; }\n  .step-values { width:min(100%,420px); margin-top:4px; border-collapse:collapse; overflow:hidden; border-radius:8px; background:#f1f3f5; }\n  .step-values td { padding:7px 14px; border-bottom:1px solid var(--line); }\n  .step-values tr:last-child td { border-bottom:0; }\n  .step-values td:first-child { color:var(--muted); font-weight:650; white-space:nowrap; }\n  .step-values td:last-child { font-weight:650; }\n  .also { margin-top:7px; color:var(--muted); font-size:12px; }\n  .msg { margin-top:5px; font-weight:650; } .msg.ok { color:var(--ok); } .msg.err { color:#b42318; }\n  .pagination { display:flex; justify-content:flex-end; align-items:center; gap:9px; padding:11px 2px; color:var(--muted); font-size:12px; }\n  .pagination button { padding:6px 11px; font-size:12px; }\n  .empty,.status { padding:40px 20px; text-align:center; color:var(--muted); } .status.err { color:#b42318; }\n  .hidden { display:none!important; } #refreshMsg.err { color:#b42318; } #refreshMsg.ok { color:var(--ok); }\n  a { color:var(--teal); text-decoration:none; } a:hover { text-decoration:underline; }\n  @media(max-width:900px) { .cards { grid-template-columns:repeat(2,minmax(145px,1fr)); } .action-head { flex-direction:column; } .action-steps { grid-template-columns:1fr; gap:14px; } }\n  @media(max-width:600px) { main { padding:16px; } header { padding:14px 16px; } .cards { gap:8px; } input[type=search] { min-width:100%; } }\n</style>\n</head>\n<body>\n<header>\n  <div><h1>PSE Split Automation</h1><div class="sub">Active Partner Sales Rep coverage</div></div>\n  <div class="spacer"></div>\n  <span id="refreshMsg" class="sub" aria-live="polite"></span><span id="who" class="sub"></span>\n  <button class="ghost hidden" id="refreshBtn">Refresh data</button><button id="csvBtn">Export CSV</button>\n</header>\n<main>\n  <div id="status" class="status">Loading dashboard data…</div>\n  <section id="dashboard" class="hidden">\n    <div class="cards">\n      <div class="card"><div class="k">Total active PSE opps</div><div class="v" id="cTotal">—</div></div>\n      <div class="card ok"><div class="k">PSE fully set up (Team + Split)</div><div class="v" id="cTagged">—</div></div>\n      <div class="card warn"><div class="k">PSE to update</div><div class="v" id="cNeedsUpdate">—</div></div>\n      <div class="card"><div class="k">Scope</div><div class="v" style="font-size:15px">Commissionable</div><div class="sub">Renewal · New Business · Expansion<br />All stages &amp; dates</div></div>\n    </div>\n    <div class="toolbar">\n      <input type="search" id="search" placeholder="Search Opportunity name, Account name, or Opp ID…" />\n      <div class="multi-filter" id="typeFilter" data-filter-key="type" data-filter-label="types">\n        <button type="button" class="filter-trigger" id="typeFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="typeFilterMenu">All types</button>\n        <div class="filter-menu hidden" id="typeFilterMenu" role="group" aria-label="Opportunity Type options"></div>\n      </div>\n      <div class="multi-filter" id="segmentFilter" data-filter-key="segment" data-filter-label="segments">\n        <button type="button" class="filter-trigger" id="segmentFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="segmentFilterMenu">All segments</button>\n        <div class="filter-menu hidden" id="segmentFilterMenu" role="group" aria-label="Market Segment options"></div>\n      </div>\n      <div class="multi-filter" id="regionFilter" data-filter-key="region" data-filter-label="regions">\n        <button type="button" class="filter-trigger" id="regionFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="regionFilterMenu">All regions</button>\n        <div class="filter-menu hidden" id="regionFilterMenu" role="group" aria-label="Region options"></div>\n      </div>\n      <span class="spacer"></span><span id="filterCount" class="sub"></span>\n    </div>\n    <section id="panelUpdate">\n      <h2>PSE to update</h2>\n      <div id="bulkBar" class="bulk-bar">\n        <div class="bulk-info"><span id="bulkCount">0</span> Opps need the active PSE added to the team and split</div>\n        <button id="updateAllBtn">Update all 0 in Salesforce →</button><span id="bulkMsg" class="msg"></span>\n      </div>\n      <div id="actionList" class="action-list"></div>\n      <div id="actionEmpty" class="empty hidden">No active PSE needs a Team or Overlay Split update for these filters.</div>\n      <div class="pagination"><span id="actionPageInfo"></span><button class="ghost" id="actionPrev">Previous</button><button class="ghost" id="actionNext">Next</button></div>\n    </section>\n    <footer class="sub">This list includes commissionable Renewal, New Business, and Expansion Opportunities with an active rep in <code>Partner_Sales_Rep__c</code> who is missing from the Opportunity Team, the 100% Overlay split, or both. Updates add only missing entries, after a confirmation preview.</footer>\n  </section>\n</main>\n<script src="app.js"></script>\n</body>\n</html>\n'
APP_JS = '/* PSE Split Automation — active PSEs missing from Opportunity Teams. */\nconst SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";\nconst TEAM_ROLE = "Partner Account Executive";\nconst SPLIT_TYPE = "Overlay";\nconst SPLIT_PERCENTAGE = 100;\nconst PAGE_SIZE = 100;\nconst OPPORTUNITY_TYPES = ["Renewal", "New Business", "Expansion"];\nconst SCOPE_VERSION = 2;\nconst state = {\n  rows: [], summary: {}, type: new Set(), segment: new Set(), region: new Set(),\n  filterOptions: { type: OPPORTUNITY_TYPES, segment: [], region: [] }, query: "",\n  sortKey: "closeDate", sortDir: -1, updatePage: 1,\n  writeApi: { enabled: false, refreshEnabled: false, org: null, authMode: "local", loggedIn: true },\n};\nconst $ = (id) => document.getElementById(id);\nlet wired = false;\nlet searchTimer = null;\n\nfunction esc(value) {\n  return String(value == null ? "" : value).replace(/[&<>"\']/g, (ch) =>\n    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[ch]));\n}\nfunction fmtDate(value) {\n  if (!value) return "—";\n  const date = new Date(value + "T00:00:00");\n  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" });\n}\nfunction fmtCount(value) { return Number(value || 0).toLocaleString("en-US"); }\nfunction detailsLine(row) {\n  return [row.id, row.stage, fmtDate(row.closeDate), row.type].filter(Boolean).join(" · ");\n}\nfunction accountLine(row) {\n  return [row.account, row.accountOwner, row.segment, row.region].filter(Boolean).join(" · ") || "—";\n}\n\nasync function load() {\n  wire();\n  try {\n    const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });\n    if (!response.ok) {\n      await detectApi();\n      const status = $("status");\n      status.className = state.writeApi.refreshEnabled ? "status" : "status err";\n      status.textContent = state.writeApi.refreshEnabled\n        ? "No Salesforce snapshot yet. Click Refresh data to load the first dashboard snapshot."\n        : "Dashboard data is not available. Run `python3 refresh.py --org prod`, then reload. (HTTP " + response.status + ")";\n      return;\n    }\n    hydrate(await response.json());\n    await detectApi();\n  } catch (error) {\n    await detectApi();\n    const status = $("status");\n    status.className = state.writeApi.refreshEnabled ? "status" : "status err";\n    status.textContent = state.writeApi.refreshEnabled\n      ? "Dashboard snapshot could not be loaded. Click Refresh data to fetch it from Salesforce. (" + error.message + ")"\n      : window.location.protocol === "file:"\n        ? "Open the dashboard through its local server. Double-click serve.command, then open http://localhost:8773 in Chrome."\n        : "Dashboard data is not available. Run `python3 refresh.py --org prod`, then reload. (" + error.message + ")";\n  }\n}\n\nfunction renderFilter(id, key, values) {\n  const component = $(id);\n  const selected = state[key];\n  const available = new Set(values);\n  state[key] = new Set([...selected].filter((value) => available.has(value)));\n  state.filterOptions[key] = values;\n  const choices = state[key];\n  const label = component.dataset.filterLabel;\n  const button = $(id + "Button");\n  button.textContent = !choices.size || choices.size === values.length\n    ? "All " + label\n    : choices.size === 1 ? [...choices][0] : choices.size + " " + label + " selected";\n  const menu = $(id + "Menu");\n  menu.innerHTML = values.map((value) =>\n    \'<label class="filter-option"><input type="checkbox" data-filter-option value="\' + esc(value) + \'"\' +\n    (choices.has(value) ? " checked" : "") + \'><span>\' + esc(value) + \'</span></label>\').join("") +\n    \'<div class="filter-actions"><button type="button" data-filter-select-all>Select all</button>\' +\n    \'<button type="button" data-filter-clear>Clear</button></div>\';\n}\n\nfunction hydrate(data) {\n  wire();\n  if ((data.config || {}).scopeVersion !== SCOPE_VERSION) {\n    state.rows = [];\n    const status = $("status");\n    status.className = "status err";\n    status.textContent = "This snapshot predates the commissionable opportunity scope. Run `python3 refresh.py --org prod` or use Refresh data locally to load the current Renewal, New Business, and Expansion worklist.";\n    return;\n  }\n  state.rows = data.rows || [];\n  state.summary = data.summary || {};\n  $("cTotal").textContent = fmtCount(state.summary.total ?? state.rows.length);\n  $("cTagged").textContent = fmtCount(state.summary.tagged);\n  $("cNeedsUpdate").textContent = fmtCount(state.summary.needsUpdate);\n  const generated = data.generatedAt ? new Date(data.generatedAt) : null;\n  $("who").textContent = generated ? "Refreshed " + generated.toLocaleString() : "";\n  renderFilter("typeFilter", "type", OPPORTUNITY_TYPES);\n  renderFilter("segmentFilter", "segment", [...new Set(state.rows.map((row) => row.segment).filter(Boolean))].sort());\n  renderFilter("regionFilter", "region", [...new Set(state.rows.map((row) => row.region).filter(Boolean))].sort());\n  $("status").classList.add("hidden");\n  $("dashboard").classList.remove("hidden");\n  render();\n}\n\nasync function detectApi() {\n  try {\n    const response = await fetch("/api/health", { cache: "no-store" });\n    if (!response.ok) return;\n    const health = await response.json();\n    state.writeApi.enabled = !!health.writeEnabled;\n    state.writeApi.refreshEnabled = !!health.refreshEnabled;\n    state.writeApi.org = health.org || "default";\n    state.writeApi.authMode = health.authMode || "local";\n    state.writeApi.loggedIn = state.writeApi.authMode !== "oauth" || !!health.loggedIn;\n    if (health.refreshEnabled) $("refreshBtn").classList.remove("hidden");\n    if (state.writeApi.enabled) $("who").textContent += " · Salesforce org: " + state.writeApi.org;\n    renderUpdate();\n  } catch (_) { /* Static hosting has no local Salesforce API. */ }\n}\n\nfunction filteredRows() {\n  let rows = state.rows.filter((row) => row.nonCommissionable === false &&\n    OPPORTUNITY_TYPES.includes(row.type) && (!row.partnerRepOnTeam || !row.partnerRepOnSplit));\n  ["type", "segment", "region"].forEach((key) => {\n    const selected = state[key];\n    const options = state.filterOptions[key];\n    if (selected.size && selected.size < options.length) rows = rows.filter((row) => selected.has(row[key]));\n  });\n  if (state.query) {\n    const q = state.query;\n    rows = rows.filter((row) => [row.name, row.account, row.id, row.accountOwner, row.partnerRepName]\n      .some((value) => String(value == null ? "" : value).toLowerCase().includes(q)));\n  }\n  const key = state.sortKey;\n  return [...rows].sort((a, b) => {\n    const av = a[key] == null ? "" : a[key];\n    const bv = b[key] == null ? "" : b[key];\n    const compare = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });\n    return compare * state.sortDir;\n  });\n}\n\nfunction opportunityCard(row) {\n  const href = SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + encodeURIComponent(row.id) + "/view";\n  const button = state.writeApi.enabled\n    ? \'<button class="btn-update" data-update="\' + esc(row.id) + \'">Update in Salesforce →</button>\'\n    : \'<a class="btn-update" href="\' + esc(href) + \'" target="_blank" rel="noopener">Open in Salesforce →</a>\';\n  const teamAlready = row.partnerRepOnTeam\n    ? \'<div class="also">Already on the Opportunity Team — no change needed.</div>\' : \'\';\n  const splitAlready = row.partnerRepOnSplit\n    ? \'<div class="also">Overlay split already set — no split change needed.</div>\' : \'\';\n  return \'<article class="action-card needs-update" data-card="\' + esc(row.id) + \'">\' +\n    \'<div class="action-head"><div class="head-main">\' +\n    \'<div class="opp-title"><a href="\' + esc(href) + \'" target="_blank" rel="noopener">\' + esc(row.name || "Unnamed Opportunity") + \'</a></div>\' +\n    \'<div class="details-line">\' + esc(detailsLine(row)) + \'</div>\' +\n    \'<div class="details-line account-name">\' + esc(accountLine(row)) + \'</div>\' +\n    \'<div class="pse-tag"><b>PSE Tagged</b><span class="pse-name">\' + esc(row.partnerRepName || "—") + \'</span><span class="pill">Active</span></div>\' +\n    \'<div class="msg" data-msg="\' + esc(row.id) + \'"></div></div>\' + button + \'</div>\' +\n    \'<div class="action-steps">\' +\n      \'<section class="step"><div class="step-title">1 · Add Opportunity Team Member</div>\' +\n        \'<div class="step-path">Opportunity → Opportunity Team → Add Members</div>\' +\n        \'<table class="step-values"><tbody>\' +\n          \'<tr><td>User</td><td>\' + esc(row.partnerRepName || "—") + \'</td></tr>\' +\n          \'<tr><td>Team Role</td><td>\' + esc(TEAM_ROLE) + \'</td></tr>\' +\n          \'<tr><td>Opportunity Access</td><td>Read Only</td></tr>\' +\n        \'</tbody></table>\' + teamAlready + \'</section>\' +\n      \'<section class="step"><div class="step-title">2 · Add Overlay Split</div>\' +\n        \'<div class="step-path">Opportunity → Opportunity Splits → Edit Splits</div>\' +\n        \'<table class="step-values"><tbody>\' +\n          \'<tr><td>Split Owner</td><td>\' + esc(row.partnerRepName || "—") + \'</td></tr>\' +\n          \'<tr><td>Split Type</td><td>\' + esc(SPLIT_TYPE) + \'</td></tr>\' +\n        \'</tbody></table>\' + splitAlready + \'</section>\' +\n    \'</div></article>\';\n}\n\nfunction wireCardUpdates(containerId) {\n  $(containerId).querySelectorAll("button[data-update]").forEach((button) =>\n    button.addEventListener("click", () => updateOne(button.dataset.update, button)));\n}\n\nfunction renderUpdate() {\n  const rows = filteredRows();\n  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));\n  state.updatePage = Math.min(state.updatePage, pageCount);\n  const start = (state.updatePage - 1) * PAGE_SIZE;\n  $("bulkCount").textContent = rows.length.toLocaleString();\n  $("updateAllBtn").textContent = "Update all " + rows.length.toLocaleString() + " in Salesforce →";\n  $("updateAllBtn").disabled = !state.writeApi.enabled || rows.length === 0;\n  $("actionList").innerHTML = rows.slice(start, start + PAGE_SIZE).map((row) => opportunityCard(row)).join("");\n  wireCardUpdates("actionList");\n  $("actionPageInfo").textContent = rows.length ? `Page ${state.updatePage} of ${pageCount}` : "";\n  $("filterCount").textContent = rows.length.toLocaleString() + " PSEs to update";\n  $("actionPrev").disabled = state.updatePage <= 1;\n  $("actionNext").disabled = state.updatePage >= pageCount;\n  $("actionEmpty").classList.toggle("hidden", rows.length > 0);\n  $("actionList").classList.toggle("hidden", rows.length === 0);\n}\n\nfunction render() {\n  renderUpdate();\n}\n\nasync function postJson(path, body) {\n  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });\n  const result = await response.json();\n  if (!response.ok) throw new Error(result.error || ("HTTP " + response.status));\n  return result;\n}\n\nfunction ensureWriteLogin() {\n  if (state.writeApi.authMode !== "oauth" || state.writeApi.loggedIn) return Promise.resolve(true);\n  return new Promise((resolve) => {\n    const popup = window.open("/oauth/login?next=/", "sfLogin", "width=620,height=720");\n    if (!popup) { window.alert("Please allow the Salesforce login popup, then click again."); resolve(false); return; }\n    let finished = false;\n    const finish = (okay) => {\n      if (finished) return;\n      finished = true;\n      clearInterval(timer);\n      window.removeEventListener("message", onMessage);\n      state.writeApi.loggedIn = !!okay;\n      resolve(!!okay);\n    };\n    const onMessage = (event) => {\n      if (event.origin !== window.location.origin || !event.data || !event.data.sfAuth) return;\n      if (event.data.error) { window.alert("Salesforce login failed: " + event.data.error); finish(false); }\n      else finish(true);\n    };\n    window.addEventListener("message", onMessage);\n    const timer = setInterval(() => { if (popup.closed) finish(state.writeApi.loggedIn); }, 500);\n  });\n}\n\nfunction setRowState(oppId, onTeam, onSplit) {\n  const row = state.rows.find((item) => item.id === oppId);\n  if (!row) return;\n  const wasComplete = row.partnerRepOnTeam && row.partnerRepOnSplit;\n  row.partnerRepOnTeam = !!onTeam;\n  row.partnerRepOnSplit = !!onSplit;\n  const isComplete = row.partnerRepOnTeam && row.partnerRepOnSplit;\n  row.bucket = isComplete ? "complete" : "needs_call";\n  if (!wasComplete && isComplete) {\n    state.summary.tagged = (state.summary.tagged || 0) + 1;\n    state.summary.needsUpdate = Math.max(0, (state.summary.needsUpdate || 0) - 1);\n  } else if (wasComplete && !isComplete) {\n    state.summary.tagged = Math.max(0, (state.summary.tagged || 0) - 1);\n    state.summary.needsUpdate = (state.summary.needsUpdate || 0) + 1;\n  }\n  $("cTagged").textContent = fmtCount(state.summary.tagged);\n  $("cNeedsUpdate").textContent = fmtCount(state.summary.needsUpdate);\n}\n\nasync function updateOne(oppId, button) {\n  button.disabled = true;\n  const message = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\');\n  message.textContent = "Checking Salesforce…";\n  message.className = "msg";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");\n    const preview = await postJson("/api/preview", { oppId });\n    if (preview.alreadyComplete) {\n      setRowState(oppId, true, true); renderUpdate(); return;\n    }\n    if (!preview.eligible) throw new Error(preview.reason || "This PSE is no longer eligible.");\n    const okay = window.confirm(\n      "Update the Opportunity Team and Opportunity Split for:\\n\\n" + preview.oppName +\n      "\\n\\nPSE: " + preview.partnerRepName +\n      "\\nTeam role: " + TEAM_ROLE + " (Opportunity Access: Read Only)" +\n      "\\nSplit: " + SPLIT_PERCENTAGE + "% " + SPLIT_TYPE +\n      "\\nSalesforce org: " + state.writeApi.org + "\\n\\nOnly missing entries will be added. Proceed?"\n    );\n    if (!okay) { message.textContent = "Cancelled — no Salesforce change was made."; button.disabled = false; return; }\n    message.textContent = "Updating Salesforce…";\n    const result = await postJson("/api/write", { oppId, confirm: "CONFIRM" });\n    setRowState(oppId, result.partnerRepOnTeam, result.partnerRepOnSplit);\n    if (!result.done) { renderUpdate(); throw new Error(result.error || "Salesforce did not confirm both the team and split updates."); }\n    renderUpdate();\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;\n    const current = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\');\n    if (current) { current.textContent = error.message; current.className = "msg err"; }\n    button.disabled = false;\n  }\n}\n\nasync function updateAll() {\n  const rows = filteredRows();\n  const ids = rows.map((row) => row.id).filter(Boolean);\n  if (!ids.length) return;\n  const button = $("updateAllBtn");\n  const message = $("bulkMsg");\n  button.disabled = true;\n  message.className = "msg";\n  message.textContent = "Checking current Salesforce status…";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");\n    const preview = await postJson("/api/preview-all", { oppIds: ids });\n    if (!preview.idsToAdd.length) {\n      Object.entries(preview.states || {}).forEach(([id, status]) =>\n        setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));\n      renderUpdate();\n      message.textContent = "No updates needed — the active PSEs are already on the team and Overlay split.";\n      return;\n    }\n    Object.entries(preview.states || {}).forEach(([id, status]) =>\n      setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));\n    renderUpdate();\n    const okay = window.confirm(\n      "Update " + preview.idsToAdd.length.toLocaleString() + " Opportunities in " + state.writeApi.org +\n      "?\\n\\nFor each Opportunity, add its currently tagged active PSE to the Opportunity Team (" + TEAM_ROLE + ", Read Only access) and create a " +\n      SPLIT_PERCENTAGE + "% " + SPLIT_TYPE + " Opportunity Split where missing. Existing entries are kept without duplicates.\\n\\nThis uses the current filters and rechecks each assigned rep before writing."\n    );\n    if (!okay) { message.textContent = "Cancelled — no Salesforce changes were made."; button.disabled = false; return; }\n    message.textContent = "Updating Salesforce… this may take several minutes.";\n    const result = await postJson("/api/write-all", { oppIds: preview.idsToAdd, confirm: "CONFIRM" });\n    Object.entries(result.states || {}).forEach(([id, status]) =>\n      setRowState(id, status.partnerRepOnTeam, status.partnerRepOnSplit));\n    message.className = result.failed ? "msg err" : "msg ok";\n    message.textContent = fmtCount(result.succeeded) + " fully updated · " + fmtCount(result.teamAdded) + " team adds · " +\n      fmtCount(result.splitAdded) + " split adds" + (result.failed ? " · " + fmtCount(result.failed) + " incomplete" : "") +\n      ((result.inactive || result.changed || result.excluded) ? " · " + fmtCount((result.inactive || 0) + (result.changed || 0) + (result.excluded || 0)) + " skipped" : "");\n    render();\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;\n    message.className = "msg err";\n    message.textContent = "Update failed: " + error.message;\n  } finally {\n    button.disabled = false;\n  }\n}\n\nasync function reloadSnapshot() {\n  const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });\n  if (!response.ok) throw new Error("Could not reload the dashboard snapshot.");\n  hydrate(await response.json());\n}\n\nasync function refreshData() {\n  const button = $("refreshBtn");\n  const message = $("refreshMsg");\n  button.disabled = true; button.textContent = "Refreshing…";\n  message.className = "sub"; message.textContent = "Reading Salesforce…";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required to refresh the dashboard.");\n    const result = await postJson("/api/refresh", {});\n    if (result.jobId) {\n      let complete = false;\n      while (!complete) {\n        await new Promise((resolve) => setTimeout(resolve, 1500));\n        const response = await fetch("/api/refresh-status?jobId=" + encodeURIComponent(result.jobId), { cache: "no-store" });\n        const job = await response.json();\n        if (!response.ok) throw new Error(job.error || ("HTTP " + response.status));\n        message.textContent = job.message || "Refreshing Salesforce data…";\n        if (job.status === "failed") throw new Error(job.message || "Salesforce refresh failed.");\n        complete = job.status === "complete";\n      }\n    }\n    await reloadSnapshot();\n    message.className = "sub ok";\n    message.textContent = "Refreshed · " + fmtCount((result.summary || state.summary).total) + " in-scope active PSE Opps";\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce session expired")) state.writeApi.loggedIn = false;\n    message.className = "sub err"; message.textContent = "Refresh failed: " + error.message;\n  } finally { button.disabled = false; button.textContent = "Refresh data"; }\n}\n\nfunction csvCell(value) { return \'"\' + String(value == null ? "" : value).replace(/"/g, \'""\') + \'"\'; }\nfunction exportCsv() {\n  const rows = filteredRows();\n  const columns = ["id", "name", "stage", "closeDate", "type", "account", "accountOwner", "segment", "region", "partnerRepName", "partnerRepId", "partnerRepOnTeam", "partnerRepOnSplit"];\n  const lines = [columns.join(","), ...rows.map((row) => columns.map((key) => csvCell(row[key])).join(","))];\n  const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], { type: "text/csv;charset=utf-8" });\n  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");\n  anchor.href = url; anchor.download = "pse_to_update.csv"; anchor.click(); URL.revokeObjectURL(url);\n}\n\nfunction wire() {\n  if (wired) return;\n  wired = true;\n  $("search").addEventListener("input", (event) => {\n    clearTimeout(searchTimer);\n    searchTimer = setTimeout(() => { state.query = event.target.value.trim().toLowerCase(); state.updatePage = 1; render(); }, 160);\n  });\n  [["typeFilter", "type"], ["segmentFilter", "segment"], ["regionFilter", "region"]].forEach(([id, key]) => {\n    const component = $(id);\n    const button = $(id + "Button");\n    button.addEventListener("click", () => {\n      document.querySelectorAll(".multi-filter").forEach((other) => {\n        if (other !== component) {\n          $(other.id + "Menu").classList.add("hidden");\n          $(other.id + "Button").setAttribute("aria-expanded", "false");\n        }\n      });\n      const menu = $(id + "Menu");\n      menu.classList.toggle("hidden");\n      button.setAttribute("aria-expanded", String(!menu.classList.contains("hidden")));\n    });\n    component.addEventListener("change", (event) => {\n      if (!event.target.matches("input[data-filter-option]")) return;\n      const selected = new Set(state[key]);\n      if (event.target.checked) selected.add(event.target.value);\n      else selected.delete(event.target.value);\n      state[key] = selected;\n      renderFilter(id, key, state.filterOptions[key]);\n      state.updatePage = 1;\n      render();\n    });\n    component.addEventListener("click", (event) => {\n      const action = event.target.closest("button[data-filter-select-all], button[data-filter-clear]");\n      if (!action) return;\n      event.stopPropagation();\n      state[key] = action.hasAttribute("data-filter-select-all")\n        ? new Set(state.filterOptions[key]) : new Set();\n      renderFilter(id, key, state.filterOptions[key]);\n      state.updatePage = 1;\n      render();\n    });\n  });\n  document.addEventListener("click", (event) => {\n    if (event.target.closest(".multi-filter")) return;\n    document.querySelectorAll(".multi-filter").forEach((component) => {\n      $(component.id + "Menu").classList.add("hidden");\n      $(component.id + "Button").setAttribute("aria-expanded", "false");\n    });\n  });\n  document.addEventListener("keydown", (event) => {\n    if (event.key !== "Escape") return;\n    document.querySelectorAll(".multi-filter").forEach((component) => {\n      $(component.id + "Menu").classList.add("hidden");\n      $(component.id + "Button").setAttribute("aria-expanded", "false");\n    });\n  });\n  $("actionPrev").addEventListener("click", () => { state.updatePage = Math.max(1, state.updatePage - 1); renderUpdate(); });\n  $("actionNext").addEventListener("click", () => { state.updatePage += 1; renderUpdate(); });\n  $("updateAllBtn").addEventListener("click", updateAll);\n  $("refreshBtn").addEventListener("click", refreshData);\n  $("csvBtn").addEventListener("click", exportCsv);\n}\n\nload();\n'

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax")

API_VERSION = "v60.0"
TEAM_ROLE = "Partner Account Executive"
TEAM_ACCESS = "Read"
SPLIT_LABEL = "Overlay"
SPLIT_PERCENTAGE = 100
OPPORTUNITY_TYPES = ("Renewal", "New Business", "Expansion")
SCOPE_VERSION = 2
LIGHTNING_URL = os.environ.get("SF_LIGHTNING_URL", "https://zendesk.lightning.force.com").rstrip("/")
SF_LOGIN_URL = os.environ.get("SF_LOGIN_URL", "https://zendesk.my.salesforce.com").rstrip("/")
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")
SF_REDIRECT_URI = os.environ.get("SF_REDIRECT_URI", "").strip()
WRITE_CONFIGURED = bool(SF_CLIENT_ID and SF_CLIENT_SECRET)
OPP_ID_RE = re.compile(r"^006[A-Za-z0-9]{12,15}$")
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


class NeedsLogin(Exception):
    pass


def _database_url() -> str:
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "PG_URL", "DB_URL"):
        value = os.environ.get(key)
        if value:
            return value.replace("postgres://", "postgresql://", 1) if value.startswith("postgres://") else value
    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
    name = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB")
    port = os.environ.get("PGPORT", "5432")
    if host and user and name:
        credential = f"{user}:{password}@" if password else f"{user}@"
        return f"postgresql://{credential}{host}:{port}/{name}"
    raise RuntimeError("AppFoundry Postgres is required. Attach a PostgreSQL database to this app.")


def db():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = create_engine(_database_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)
                with _ENGINE.begin() as cx:
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_snapshot (
                            id SMALLINT PRIMARY KEY CHECK (id = 1),
                            payload TEXT NOT NULL,
                            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_sessions (
                            session_id TEXT PRIMARY KEY,
                            access_token TEXT NOT NULL,
                            refresh_token TEXT,
                            instance_url TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_jobs (
                            job_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            message TEXT NOT NULL,
                            result TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_audit (
                            id BIGSERIAL PRIMARY KEY,
                            event TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
    return _ENGINE


def _session_id() -> str | None:
    return session.get("sf_session_id")


def _token_cipher() -> Fernet:
    secret = app.secret_key.decode() if isinstance(app.secret_key, bytes) else str(app.secret_key)
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _session_record(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    with db().begin() as cx:
        row = cx.execute(text("SELECT access_token, refresh_token, instance_url FROM pse_split_sessions WHERE session_id=:id"), {"id": session_id}).mappings().first()
    if not row:
        return None
    try:
        cipher = _token_cipher()
        saved = dict(row)
        saved["access_token"] = cipher.decrypt(saved["access_token"].encode()).decode()
        if saved.get("refresh_token"):
            saved["refresh_token"] = cipher.decrypt(saved["refresh_token"].encode()).decode()
        return saved
    except InvalidToken:
        return None


def _store_tokens(session_id: str, payload: dict) -> None:
    cipher = _token_cipher()
    with db().begin() as cx:
        cx.execute(text("""
            INSERT INTO pse_split_sessions (session_id, access_token, refresh_token, instance_url)
            VALUES (:id, :access, :refresh, :instance)
            ON CONFLICT (session_id) DO UPDATE SET access_token=EXCLUDED.access_token,
                refresh_token=COALESCE(EXCLUDED.refresh_token, pse_split_sessions.refresh_token),
                instance_url=EXCLUDED.instance_url, updated_at=NOW()
        """), {"id": session_id,
              "access": cipher.encrypt(payload["access_token"].encode()).decode(),
              "refresh": cipher.encrypt(payload["refresh_token"].encode()).decode() if payload.get("refresh_token") else None,
              "instance": payload["instance_url"].rstrip("/")})


def _redirect_uri() -> str:
    if SF_REDIRECT_URI:
        return SF_REDIRECT_URI
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/oauth/callback"


def _token_refresh(session_id: str, saved: dict) -> dict:
    refresh_token = saved.get("refresh_token")
    if not refresh_token:
        raise NeedsLogin()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": SF_CLIENT_ID,
        "client_secret": SF_CLIENT_SECRET, "refresh_token": refresh_token,
    }).encode()
    req = urllib.request.Request(SF_LOGIN_URL + "/services/oauth2/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise NeedsLogin() from error
    result.setdefault("refresh_token", refresh_token)
    _store_tokens(session_id, result)
    return _session_record(session_id)


def sf_request(session_id: str, method: str, path: str, payload: dict | None = None, retry: bool = True):
    saved = _session_record(session_id)
    if not saved:
        raise NeedsLogin()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(saved["instance_url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + saved["access_token"])
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        if error.code == 401 and retry:
            refreshed = _token_refresh(session_id, saved)
            return sf_request(session_id, method, path, payload, retry=False)
        try:
            details = json.loads(raw)
        except json.JSONDecodeError:
            details = {"message": raw[:1000]}
        if isinstance(details, list):
            details = {"message": "; ".join(x.get("message", "") for x in details)}
        return error.code, details


def sf_query(session_id: str, soql: str, label: str = "records") -> list[dict]:
    path = "/services/data/" + API_VERSION + "/query/?q=" + urllib.parse.quote(soql, safe="")
    records = []
    while path:
        status, result = sf_request(session_id, "GET", path)
        if status != 200:
            raise RuntimeError(result.get("message") or f"Salesforce {label} query failed ({status})")
        records.extend(result.get("records", []))
        next_url = result.get("nextRecordsUrl")
        path = next_url if next_url else ""
    return records


def sf_insert(session_id: str, object_name: str, record: dict) -> tuple[int, dict]:
    return sf_request(session_id, "POST", f"/services/data/{API_VERSION}/sobjects/{object_name}/", record)


def _ids_soql(ids: list[str]) -> str:
    return ",".join("'" + value + "'" for value in ids)


def _query_opps(session_id: str, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    soql = ("SELECT Id, Name, Type, Non_Commissionable__c, Partner_Sales_Rep__c, "
            "Partner_Sales_Rep__r.Name, Partner_Sales_Rep__r.IsActive FROM Opportunity "
            f"WHERE Id IN ({_ids_soql(ids)})")
    return {row["Id"]: row for row in sf_query(session_id, soql)}


def _in_scope(opp: dict) -> bool:
    return opp.get("Non_Commissionable__c") is False and opp.get("Type") in OPPORTUNITY_TYPES


def _team_members(session_id: str, ids: list[str]) -> dict[str, set[str]]:
    rows = sf_query(session_id, "SELECT OpportunityId, UserId FROM OpportunityTeamMember WHERE OpportunityId IN (" + _ids_soql(ids) + ")", "team members") if ids else []
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["OpportunityId"], set()).add(row["UserId"])
    return result


def _split_type_id(session_id: str) -> str:
    rows = sf_query(session_id, "SELECT SplitTypeId FROM OpportunitySplit WHERE SplitType.MasterLabel = 'Overlay' LIMIT 1", "Overlay split type")
    if not rows or not rows[0].get("SplitTypeId"):
        raise RuntimeError("This Salesforce org has no Overlay Opportunity Split type.")
    return rows[0]["SplitTypeId"]


def _split_owners(session_id: str, ids: list[str], split_type_id: str) -> dict[str, set[str]]:
    rows = sf_query(session_id, "SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit WHERE OpportunityId IN (" + _ids_soql(ids) + ") AND SplitTypeId = '" + split_type_id + "'", "Overlay splits") if ids else []
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["OpportunityId"], set()).add(row["SplitOwnerId"])
    return result


def _batches(items: list[str], size: int = 500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _summary(rows: list[dict]) -> dict:
    complete = sum(1 for row in rows if row.get("partnerRepOnTeam") and row.get("partnerRepOnSplit"))
    return {"total": len(rows), "tagged": complete, "needsUpdate": len(rows) - complete}


def _refresh_data(session_id: str, progress=None) -> dict:
    split_type_id = _split_type_id(session_id)
    types = ", ".join("'" + value + "'" for value in OPPORTUNITY_TYPES)
    soql = (
        "SELECT Id, Name, StageName, CloseDate, Type, Amount, Non_Commissionable__c, "
        "Account.Name, Account.Owner.Name, Account_Owner_Market_Segment__c, Region__c, "
        "Partner_Sales_Rep__c, Partner_Sales_Rep__r.Name, Partner_Sales_Rep__r.IsActive "
        "FROM Opportunity WHERE Partner_Sales_Rep__c != null AND Non_Commissionable__c = false "
        f"AND Type IN ({types}) AND Partner_Sales_Rep__r.IsActive = true"
    )
    records = sf_query(session_id, soql, "eligible opportunities")
    ids = [record["Id"] for record in records if record.get("Id")]
    if progress:
        progress(f"Checking team and split assignments for {len(ids):,} opportunities…")
    team_by_opp: dict[str, set[str]] = {}
    split_by_opp: dict[str, set[str]] = {}
    chunks = list(_batches(ids))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        for batch in chunks:
            futures[pool.submit(_team_members, session_id, batch)] = "team"
            futures[pool.submit(_split_owners, session_id, batch, split_type_id)] = "split"
        total = len(futures)
        for done, future in enumerate(as_completed(futures), start=1):
            target = team_by_opp if futures[future] == "team" else split_by_opp
            for opp_id, owners in future.result().items():
                target.setdefault(opp_id, set()).update(owners)
            if progress and (done % 20 == 0 or done == total):
                progress(f"Checking Salesforce team/split data… {done:,} of {total:,} batches")

    rows = []
    for record in records:
        rep_id = record.get("Partner_Sales_Rep__c")
        rep = record.get("Partner_Sales_Rep__r") or {}
        on_team = rep_id in team_by_opp.get(record["Id"], set())
        on_split = rep_id in split_by_opp.get(record["Id"], set())
        complete = on_team and on_split
        missing = []
        if not on_team:
            missing.append("Opportunity Team")
        if not on_split:
            missing.append("Overlay split")
        rows.append({
            "id": record.get("Id"), "name": record.get("Name"),
            "account": (record.get("Account") or {}).get("Name"),
            "accountOwner": ((record.get("Account") or {}).get("Owner") or {}).get("Name"),
            "stage": record.get("StageName"), "closeDate": record.get("CloseDate"),
            "type": record.get("Type"), "segment": record.get("Account_Owner_Market_Segment__c"),
            "region": record.get("Region__c"), "amount": record.get("Amount"),
            "nonCommissionable": record.get("Non_Commissionable__c"),
            "partnerRepId": rep_id, "partnerRepName": rep.get("Name"), "partnerRepActive": True,
            "partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split,
            "bucket": "complete" if complete else "needs_call",
            "reason": "PSE is on the Opportunity Team and Overlay split" if complete else "Active partner rep is missing from " + " and ".join(missing),
        })
    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "config": {"scope": "Commissionable Renewal, New Business, and Expansion opportunities",
                   "scopeVersion": SCOPE_VERSION, "commissionableOnly": True,
                   "opportunityTypes": list(OPPORTUNITY_TYPES), "splitType": SPLIT_LABEL,
                   "splitTypeId": split_type_id, "splitPercentage": SPLIT_PERCENTAGE},
        "summary": _summary(rows), "rows": rows,
    }
    _save_snapshot(payload)
    return payload


def _save_snapshot(payload: dict) -> None:
    with db().begin() as cx:
        cx.execute(text("""
            INSERT INTO pse_split_snapshot (id, payload, refreshed_at) VALUES (1, :payload, NOW())
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, refreshed_at=NOW()
        """), {"payload": json.dumps(payload, ensure_ascii=False)})


def _load_snapshot() -> dict:
    with db().begin() as cx:
        row = cx.execute(text("SELECT payload FROM pse_split_snapshot WHERE id=1")).first()
    if not row:
        return {"generatedAt": None,
                "config": {"scopeVersion": SCOPE_VERSION, "scope": "Commissionable Renewal, New Business, and Expansion opportunities"},
                "summary": {"total": 0, "tagged": 0, "needsUpdate": 0}, "rows": []}
    return json.loads(row[0])


def _set_job(job_id: str, status: str, message: str, result: dict | None = None) -> None:
    with db().begin() as cx:
        cx.execute(text("UPDATE pse_split_jobs SET status=:status, message=:message, result=:result, updated_at=NOW() WHERE job_id=:id"),
                   {"id": job_id, "status": status, "message": message,
                    "result": json.dumps(result, ensure_ascii=False) if result is not None else None})


def _run_refresh(job_id: str, session_id: str) -> None:
    try:
        _set_job(job_id, "running", "Reading commissionable Renewal, New Business, and Expansion opportunities…")
        payload = _refresh_data(session_id, lambda message: _set_job(job_id, "running", message))
        _set_job(job_id, "complete", "Dashboard refreshed.", {"summary": payload["summary"], "generatedAt": payload["generatedAt"]})
    except Exception as error:
        message = "Salesforce session expired. Click Refresh data and sign in again." if isinstance(error, NeedsLogin) else str(error)[:1200]
        _set_job(job_id, "failed", message or "Salesforce refresh failed.")


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _require_login():
    session_id = _session_id()
    if not _session_record(session_id):
        return None, (_json_error("Salesforce login is required.", 401)[0], 401)
    return session_id, None


def _eligible_ids() -> set[str]:
    payload = _load_snapshot()
    if (payload.get("config") or {}).get("scopeVersion") != SCOPE_VERSION:
        return set()
    return {row["id"] for row in payload.get("rows", [])
            if row.get("id") and row.get("partnerRepActive") is True
            and row.get("nonCommissionable") is False and row.get("type") in OPPORTUNITY_TYPES
            and (not row.get("partnerRepOnTeam") or not row.get("partnerRepOnSplit"))}


def _validate_ids(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("oppIds must be a list")
    ids = list(dict.fromkeys(str(item) for item in value if item))
    if not ids or len(ids) > 50000 or any(not OPP_ID_RE.fullmatch(value) for value in ids):
        raise ValueError("Invalid Opportunity IDs in request")
    return ids


def _update_snapshot_states(states: dict[str, dict]) -> None:
    if not states:
        return
    payload = _load_snapshot()
    for row in payload.get("rows", []):
        value = states.get(row.get("id"))
        if value:
            row["partnerRepOnTeam"] = bool(value.get("partnerRepOnTeam"))
            row["partnerRepOnSplit"] = bool(value.get("partnerRepOnSplit"))
            complete = row["partnerRepOnTeam"] and row["partnerRepOnSplit"]
            row["bucket"] = "complete" if complete else "needs_call"
            row["reason"] = "PSE is on the Opportunity Team and Overlay split" if complete else "Active partner rep still needs an Opportunity Team or Overlay split update"
    payload["summary"] = _summary(payload.get("rows", []))
    _save_snapshot(payload)


def _team_and_splits(session_id: str, ids: list[str], split_type_id: str):
    return _team_members(session_id, ids), _split_owners(session_id, ids, split_type_id)


def _write_one(session_id: str, opp_id: str) -> dict:
    if opp_id not in _eligible_ids():
        raise ValueError("Opportunity is no longer in the active PSE update worklist. Refresh first.")
    opportunity = _query_opps(session_id, [opp_id]).get(opp_id)
    if not opportunity or not _in_scope(opportunity):
        raise ValueError("Opportunity no longer meets the commissionable Renewal, New Business, or Expansion scope.")
    rep_id = opportunity.get("Partner_Sales_Rep__c")
    rep = opportunity.get("Partner_Sales_Rep__r") or {}
    if not rep_id or rep.get("IsActive") is not True:
        raise ValueError("The assigned Partner Sales Rep is no longer active.")
    split_type_id = _split_type_id(session_id)
    team, splits = _team_and_splits(session_id, [opp_id], split_type_id)
    on_team = rep_id in team.get(opp_id, set())
    on_split = rep_id in splits.get(opp_id, set())
    results = []
    if not on_team:
        status, result = sf_insert(session_id, "OpportunityTeamMember", {
            "OpportunityId": opp_id, "UserId": rep_id, "TeamMemberRole": TEAM_ROLE,
            "OpportunityAccessLevel": TEAM_ACCESS,
        })
        results.append({"object": "OpportunityTeamMember", "success": status in (200, 201), "error": result.get("message")})
    if not on_split:
        status, result = sf_insert(session_id, "OpportunitySplit", {
            "OpportunityId": opp_id, "SplitOwnerId": rep_id, "SplitTypeId": split_type_id,
            "SplitPercentage": SPLIT_PERCENTAGE,
        })
        results.append({"object": "OpportunitySplit", "success": status in (200, 201), "error": result.get("message")})
    team_after, splits_after = _team_and_splits(session_id, [opp_id], split_type_id)
    on_team = rep_id in team_after.get(opp_id, set())
    on_split = rep_id in splits_after.get(opp_id, set())
    _update_snapshot_states({opp_id: {"partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split}})
    return {"done": on_team and on_split, "oppId": opp_id, "partnerRepOnTeam": on_team,
            "partnerRepOnSplit": on_split, "results": results}


def _preview_many(session_id: str, ids: list[str]) -> dict:
    eligible = _eligible_ids()
    requested = [value for value in ids if value in eligible]
    states: dict[str, dict] = {}
    ids_to_add = []
    changed = inactive = already_complete = 0
    split_type_id = _split_type_id(session_id)
    for batch in _batches(requested, 200):
        opportunities = _query_opps(session_id, batch)
        team, splits = _team_and_splits(session_id, batch, split_type_id)
        for opp_id in batch:
            opportunity = opportunities.get(opp_id)
            if not opportunity or not _in_scope(opportunity) or not opportunity.get("Partner_Sales_Rep__c"):
                changed += 1
                continue
            rep = opportunity.get("Partner_Sales_Rep__r") or {}
            if rep.get("IsActive") is not True:
                inactive += 1
                continue
            rep_id = opportunity["Partner_Sales_Rep__c"]
            on_team, on_split = rep_id in team.get(opp_id, set()), rep_id in splits.get(opp_id, set())
            states[opp_id] = {"partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split}
            if on_team and on_split:
                already_complete += 1
            else:
                ids_to_add.append(opp_id)
    _update_snapshot_states(states)
    return {"idsToAdd": ids_to_add, "states": states, "alreadyComplete": already_complete,
            "inactive": inactive, "changed": changed, "excluded": len(ids) - len(requested)}


def _bulk_write(session_id: str, ids: list[str]) -> dict:
    eligible = _eligible_ids()
    requested = [value for value in ids if value in eligible]
    excluded = len(ids) - len(requested)
    split_type_id = _split_type_id(session_id)
    states: dict[str, dict] = {}
    succeeded_ids, failed_ids = set(), set()
    team_added = split_added = already_complete = inactive = changed = 0
    for batch in _batches(requested, 200):
        opportunities = _query_opps(session_id, batch)
        team, splits = _team_and_splits(session_id, batch, split_type_id)
        team_records, team_ids, split_records, split_ids = [], [], [], []
        batch_states = {}
        for opp_id in batch:
            opportunity = opportunities.get(opp_id)
            if not opportunity or not _in_scope(opportunity) or not opportunity.get("Partner_Sales_Rep__c"):
                changed += 1
                continue
            rep = opportunity.get("Partner_Sales_Rep__r") or {}
            if rep.get("IsActive") is not True:
                inactive += 1
                continue
            rep_id = opportunity["Partner_Sales_Rep__c"]
            on_team, on_split = rep_id in team.get(opp_id, set()), rep_id in splits.get(opp_id, set())
            batch_states[opp_id] = {"partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split}
            if on_team and on_split:
                already_complete += 1
                continue
            if not on_team:
                team_records.append({"OpportunityId": opp_id, "UserId": rep_id,
                                     "TeamMemberRole": TEAM_ROLE, "OpportunityAccessLevel": TEAM_ACCESS})
                team_ids.append(opp_id)
            if not on_split:
                split_records.append({"OpportunityId": opp_id, "SplitOwnerId": rep_id,
                                      "SplitTypeId": split_type_id, "SplitPercentage": SPLIT_PERCENTAGE})
                split_ids.append(opp_id)
        for object_name, records, record_ids, state_key in (
            ("OpportunityTeamMember", team_records, team_ids, "partnerRepOnTeam"),
            ("OpportunitySplit", split_records, split_ids, "partnerRepOnSplit"),
        ):
            for start in range(0, len(records), 200):
                chunk = records[start:start + 200]
                chunk_ids = record_ids[start:start + 200]
                status, result = sf_request(session_id, "POST", f"/services/data/{API_VERSION}/composite/sobjects", {
                    "allOrNone": False,
                    "records": [{"attributes": {"type": object_name}, **record} for record in chunk],
                })
                responses = result if isinstance(result, list) else []
                for opp_id, response in zip(chunk_ids, responses):
                    if response.get("success"):
                        batch_states[opp_id][state_key] = True
                        if state_key == "partnerRepOnTeam": team_added += 1
                        else: split_added += 1
                    else:
                        failed_ids.add(opp_id)
                if status not in (200, 201):
                    failed_ids.update(chunk_ids)
        for opp_id, status in batch_states.items():
            states[opp_id] = status
            if status["partnerRepOnTeam"] and status["partnerRepOnSplit"]: succeeded_ids.add(opp_id)
            else: failed_ids.add(opp_id)
        _update_snapshot_states(batch_states)
    return {"succeeded": len(succeeded_ids), "states": states, "teamAdded": team_added,
            "splitAdded": split_added, "alreadyComplete": already_complete,
            "inactive": inactive, "changed": changed, "excluded": excluded,
            "failed": len(failed_ids)}


def _audit(event: dict) -> None:
    with db().begin() as cx:
        cx.execute(text("INSERT INTO pse_split_audit (event) VALUES (:event)"), {"event": json.dumps(event, ensure_ascii=False)})


@app.get("/")
def index():
    if not INDEX_HTML:
        return "AppFoundry bundle is missing the embedded dashboard assets.", 500
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/app.js")
def javascript():
    return Response(APP_JS, mimetype="application/javascript")


@app.get("/pse-data.json")
def data_json():
    try:
        return jsonify(_load_snapshot())
    except Exception as error:
        return _json_error(str(error), 500)


@app.get("/api/health")
def health():
    try:
        sid = _session_id()
        record = _session_record(sid)
        return jsonify({"refreshEnabled": WRITE_CONFIGURED, "writeEnabled": WRITE_CONFIGURED,
                        "authMode": "oauth", "loggedIn": bool(record),
                        "org": (record or {}).get("instance_url", "Salesforce")})
    except Exception as error:
        return _json_error(str(error), 500)


@app.get("/oauth/login")
def oauth_login():
    if not WRITE_CONFIGURED:
        return _json_error("Set SF_CLIENT_ID and SF_CLIENT_SECRET in AppFoundry first.", 503)
    state["oauth_state"] = secrets.token_urlsafe(24)
    session["oauth_next"] = request.args.get("next", "/")
    params = {"response_type": "code", "client_id": SF_CLIENT_ID,
              "redirect_uri": _redirect_uri(), "scope": "api refresh_token",
              "state": state["oauth_state"]}
    return redirect(SF_LOGIN_URL + "/services/oauth2/authorize?" + urllib.parse.urlencode(params))


def _oauth_result(error: str | None = None):
    payload = json.dumps({"sfAuth": True, "error": error})
    message = "Salesforce login complete. You can close this window." if not error else "Salesforce login failed. Close this window and retry."
    return Response("<!doctype html><meta charset=utf-8><title>Salesforce login</title>"
                    "<body style='font:16px system-ui;padding:32px'>" + html.escape(message) +
                    "<script>try{window.opener&&window.opener.postMessage(" + payload + ",location.origin)}catch(e){};window.close()</script></body>",
                    mimetype="text/html")


@app.get("/oauth/callback")
def oauth_callback():
    if request.args.get("error"):
        return _oauth_result(request.args.get("error_description") or request.args["error"])
    if request.args.get("state") != session.get("oauth_state"):
        return _oauth_result("Login state mismatch")
    code = request.args.get("code")
    if not code:
        return _oauth_result("No authorization code returned")
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                   "client_id": SF_CLIENT_ID, "client_secret": SF_CLIENT_SECRET,
                                   "redirect_uri": _redirect_uri()}).encode()
    req = urllib.request.Request(SF_LOGIN_URL + "/services/oauth2/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            token = json.loads(resp.read().decode())
        if not token.get("access_token") or not token.get("instance_url"):
            return _oauth_result("Salesforce did not return an access token")
        sid = _session_id() or secrets.token_urlsafe(32)
        _store_tokens(sid, token)
        session["sf_session_id"] = sid
        session.pop("oauth_state", None)
        return _oauth_result()
    except Exception as error:
        return _oauth_result(str(error)[:300])


@app.post("/api/refresh")
def refresh():
    sid, error = _require_login()
    if error:
        return error
    job_id = secrets.token_urlsafe(18)
    with db().begin() as cx:
        cx.execute(text("INSERT INTO pse_split_jobs (job_id, session_id, status, message) VALUES (:id, :sid, 'queued', 'Queued Salesforce refresh…')"),
                   {"id": job_id, "sid": sid})
    threading.Thread(target=_run_refresh, args=(job_id, sid), daemon=True).start()
    return jsonify({"jobId": job_id}), 202


@app.get("/api/refresh-status")
def refresh_status():
    sid = _session_id()
    job_id = request.args.get("jobId", "")
    with db().begin() as cx:
        row = cx.execute(text("SELECT status, message, result FROM pse_split_jobs WHERE job_id=:id AND session_id=:sid"),
                         {"id": job_id, "sid": sid or ""}).mappings().first()
    if not row:
        return _json_error("Refresh job not found.", 404)
    return jsonify({"status": row["status"], "message": row["message"],
                    "result": json.loads(row["result"]) if row["result"] else None})


@app.post("/api/preview")
def preview_one():
    sid, error = _require_login()
    if error: return error
    opp_id = str((request.get_json(silent=True) or {}).get("oppId") or "")
    if not OPP_ID_RE.fullmatch(opp_id): return _json_error("Invalid Opportunity ID")
    if opp_id not in _eligible_ids(): return _json_error("Opportunity is no longer in the PSE update worklist. Refresh first.", 409)
    try:
        opp = _query_opps(sid, [opp_id]).get(opp_id)
        if not opp or not _in_scope(opp): return _json_error("Opportunity no longer meets the commissionable type scope.", 409)
        rep_id, rep = opp.get("Partner_Sales_Rep__c"), opp.get("Partner_Sales_Rep__r") or {}
        if not rep_id or rep.get("IsActive") is not True: return _json_error("Assigned Partner Sales Rep is no longer active.", 409)
        split_type_id = _split_type_id(sid)
        team, splits = _team_and_splits(sid, [opp_id], split_type_id)
        on_team = rep_id in team.get(opp_id, set())
        on_split = rep_id in splits.get(opp_id, set())
        return jsonify({"eligible": not (on_team and on_split), "alreadyComplete": on_team and on_split,
                        "oppId": opp_id, "oppName": opp.get("Name"), "partnerRepName": rep.get("Name"),
                        "partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split})
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.post("/api/write")
def write_one():
    sid, error = _require_login()
    if error: return error
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "CONFIRM": return _json_error("Confirmation required.")
    try:
        opp_id = str(body.get("oppId") or "")
        if not OPP_ID_RE.fullmatch(opp_id): return _json_error("Invalid Opportunity ID")
        result = _write_one(sid, opp_id)
        _audit({"type": "single_team_and_split_update", "oppId": opp_id, **result})
        return jsonify(result)
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 409)


@app.post("/api/preview-all")
def preview_many():
    sid, error = _require_login()
    if error: return error
    try:
        ids = _validate_ids((request.get_json(silent=True) or {}).get("oppIds"))
        return jsonify(_preview_many(sid, ids))
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.post("/api/write-all")
def write_many():
    sid, error = _require_login()
    if error: return error
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "CONFIRM": return _json_error("Confirmation required.")
    try:
        ids = _validate_ids(body.get("oppIds"))
        result = _bulk_write(sid, ids)
        _audit({"type": "bulk_team_and_split_update", "requested": len(ids), **result})
        return jsonify(result)
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.errorhandler(Exception)
def app_error(error):
    app.logger.exception("AppFoundry request failed")
    return _json_error(str(error), 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
