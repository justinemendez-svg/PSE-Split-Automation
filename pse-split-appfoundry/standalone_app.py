"""PSE Split Automation AppFoundry app.

The AppFoundry deployment keeps the latest Salesforce snapshot in Postgres.
Refresh reads commissionable Renewal, New Business, and Expansion opportunities
with a tagged Partner and resolves the existing PSE or Partner Owner. Salesforce writes remain previewed and
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
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, session
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR.parent / "docs"
INDEX_HTML = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8" />\n<meta name="viewport" content="width=device-width, initial-scale=1" />\n<title>PSE Split Automation</title>\n<style>\n  :root { --bg:#f6f8f9; --panel:#fff; --line:#e2e7eb; --text:#1b2733; --muted:#68737d; --teal:#17494d; --lime:#78a300; --warn:#b26a00; --ok:#3f8c00; --chip:#eef1f3; }\n  * { box-sizing:border-box; }\n  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }\n  header { display:flex; align-items:center; gap:16px; flex-wrap:wrap; padding:16px 24px; background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:10; }\n  h1 { margin:0; font-size:20px; font-weight:700; } .sub { color:var(--muted); font-size:12px; }\n  .spacer { flex:1; }\n  button { border:0; border-radius:8px; padding:9px 15px; background:var(--teal); color:white; font:inherit; font-weight:650; cursor:pointer; }\n  button:hover { background:#23646a; } button:disabled { opacity:.5; cursor:wait; }\n  button.ghost { border:1px solid var(--line); background:white; color:var(--text); } button.ghost:hover { background:#f3f5f7; }\n  main { max-width:1500px; margin:0 auto; padding:24px; }\n  .cards { display:grid; grid-template-columns:repeat(3,minmax(150px,1fr)); gap:14px; margin-bottom:20px; }\n  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:15px 18px; min-height:96px; }\n  .card .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.45px; }\n  .category-card { display:block; text-align:left; color:var(--text); font-weight:400; cursor:pointer; width:100%; font:inherit; }\n  .category-card span { display:block; }\n  .category-card:hover { background:#edf5f3; border-color:#83a8a4; }\n  .category-card.selected { background:#eaf3f1; border:2px solid var(--teal); padding:14px 17px; }\n  .category-card .v { color:var(--teal); }\n  .category-context { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; margin:0 0 15px; font-weight:650; }\n  .card .v { font-size:28px; font-weight:720; margin-top:5px; } .card.ok .v { color:var(--ok); } .card.warn .v { color:var(--warn); }\n  .toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:12px; }\n  input[type=search],select { min-height:40px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:#f3f5f7; color:var(--text); font:inherit; }\n  input[type=search] { flex:1; min-width:250px; } select { min-width:150px; cursor:pointer; }\n  .multi-filter { position:relative; }\n  .filter-trigger { display:flex; align-items:center; justify-content:space-between; gap:16px; min-width:170px; min-height:40px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:#f3f5f7; color:var(--text); font:inherit; font-weight:500; text-align:left; }\n  .filter-trigger::after { content:"▾"; color:var(--muted); font-size:12px; }\n  .filter-menu { position:absolute; z-index:20; top:calc(100% + 5px); left:0; width:240px; max-height:300px; overflow:auto; border:1px solid var(--line); border-radius:9px; background:white; box-shadow:0 8px 24px rgba(27,39,51,.14); }\n  .filter-menu.hidden { display:none!important; }\n  .filter-option { display:flex; align-items:center; gap:9px; padding:8px 12px; cursor:pointer; }\n  .filter-option:hover { background:#f3f5f7; }\n  .filter-option input { accent-color:var(--teal); }\n  .filter-actions { position:sticky; bottom:0; display:flex; justify-content:space-between; padding:7px 10px; border-top:1px solid var(--line); background:#fff; }\n  .filter-actions button { padding:4px 6px; background:transparent; color:var(--teal); font-size:12px; }\n  .filter-actions button:hover { background:#f3f5f7; }\n  h2 { margin:16px 0 12px; font-size:17px; font-weight:680; }\n  .opp-title { font-size:15px; font-weight:680; }\n  .account-name { font-weight:630; } .pse-name { font-weight:650; color:var(--teal); }\n  .pill { display:inline-block; border-radius:999px; padding:2px 9px; background:#e9f3e9; color:var(--ok); font-size:11px; font-weight:700; white-space:nowrap; }\n  .pill.warn { background:#fff0d4; color:var(--warn); }\n  .bulk-bar { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:14px; padding:13px 17px; background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:inset 3px 0 0 var(--warn); }\n  .bulk-info { font-weight:680; }\n  .action-list { display:grid; gap:14px; }\n  .action-card { overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:inset 3px 0 0 var(--warn); }\n  .action-card.is-tagged { box-shadow:inset 3px 0 0 var(--ok); }\n  .action-head { display:flex; align-items:flex-start; gap:16px; padding:15px 18px; }\n  .head-main { flex:1; min-width:0; } .action-head .btn-update { flex-shrink:0; white-space:nowrap; }\n  a.btn-update { display:inline-flex; align-items:center; border-radius:8px; padding:9px 15px; background:var(--teal); color:#fff; font-weight:650; text-decoration:none; }\n  a.btn-update:hover { background:#23646a; }\n  .details-line { margin-top:4px; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }\n  .pse-tag { display:inline-flex; align-items:center; gap:8px; margin-top:9px; padding:4px 10px; border-radius:7px; background:#eaf2f1; color:var(--teal); }\n  .action-steps { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:20px; padding:14px 18px 16px; border-top:1px solid var(--line); }\n  .step { min-width:0; }\n  .step-title { font-size:14px; font-weight:700; }\n  .step-path { margin-top:3px; color:var(--muted); font-size:12px; }\n  .step-values { width:min(100%,420px); margin-top:4px; border-collapse:collapse; overflow:hidden; border-radius:8px; background:#f1f3f5; }\n  .step-values td { padding:7px 14px; border-bottom:1px solid var(--line); }\n  .step-values tr:last-child td { border-bottom:0; }\n  .step-values td:first-child { color:var(--muted); font-weight:650; white-space:nowrap; }\n  .step-values td:last-child { font-weight:650; }\n  .also { margin-top:7px; color:var(--muted); font-size:12px; }\n  .msg { margin-top:5px; font-weight:650; } .msg.ok { color:var(--ok); } .msg.err { color:#b42318; }\n  .pagination { display:flex; justify-content:flex-end; align-items:center; gap:9px; padding:11px 2px; color:var(--muted); font-size:12px; }\n  .pagination button { padding:6px 11px; font-size:12px; }\n  .empty,.status { padding:40px 20px; text-align:center; color:var(--muted); } .status.err { color:#b42318; }\n  .hidden { display:none!important; } #refreshMsg.err { color:#b42318; } #refreshMsg.ok { color:var(--ok); }\n  a { color:var(--teal); text-decoration:none; } a:hover { text-decoration:underline; }\n  @media(max-width:900px) { .cards { grid-template-columns:repeat(2,minmax(145px,1fr)); } .action-head { flex-direction:column; } .action-steps { grid-template-columns:1fr; gap:14px; } }\n  @media(max-width:600px) { main { padding:16px; } header { padding:14px 16px; } .cards { gap:8px; } input[type=search] { min-width:100%; } }\n\n  dialog.process-page { position:fixed; inset:0; width:100vw; max-width:none; height:100dvh; max-height:none; margin:0; padding:0; border:0; background:var(--bg); color:var(--text); }\n  dialog.process-page::backdrop { background:rgba(0,0,0,.3); }\n  .process-bar { position:sticky; top:0; z-index:2; display:flex; align-items:center; gap:20px; padding:18px max(24px,calc((100vw - 1100px)/2)); background:white; border-bottom:1px solid var(--line); }\n  .process-bar .process-brand { margin:0; font-size:22px; font-weight:700; } .process-bar button { margin-left:auto; white-space:nowrap; }\n  .process-content { max-width:1100px; margin:auto; padding:32px 24px 50px; }\n  .process-intro { max-width:760px; } .process-intro h2 { font-size:32px; font-weight:750; line-height:1.2; margin:0 0 8px; }\n  .process-intro p { margin:0; font-size:14px; font-weight:400; color:var(--muted); }\n  .process-scope { margin:20px 0; color:var(--teal); }\n  .flow-step { position:relative; background:white; border:1px solid var(--line); border-radius:14px; padding:22px; }\n  .flow-step h3 { margin:0 0 10px; display:flex; align-items:center; gap:12px; font-size:17px; }\n  .step-number { display:inline-grid; place-items:center; width:32px; height:32px; border-radius:50%; background:var(--teal); color:white; flex-shrink:0; }\n  .flow-arrow { text-align:center; color:var(--teal); font-size:24px; height:38px; }\n  .branches { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }\n  .branch { background:#f4f7f7; border-radius:9px; padding:15px; } .branch strong { display:block; color:var(--teal); margin-bottom:5px; }\n  .branch p { margin:0; } .process-note { margin:12px 0 0; padding:12px 15px; background:#fff4dd; border-radius:8px; }\n  .flow-step dl { display:grid; grid-template-columns:max-content 1fr; gap:6px 16px; margin:12px 0 0; } .flow-step dt { color:var(--muted); } .flow-step dd { margin:0; font-weight:600; }\n  .process-help { margin-top:28px; background:white; padding:22px; border-radius:14px; border:1px solid var(--line); }\n  .process-help h3 { margin:0 0 12px; } .process-help p { margin:10px 0; }\n  .process-help h4 { margin:22px 0 8px; font-size:15px; color:var(--teal); }\n  .process-help ol { padding-left:24px; } .process-help li { margin:10px 0; padding-left:4px; }\n  .help-filters { display:grid; grid-template-columns:minmax(130px,180px) 1fr; gap:12px 18px; margin:16px 0; }\n  .help-filters dt { font-weight:650; } .help-filters dd { margin:0; }\n  @media(max-width:600px) { .help-filters { grid-template-columns:1fr; gap:4px; } .help-filters dd { margin-bottom:12px; } }\n  button:focus-visible, a:focus-visible { outline:3px solid #6a9fa3; outline-offset:3px; }\n  @media(max-width:650px) { .branches { grid-template-columns:1fr; } .process-intro h2 { font-size:25px; } .process-bar { padding:14px 16px; } .flow-step { padding:16px; } }\n</style>\n</head>\n<body>\n<header>\n  <div><h1>PSE Split Automation</h1></div>\n  <div class="spacer"></div>\n  <span id="refreshMsg" class="sub" aria-live="polite"></span><span id="who" class="sub"></span>\n  <button class="ghost" id="processFlowBtn">Process Flow</button>\n  <button class="ghost hidden" id="refreshBtn">Refresh</button><button id="csvBtn">Export CSV</button>\n</header>\n<main>\n  <div id="status" class="status">Loading dashboard data…</div>\n  <section id="dashboard" class="hidden">\n    <div class="cards" role="group" aria-label="Filter Opportunities by PSE assignment">\n      <button type="button" class="card category-card" data-pse-category="tagged" aria-pressed="false" aria-controls="panelUpdate"><span class="k">Opportunities with tagged PSE</span><span class="v" id="cWithPse">—</span><span class="sub">Partner + tagged PSE · missing Team or Split</span></button>\n      <button type="button" class="card category-card" data-pse-category="untagged" aria-pressed="false" aria-controls="panelUpdate"><span class="k">Partner but no tagged PSE</span><span class="v" id="cWithoutPse">—</span><span class="sub">PSE blank · Partner Owner missing from Team or Split</span></button>\n      <button type="button" class="card category-card selected" data-pse-category="all" aria-pressed="true" aria-controls="panelUpdate"><span class="k">All Opportunities to update</span><span class="v" id="cNeedsUpdate">—</span><span class="sub">Both categories · missing Team or Split</span></button>\n    </div>\n    <div class="category-context"><span id="categoryLabel" aria-live="polite">Showing all Opportunities to update</span><span class="sub">Card counts follow your search and filters. Select a box to filter the list.</span></div>\n    <div class="toolbar">\n      <input type="search" id="search" placeholder="Search Opportunity name, Account name, or Opp ID…" />\n      <div class="multi-filter" id="quarterFilter" data-filter-title="Fiscal Quarter–Year" data-filter-key="fiscalQuarter" data-filter-label="fiscal quarters" title="Based on Close Date. Zendesk fiscal year: February–January.">\n        <button type="button" class="filter-trigger" id="quarterFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="quarterFilterMenu">Fiscal Quarter–Year</button>\n        <div class="filter-menu hidden" id="quarterFilterMenu" role="group" aria-label="Fiscal Quarter-Year options"></div>\n      </div>\n      <div class="multi-filter" id="typeFilter" data-filter-title="Opportunity Type" data-filter-key="type" data-filter-label="types">\n        <button type="button" class="filter-trigger" id="typeFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="typeFilterMenu">Opportunity Type</button>\n        <div class="filter-menu hidden" id="typeFilterMenu" role="group" aria-label="Opportunity Type options"></div>\n      </div>\n      <div class="multi-filter" id="segmentFilter" data-filter-title="Market Segment" data-filter-key="segment" data-filter-label="segments">\n        <button type="button" class="filter-trigger" id="segmentFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="segmentFilterMenu">Market Segment</button>\n        <div class="filter-menu hidden" id="segmentFilterMenu" role="group" aria-label="Market Segment options"></div>\n      </div>\n      <div class="multi-filter" id="regionFilter" data-filter-title="Region" data-filter-key="region" data-filter-label="regions">\n        <button type="button" class="filter-trigger" id="regionFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="regionFilterMenu">Region</button>\n        <div class="filter-menu hidden" id="regionFilterMenu" role="group" aria-label="Region options"></div>\n      </div>\n      <div class="multi-filter" id="pseStatusFilter" data-filter-title="PSE Status" data-filter-key="pseStatus" data-filter-label="PSE statuses">\n        <button type="button" class="filter-trigger" id="pseStatusFilterButton" aria-haspopup="true" aria-expanded="false" aria-controls="pseStatusFilterMenu">PSE Status</button>\n        <div class="filter-menu hidden" id="pseStatusFilterMenu" role="group" aria-label="PSE Status options"></div>\n      </div>\n      <span class="spacer"></span><span id="filterCount" class="sub"></span>\n    </div>\n    <section id="panelUpdate">\n      <h2>PSE to update</h2>\n      <div id="bulkBar" class="bulk-bar">\n        <div class="bulk-info"><span id="bulkCount">0</span> Opps need a PSE field, Team, or Split update</div>\n        <button id="updateAllBtn">Update all 0 in Salesforce →</button><span id="bulkMsg" class="msg"></span>\n      </div>\n      <div id="actionList" class="action-list"></div>\n      <div id="actionEmpty" class="empty hidden">No Opportunities need a PSE field, Team, or Split update for these filters.</div>\n      <div class="pagination"><span id="actionPageInfo"></span><button class="ghost" id="actionPrev">Previous</button><button class="ghost" id="actionNext">Next</button></div>\n    </section>\n    <footer class="sub">Commissionable Renewal, New Business, and Expansion Opportunities at Stage 08 - Closed with a tagged Partner and a PSE missing from the Team, Splits, or both. When the PSE field is blank, membership checks use the Partner Owner. Existing PSE values and existing Team and Split entries are preserved. Refresh reads Salesforce; Update actions apply the process after confirmation.</footer>\n  </section>\n</main>\n\n<dialog id="processFlow" class="process-page" aria-labelledby="processTitle">\n  <div class="process-bar"><div class="process-brand">PSE Split Automation</div><button id="closeProcessFlow" class="ghost" autofocus>Close · Back to dashboard ×</button></div>\n  <div class="process-content">\n    <div class="process-intro"><h2 id="processTitle">Process Flow</h2><p>How the dashboard works</p></div>\n    <p class="process-scope"><b>Filter:</b> Closed Opportunity · With Partner Tagged · Stage: 08 - Closed</p>\n    <section class="flow-step"><h3><span class="step-number">1</span> Does the closed Opportunity have a Partner tagged?</h3>\n      <p>Start with Opportunities whose Stage is exactly <b>08 - Closed</b>. Check whether the closed Opportunity has a tagged <b>Partner</b>.</p>\n      <div class="branches"><div class="branch"><strong>Partner is tagged → Continue</strong><p>Use the tagged Partner for this process.</p></div><div class="branch"><strong>No Partner → Stop</strong><p>Skip this Opportunity. It does not appear in the worklist.</p></div></div>\n      <p class="also">Salesforce field: the lookup labeled “Partner” is <code>Referring_Partner__c</code>.</p>\n    </section><div class="flow-arrow" aria-hidden="true">↓</div>\n    <section class="flow-step"><h3><span class="step-number">2</span> Choose the Partner Sales Rep</h3>\n      <div class="branches"><div class="branch"><strong>PSE already populated → Use existing rep</strong><p>Keep <code>Partner_Sales_Rep__c</code> exactly as it is, even if the Partner has a different Owner.</p></div><div class="branch"><strong>PSE blank → Use the Partner’s Owner</strong><p>When you click Update, populate <code>Partner_Sales_Rep__c</code> with the Owner of the tagged Partner.</p></div></div>\n      <p class="process-note"><b>Key rule:</b> Populate the PSE field only when blank. Both paths continue to the Team check, then the Splits check.</p>\n    </section><div class="flow-arrow" aria-hidden="true">↓</div>\n    <section class="flow-step"><h3><span class="step-number">3</span> Check the Opportunity Team</h3>\n      <div class="branches"><div class="branch"><strong>Rep already on the Team → Keep entry</strong><p>Make no Team change. Continue to Opportunity Splits.</p></div><div class="branch"><strong>Rep missing → Add Team member</strong><dl><dt>User</dt><dd>Chosen PSE</dd><dt>Team Role</dt><dd>Partner Account Executive</dd><dt>Opportunity Access</dt><dd>Read Only</dd></dl></div></div>\n    </section><div class="flow-arrow" aria-hidden="true">↓</div>\n    <section class="flow-step"><h3><span class="step-number">4</span> Check Opportunity Splits</h3>\n      <div class="branches"><div class="branch"><strong>Rep already on a Split → Keep entry</strong><p>Make no Split change. Any existing Split for that rep counts as present; its type, role, and percentage stay as they are.</p></div><div class="branch"><strong>Rep missing → Add Overlay Split</strong><dl><dt>Split Owner</dt><dd>Chosen PSE</dd><dt>Split Type</dt><dd>Overlay</dd><dt>Team Role</dt><dd>Partner Account Executive</dd><dt>Percent</dt><dd>100.00%</dd></dl></div></div>\n    </section>\n    <section class="process-help"><h3>Using the dashboard</h3>\n      <p>The list shows closed Opportunities with a Partner tagged where the PSE still needs to be added to the Opportunity Team, Opportunity Splits, or both. When the PSE field is blank, these checks use the tagged Partner’s Owner.</p>\n      <h4>Refresh</h4>\n      <p>Click <b>Refresh</b> to pull the latest data from Salesforce. The dashboard updates its counts and list based on the current Opportunity, Partner, PSE, Team, and Split records in SFDC. Data refreshes only when this button is clicked. Opening or reloading the page shows the last saved data.</p>\n      <h4>Clickable boxes</h4>\n      <p>Click a box to filter the list by category:</p>\n      <ol>\n        <li><b>Opportunities with tagged PSE:</b> Closed Opportunities with a Partner and an existing PSE, where that PSE is missing from the Opportunity Team, Opportunity Splits, or both.</li>\n        <li><b>Partner but no tagged PSE:</b> Closed Opportunities with a Partner but a blank PSE field, where the Partner’s Owner is missing from the Opportunity Team, Opportunity Splits, or both. The Owner is the PSE that will be used when you click Update.</li>\n        <li><b>All Opportunities to update:</b> Combines the first two categories: closed Opportunities with a Partner, with or without a tagged PSE, that still need a Team or Split update.</li>\n      </ol>\n      <p><b>What “missing” means:</b> If the rep is already on the Team but has no Split, the Opportunity stays in the list. The same applies if the rep has a Split but is missing from the Team. If the rep is already on both, the Opportunity is excluded from all three boxes.</p>\n      <h4>Search and filters</h4>\n      <p>Search by <b>Opportunity name, Opportunity ID, or Account name</b>. Use the filters below to narrow the selected category. You can select multiple values in each filter; selections across different filters work together. Clear a filter’s selections to remove that restriction.</p>\n      <dl class="help-filters">\n        <dt>Fiscal Quarter–Year</dt><dd>Filters by Opportunity Close Date using Zendesk’s fiscal calendar: Q1 Feb–Apr, Q2 May–Jul, Q3 Aug–Oct, and Q4 Nov–Jan. For example, November 2026 through January 2027 is FY2027 Q4.</dd>\n        <dt>Opportunity Type</dt><dd>Choose Renewal, New Business, Expansion, or a combination.</dd>\n        <dt>Market Segment</dt><dd>Filters by the Opportunity’s Account Owner Market Segment.</dd>\n        <dt>Region</dt><dd>Filters by the Region recorded on the Opportunity.</dd>\n        <dt>PSE Status</dt><dd>Choose Active, Inactive, or both. If a PSE is tagged, this is that user’s status. If the PSE field is blank, this is the Partner Owner’s status.</dd>\n      </dl>\n      <p>The box counts reflect your search and filters. <b>All Opportunities to update = tagged PSE category + no tagged PSE category</b> for the same selections.</p>\n      <h4>Update and export</h4>\n      <p><b>Update in Salesforce</b> applies the process to one Opportunity after confirmation. <b>Update all N in Salesforce</b> applies it to all matching results across all pages, using your selected box, search, and filters. <b>Export CSV</b> downloads that same filtered list.</p>\n      <p>Refresh only reads Salesforce. Update actions preserve an existing PSE, populate a blank PSE from the tagged Partner’s Owner, and add missing Team and Split entries. Each Opportunity uses its own PSE. Existing entries are preserved; Salesforce errors are reported so you can retry the remaining steps.</p>\n      <p><b>Scope:</b> Commissionable Renewal, New Business, and Expansion Opportunities at Stage exactly <b>08 - Closed</b>, with a Partner tagged.</p>\n    </section>\n  </div>\n</dialog>\n<script src="app.js"></script>\n</body>\n</html>\n'
APP_JS = '/* Partner → PSE field → Opportunity Team → Opportunity Splits. */\nconst SF_LIGHTNING_URL = "https://zendesk.lightning.force.com";\nconst TEAM_ROLE = "Partner Account Executive";\nconst SPLIT_TYPE = "Overlay";\nconst SPLIT_PERCENTAGE = 100;\nconst PAGE_SIZE = 100;\nconst OPPORTUNITY_TYPES = ["Renewal", "New Business", "Expansion"];\nconst SCOPE_VERSION = 4;\nconst CLOSED_STAGE = "08 - Closed";\nconst state = {\n  rows: [], rowsById: new Map(), summary: {}, type: new Set(), segment: new Set(), region: new Set(), pseStatus: new Set(), fiscalQuarter: new Set(),\n  filterOptions: { type: OPPORTUNITY_TYPES, segment: [], region: [], pseStatus: [], fiscalQuarter: [] }, query: "",\n  sortKey: "closeDate", sortDir: -1, updatePage: 1, pseCategory: "all", busy: false,\n  writeApi: { enabled: false, refreshEnabled: false, org: null, authMode: "local", loggedIn: true },\n};\nconst $ = (id) => document.getElementById(id);\nlet wired = false;\nlet searchTimer = null;\n\nfunction esc(value) {\n  return String(value == null ? "" : value).replace(/[&<>"\']/g, (ch) =>\n    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[ch]));\n}\nfunction fmtDate(value) {\n  if (!value) return "—";\n  const date = new Date(value + "T00:00:00");\n  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" });\n}\nfunction fiscalQuarterFromDate(value) {\n  const match = /^(\\d{4})-(\\d{2})-(\\d{2})$/.exec(String(value || ""));\n  if (!match) return "Close date unavailable";\n  const [, yearText, monthText, dayText] = match;\n  const year = Number(yearText), month = Number(monthText), day = Number(dayText);\n  const date = new Date(Date.UTC(year, month - 1, day));\n  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "Close date unavailable";\n  const fiscalYear = month >= 2 ? year + 1 : year;\n  const quarter = Math.floor(((month + 10) % 12) / 3) + 1;\n  return "FY" + fiscalYear + " Q" + quarter;\n}\n\nfunction fmtCount(value) { return Number(value || 0).toLocaleString("en-US"); }\nfunction detailsLine(row) {\n  return [row.id, row.stage, fmtDate(row.closeDate), row.type].filter(Boolean).join(" · ");\n}\nfunction accountLine(row) {\n  return [row.account, row.accountOwner, row.segment, row.region].filter(Boolean).join(" · ") || "—";\n}\n\nasync function load() {\n  wire();\n  try {\n    const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });\n    if (!response.ok) {\n      const details = await response.json().catch(() => ({}));\n      await detectApi();\n      const status = $("status");\n      if (details.error) {\n        status.className = "status err";\n        status.textContent = "Dashboard data could not be loaded: " + details.error + " (HTTP " + response.status + "). Check the server configuration, then reload.";\n        return;\n      }\n      status.className = state.writeApi.refreshEnabled ? "status" : "status err";\n      status.textContent = state.writeApi.refreshEnabled\n        ? "No Salesforce snapshot yet. Click Refresh to load data."\n        : "Dashboard data is unavailable (HTTP " + response.status + "). Check the server configuration, then reload.";\n      return;\n    }\n    const data = await response.json();\n    hydrate(data);\n    await detectApi();\n  } catch (error) {\n    await detectApi();\n    const status = $("status");\n    status.className = state.writeApi.refreshEnabled ? "status" : "status err";\n    status.textContent = state.writeApi.refreshEnabled\n      ? "Dashboard snapshot could not be loaded. Click Refresh to fetch it from Salesforce. (" + error.message + ")"\n      : window.location.protocol === "file:"\n        ? "Open the dashboard through its local server. Double-click serve.command, then open http://localhost:8773 in Chrome."\n        : "Dashboard data could not be loaded. Check the server connection, then reload. (" + error.message + ")";\n  }\n}\n\nfunction renderFilter(id, key, values) {\n  const component = $(id);\n  const selected = state[key];\n  const available = new Set(values);\n  state[key] = new Set([...selected].filter((value) => available.has(value)));\n  state.filterOptions[key] = values;\n  const choices = state[key];\n  const label = component.dataset.filterLabel;\n  const button = $(id + "Button");\n  button.textContent = !choices.size || choices.size === values.length\n    ? component.dataset.filterTitle\n    : choices.size === 1 ? [...choices][0] : choices.size + " " + label + " selected";\n  const menu = $(id + "Menu");\n  menu.innerHTML = values.map((value) =>\n    \'<label class="filter-option"><input type="checkbox" data-filter-option value="\' + esc(value) + \'"\' +\n    (choices.has(value) ? " checked" : "") + \'><span>\' + esc(value) + \'</span></label>\').join("") +\n    \'<div class="filter-actions"><button type="button" data-filter-select-all>Select all</button>\' +\n    \'<button type="button" data-filter-clear>Clear</button></div>\';\n}\n\nfunction hydrate(data) {\n  wire();\n  if ((data.config || {}).scopeVersion !== SCOPE_VERSION) {\n    state.rows = [];\n    state.rowsById.clear();\n    $("dashboard").classList.add("hidden");\n    const status = $("status");\n    status.className = "status";\n    status.textContent = "The closed Opportunity flow (08 - Closed) needs a fresh Salesforce snapshot. Click Refresh to load the current worklist.";\n    return false;\n  }\n  state.rows = (data.rows || []).map((row) => ({ ...row, fiscalQuarter: fiscalQuarterFromDate(row.closeDate) }));\n  state.rowsById = new Map(state.rows.map((row) => [row.id, row]));\n  state.summary = data.summary || {};\n  const generated = data.generatedAt ? new Date(data.generatedAt) : null;\n  $("who").textContent = generated ? "Refreshed " + generated.toLocaleString() : "";\n  renderFilter("pseStatusFilter", "pseStatus", ["Active", "Inactive", ...(state.rows.some((row) => row.partnerRepActive !== true && row.partnerRepActive !== false) ? ["Status unavailable"] : [])]);\n  renderFilter("quarterFilter", "fiscalQuarter", [...new Set(state.rows.map((row) => row.fiscalQuarter))].sort((a, b) => b.localeCompare(a)));\n  renderFilter("typeFilter", "type", OPPORTUNITY_TYPES);\n  renderFilter("segmentFilter", "segment", [...new Set(state.rows.map((row) => row.segment).filter(Boolean))].sort());\n  renderFilter("regionFilter", "region", [...new Set(state.rows.map((row) => row.region).filter(Boolean))].sort());\n  $("status").classList.add("hidden");\n  $("dashboard").classList.remove("hidden");\n  render();\n  return true;\n}\n\nasync function detectApi() {\n  try {\n    const response = await fetch("/api/health", { cache: "no-store" });\n    if (!response.ok) return;\n    const health = await response.json();\n    state.writeApi.enabled = !!health.writeEnabled;\n    state.writeApi.refreshEnabled = !!health.refreshEnabled;\n    state.writeApi.org = health.org || "default";\n    state.writeApi.authMode = health.authMode || "local";\n    state.writeApi.loggedIn = state.writeApi.authMode !== "oauth" || !!health.loggedIn;\n    if (health.refreshEnabled) $("refreshBtn").classList.remove("hidden");\n    if (state.writeApi.enabled) $("who").textContent += " · Salesforce org: " + state.writeApi.org;\n    renderUpdate();\n  } catch (_) { /* Static hosting has no local Salesforce API. */ }\n}\n\nfunction pseStatus(row) { return row.partnerRepActive === true ? "Active" : row.partnerRepActive === false ? "Inactive" : "Status unavailable"; }\n\nfunction rowComplete(row) { return !row.partnerRepNeedsPopulate && row.partnerRepOnTeam && row.partnerRepOnSplit; }\n\nfunction matchesCategory(row, category) {\n  const missingMembership = !row.partnerRepOnTeam || !row.partnerRepOnSplit;\n  if (category === "tagged") return !row.partnerRepNeedsPopulate && missingMembership;\n  if (category === "untagged") return !!row.partnerRepNeedsPopulate && missingMembership;\n  return missingMembership;\n}\n\nfunction filteredRows(category = state.pseCategory) {\n  let rows = state.rows.filter((row) => row.inScope !== false && row.nonCommissionable === false && row.stage === CLOSED_STAGE &&\n    OPPORTUNITY_TYPES.includes(row.type) && row.partnerId && !rowComplete(row));\n  ["type", "segment", "region", "pseStatus", "fiscalQuarter"].forEach((key) => {\n    const selected = state[key];\n    const options = state.filterOptions[key];\n    if (selected.size && selected.size < options.length) rows = rows.filter((row) => selected.has(key === "pseStatus" ? pseStatus(row) : row[key]));\n  });\n  if (state.query) {\n    const q = state.query;\n    rows = rows.filter((row) => [row.name, row.account, row.id, row.accountOwner, row.partnerRepName]\n      .some((value) => String(value == null ? "" : value).toLowerCase().includes(q)));\n  }\n  rows = rows.filter((row) => matchesCategory(row, category));\n  const key = state.sortKey;\n  return [...rows].sort((a, b) => {\n    const av = a[key] == null ? "" : a[key];\n    const bv = b[key] == null ? "" : b[key];\n    const compare = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });\n    return compare * state.sortDir;\n  });\n}\n\nfunction opportunityCard(row) {\n  const href = SF_LIGHTNING_URL + "/lightning/r/Opportunity/" + encodeURIComponent(row.id) + "/view";\n  const button = state.writeApi.enabled\n    ? \'<button class="btn-update" data-update="\' + esc(row.id) + \'"\' + ((state.busy || row.blockedReason) ? \' disabled\' : \'\') + \'>Update in Salesforce →</button>\'\n    : \'<a class="btn-update" href="\' + esc(href) + \'" target="_blank" rel="noopener">Open in Salesforce →</a>\';\n  const teamAlready = row.partnerRepOnTeam\n    ? \'<div class="also">Already on the Opportunity Team — no change needed.</div>\' : \'\';\n  const splitAlready = row.partnerRepOnSplit\n    ? \'<div class="also">Rep already on Opportunity Splits — existing entry preserved.</div>\' : \'\';\n  const userStatus = row.partnerRepActive === true ? \'Active\' : row.partnerRepActive === false ? \'Inactive\' : \'Status unavailable\';\n  return \'<article class="action-card needs-update" data-card="\' + esc(row.id) + \'">\' +\n    \'<div class="action-head"><div class="head-main">\' +\n    \'<div class="opp-title"><a href="\' + esc(href) + \'" target="_blank" rel="noopener">\' + esc(row.name || "Unnamed Opportunity") + \'</a></div>\' +\n    \'<div class="details-line">\' + esc(detailsLine(row)) + \'</div>\' +\n    \'<div class="details-line account-name">\' + esc(accountLine(row)) + \'</div>\' +\n    \'<div class="details-line">Partner: \' + esc(row.partnerName || "—") + \' · Partner Owner: \' + esc(row.partnerOwnerName || "—") + \'</div>\' +\n    \'<div class="pse-tag"><b>\' + (row.partnerRepNeedsPopulate ? \'PSE to populate\' : \'PSE Tagged\') + \'</b><span class="pse-name">\' + esc(row.partnerRepName || "—") + \'</span><span class="pill\' + (row.partnerRepActive !== true ? \' warn\' : \'\') + \'">\' + userStatus + \'</span></div>\' +\n    (row.blockedReason ? \'<div class="msg err">\' + esc(row.blockedReason) + \'</div>\' : \'\') +\n    \'<div class="msg" data-msg="\' + esc(row.id) + \'"></div></div>\' + button + \'</div>\' +\n    \'<div class="action-steps">\' +\n      \'<section class="step"><div class="step-title">1 · Partner Sales Rep field</div>\' +\n        \'<div class="step-path">\' + (row.partnerRepNeedsPopulate ? \'Populate from the tagged Partner’s Owner\' : \'Preserve the existing PSE\') + \'</div>\' +\n        \'<table class="step-values"><tbody><tr><td>PSE</td><td>\' + esc(row.partnerRepName) + \'</td></tr><tr><td>Action</td><td>\' +\n        (row.partnerRepNeedsPopulate ? \'Populate blank field\' : \'No field change\') + \'</td></tr></tbody></table></section>\' +\n      \'<section class="step"><div class="step-title">2 · Check Opportunity Team</div>\' +\n        \'<div class="step-path">Opportunity → Opportunity Team → Add Members</div>\' +\n        \'<table class="step-values"><tbody>\' +\n          \'<tr><td>User</td><td>\' + esc(row.partnerRepName || "—") + \'</td></tr>\' +\n          \'<tr><td>Team Role</td><td>\' + esc(TEAM_ROLE) + \'</td></tr>\' +\n          \'<tr><td>Opportunity Access</td><td>Read Only</td></tr>\' +\n        \'</tbody></table>\' + teamAlready + \'</section>\' +\n      \'<section class="step"><div class="step-title">3 · Check Opportunity Splits</div>\' +\n        \'<div class="step-path">Opportunity → Opportunity Splits → Edit Splits</div>\' +\n        \'<table class="step-values"><tbody>\' +\n          \'<tr><td>Split Owner</td><td>\' + esc(row.partnerRepName || "—") + \'</td></tr>\' +\n          \'<tr><td>Split Type</td><td>\' + esc(SPLIT_TYPE) + \'</td></tr>\' +\n          \'<tr><td>Team Role</td><td>\' + esc(TEAM_ROLE) + \'</td></tr>\' +\n          \'<tr><td>Percent</td><td>100.00%</td></tr>\' +\n        \'</tbody></table>\' + splitAlready + \'</section>\' +\n    \'</div></article>\';\n}\n\nfunction wireCardUpdates(containerId) {\n  $(containerId).querySelectorAll("button[data-update]").forEach((button) =>\n    button.addEventListener("click", () => updateOne(button.dataset.update, button)));\n}\n\nfunction renderUpdate() {\n  const allRows = filteredRows("all");\n  const rows = allRows.filter((row) => matchesCategory(row, state.pseCategory));\n  $("cWithPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "tagged")).length);\n  $("cWithoutPse").textContent = fmtCount(allRows.filter((row) => matchesCategory(row, "untagged")).length);\n  $("cNeedsUpdate").textContent = fmtCount(allRows.length);\n  document.querySelectorAll("[data-pse-category]").forEach((button) => {\n    const selected = button.dataset.pseCategory === state.pseCategory;\n    button.classList.toggle("selected", selected);\n    button.setAttribute("aria-pressed", String(selected));\n  });\n  $("categoryLabel").textContent = {\n    all: "Showing all Opportunities to update",\n    tagged: "Showing Partner Opportunities with a tagged PSE · missing Team or Split",\n    untagged: "Showing Partner Opportunities without a tagged PSE · missing Team or Split",\n  }[state.pseCategory];\n  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));\n  state.updatePage = Math.min(state.updatePage, pageCount);\n  const start = (state.updatePage - 1) * PAGE_SIZE;\n  $("bulkCount").textContent = rows.length.toLocaleString();\n  $("updateAllBtn").textContent = "Update all " + rows.length.toLocaleString() + " in Salesforce →";\n  $("updateAllBtn").disabled = state.busy || !state.writeApi.enabled || rows.length === 0;\n  $("actionList").innerHTML = rows.slice(start, start + PAGE_SIZE).map((row) => opportunityCard(row)).join("");\n  wireCardUpdates("actionList");\n  $("actionPageInfo").textContent = rows.length ? `Page ${state.updatePage} of ${pageCount}` : "";\n  $("filterCount").textContent = fmtCount(rows.length) + " Opportunities to update";\n  $("actionPrev").disabled = state.busy || state.updatePage <= 1;\n  $("actionNext").disabled = state.busy || state.updatePage >= pageCount;\n  $("actionEmpty").classList.toggle("hidden", rows.length > 0);\n  $("actionList").classList.toggle("hidden", rows.length === 0);\n}\n\nfunction render() {\n  renderUpdate();\n}\n\nasync function postJson(path, body) {\n  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });\n  let result;\n  try { result = await response.json(); }\n  catch (_) { throw new Error("The server response could not be read. Check Salesforce status before retrying an update."); }\n  if (response.status === 401 || result.needsLogin) state.writeApi.loggedIn = false;\n  if (!response.ok) throw new Error(result.error || ("HTTP " + response.status));\n  return result;\n}\n\nfunction ensureWriteLogin() {\n  if (state.writeApi.authMode !== "oauth" || state.writeApi.loggedIn) return Promise.resolve(true);\n  return new Promise((resolve) => {\n    const popup = window.open("/oauth/login?next=/", "sfLogin", "width=620,height=720");\n    if (!popup) { window.alert("Please allow the Salesforce login popup, then click again."); resolve(false); return; }\n    let finished = false;\n    const finish = (okay) => {\n      if (finished) return;\n      finished = true;\n      clearInterval(timer);\n      window.removeEventListener("message", onMessage);\n      state.writeApi.loggedIn = !!okay;\n      resolve(!!okay);\n    };\n    const onMessage = (event) => {\n      if (event.origin !== window.location.origin || !event.data || !event.data.sfAuth) return;\n      if (event.data.error) { window.alert("Salesforce login failed: " + event.data.error); finish(false); }\n      else finish(true);\n    };\n    window.addEventListener("message", onMessage);\n    const timer = setInterval(() => { if (popup.closed) finish(state.writeApi.loggedIn); }, 500);\n  });\n}\n\nfunction setRowState(oppId, value) {\n  const row = state.rowsById.get(oppId);\n  if (!row) return;\n  const wasComplete = !!rowComplete(row);\n  Object.assign(row, value);\n  const change = Number(!!rowComplete(row)) - Number(wasComplete);\n  state.summary.tagged = (state.summary.tagged || 0) + change;\n  state.summary.needsUpdate = (state.summary.needsUpdate || 0) - change;\n\n}\n\nfunction setBusy(value) {\n  state.busy = value;\n  document.querySelectorAll(\'#search, .filter-trigger, .filter-menu input, .filter-actions button, [data-pse-category], #actionPrev, #actionNext, #csvBtn, #refreshBtn, #updateAllBtn, button[data-update]\').forEach((control) => { control.disabled = value; });\n  if (!value) renderUpdate();\n}\n\nasync function updateOne(oppId, button) {\n  if (state.busy) return;\n  setBusy(true);\n  button.disabled = true;\n  let message = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\');\n  message.textContent = "Checking Salesforce…";\n  message.className = "msg";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");\n    const preview = await postJson("/api/preview", { oppId });\n    setRowState(oppId, preview);\n    if (preview.alreadyComplete) {\n      setRowState(oppId, preview); renderUpdate(); return;\n    }\n    if (!preview.eligible) throw new Error(preview.reason || "This PSE is no longer eligible.");\n    const okay = window.confirm(\n      "Apply the Partner → PSE → Team → Splits process for:\\n\\n" + preview.oppName +\n      "\\n\\nPSE: " + preview.partnerRepName +\n      "\\nPSE field: " + (preview.partnerRepNeedsPopulate ? "Populate from Partner Owner" : "Keep existing value") +\n      "\\nTeam role: " + TEAM_ROLE + " (Opportunity Access: Read Only)" +\n      "\\nSplit: " + SPLIT_PERCENTAGE + "% " + SPLIT_TYPE +\n      "\\nSalesforce org: " + state.writeApi.org + "\\n\\nOnly blank PSE fields and missing Team/Split entries are added. Existing entries are preserved. Proceed?"\n    );\n    if (!okay) { message.textContent = "Cancelled — no Salesforce change was made."; button.disabled = false; return; }\n    message.textContent = "Updating Salesforce…";\n    const result = await postJson("/api/write", { oppId, confirm: "CONFIRM" });\n    setRowState(oppId, result);\n    if (!result.done) { renderUpdate(); throw new Error(result.error || "Salesforce did not confirm both the team and split updates."); }\n    renderUpdate();\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;\n    const current = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\');\n    if (current) { current.textContent = error.message; current.className = "msg err"; }\n    button.disabled = false;\n  } finally {\n    const text = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\')?.textContent;\n    const error = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\')?.classList.contains(\'err\');\n    setBusy(false);\n    const current = document.querySelector(\'[data-msg="\' + CSS.escape(oppId) + \'"]\');\n    if (current && text) { current.textContent = text; current.className = error ? \'msg err\' : \'msg\'; }\n  }\n}\n\nasync function updateAll() {\n  if (state.busy) return;\n  const rows = filteredRows();\n  const ids = rows.map((row) => row.id).filter(Boolean);\n  if (!ids.length) return;\n  setBusy(true);\n  const button = $("updateAllBtn");\n  const message = $("bulkMsg");\n  button.disabled = true;\n  message.className = "msg";\n  message.textContent = "Checking current Salesforce status…";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required before updating.");\n    const preview = await postJson("/api/preview-all", { oppIds: ids });\n    if (!preview.idsToAdd.length) {\n      Object.entries(preview.states || {}).forEach(([id, status]) =>\n        setRowState(id, status));\n      renderUpdate();\n      message.textContent = "No eligible updates remain. The records are complete, changed scope, or have an unavailable Partner Owner. Refresh for current status.";\n      return;\n    }\n    Object.entries(preview.states || {}).forEach(([id, status]) =>\n      setRowState(id, status));\n    renderUpdate();\n    const okay = window.confirm(\n      "Update " + preview.idsToAdd.length.toLocaleString() + " Opportunities in " + state.writeApi.org +\n      "?\\n\\nKeep the existing PSE. If blank, populate it from the tagged Partner’s Owner. Then add the PSE to the Opportunity Team (" + TEAM_ROLE + ", Read Only) if missing, followed by a 100% Overlay Split (" + TEAM_ROLE + ") if the rep has no existing Split.\\n\\nExisting field values and Team/Split entries are preserved. This applies to all results across all pages using your current filters and rechecks Salesforce before each stage."\n    );\n    if (!okay) { message.textContent = "Cancelled — no Salesforce changes were made."; button.disabled = false; return; }\n    message.textContent = "Updating Salesforce… this may take several minutes.";\n    const result = await postJson("/api/write-all", { oppIds: preview.idsToAdd, confirm: "CONFIRM" });\n    Object.entries(result.states || {}).forEach(([id, status]) =>\n      setRowState(id, status));\n    message.className = result.failed ? "msg err" : "msg ok";\n    message.textContent = fmtCount(result.succeeded) + " fully updated · " + fmtCount(result.fieldPopulated) + " PSE fields populated · " + fmtCount(result.teamAdded) + " team adds · " +\n      fmtCount(result.splitAdded) + " split adds" + (result.failed ? " · " + fmtCount(result.failed) + " incomplete" : "") +\n      ((result.inactive || result.changed || result.excluded) ? " · " + fmtCount((result.inactive || 0) + (result.changed || 0) + (result.excluded || 0)) + " skipped" : "");\n    if ((result.errors || []).length) message.textContent += " · " + result.errors.map((item) => (item.oppId ? item.oppId + ": " : "") + item.message).join("; ");\n    render();\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce login")) state.writeApi.loggedIn = false;\n    message.className = "msg err";\n    message.textContent = "Update failed: " + error.message;\n  } finally {\n    setBusy(false);\n  }\n}\n\nasync function reloadSnapshot() {\n  const response = await fetch("pse-data.json?_=" + Date.now(), { cache: "no-store" });\n  if (!response.ok) throw new Error("Could not reload the dashboard snapshot.");\n  if (!hydrate(await response.json())) throw new Error("The saved data does not match the current dashboard scope. Click Refresh again.");\n}\n\nasync function refreshData() {\n  if (state.busy) return;\n  setBusy(true);\n  const button = $("refreshBtn");\n  const message = $("refreshMsg");\n  button.disabled = true; button.textContent = "Refreshing…";\n  message.className = "sub";\n  message.textContent = "Refreshing Salesforce… This can take a few minutes.";\n  try {\n    if (!(await ensureWriteLogin())) throw new Error("Salesforce login is required to refresh the dashboard.");\n    const result = await postJson("/api/refresh", { manual: true });\n    if (result.jobId) {\n      let complete = false;\n      while (!complete) {\n        await new Promise((resolve) => setTimeout(resolve, 1500));\n        const response = await fetch("/api/refresh-status?jobId=" + encodeURIComponent(result.jobId), { cache: "no-store" });\n        const job = await response.json();\n        if (!response.ok) throw new Error(job.error || ("HTTP " + response.status));\n        message.textContent = job.message || "Refreshing Salesforce data…";\n        if (job.status === "failed") throw new Error(job.message || "Salesforce refresh failed.");\n        complete = job.status === "complete";\n      }\n    }\n    await reloadSnapshot();\n    message.className = "sub ok";\n    message.textContent = "Refreshed · " + fmtCount((result.summary || state.summary).total) + " in-scope Partner Opportunities";\n  } catch (error) {\n    if (error.message.toLowerCase().includes("salesforce session expired")) state.writeApi.loggedIn = false;\n    message.className = "sub err"; message.textContent = "Refresh failed: " + error.message;\n  } finally { button.textContent = "Refresh"; setBusy(false); }\n}\n\nfunction csvCell(value) { return \'"\' + String(value == null ? "" : value).replace(/"/g, \'""\') + \'"\'; }\nfunction exportCsv() {\n  const rows = filteredRows();\n  const columns = ["id", "name", "stage", "closeDate", "fiscalQuarter", "type", "account", "accountOwner", "segment", "region", "partnerName", "partnerOwnerName", "partnerRepName", "partnerRepId", "partnerRepNeedsPopulate", "partnerRepActive", "partnerRepOnTeam", "partnerRepOnSplit"];\n  const lines = [columns.join(","), ...rows.map((row) => columns.map((key) => csvCell(row[key])).join(","))];\n  const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], { type: "text/csv;charset=utf-8" });\n  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");\n  anchor.href = url; anchor.download = "pse_to_update.csv"; anchor.click(); URL.revokeObjectURL(url);\n}\n\nfunction wire() {\n  if (wired) return;\n  wired = true;\n  document.querySelectorAll("[data-pse-category]").forEach((button) => {\n    button.addEventListener("click", () => {\n      state.pseCategory = button.dataset.pseCategory;\n      state.updatePage = 1;\n      $("bulkMsg").textContent = "";\n      renderUpdate();\n    });\n  });\n  const process = $("processFlow");\n  let priorOverflow = "";\n  $("processFlowBtn").addEventListener("click", () => {\n    priorOverflow = document.body.style.overflow;\n    document.body.style.overflow = "hidden";\n    process.showModal();\n    process.scrollTop = 0;\n  });\n  $("closeProcessFlow").addEventListener("click", () => process.close());\n  process.addEventListener("close", () => {\n    document.body.style.overflow = priorOverflow;\n    $("processFlowBtn").focus({ preventScroll: true });\n  });\n  $("search").addEventListener("input", (event) => {\n    clearTimeout(searchTimer);\n    state.query = event.target.value.trim().toLowerCase();\n    state.updatePage = 1;\n    searchTimer = setTimeout(render, 160);\n  });\n  [["quarterFilter", "fiscalQuarter"], ["typeFilter", "type"], ["segmentFilter", "segment"], ["regionFilter", "region"], ["pseStatusFilter", "pseStatus"]].forEach(([id, key]) => {\n    const component = $(id);\n    const button = $(id + "Button");\n    button.addEventListener("click", () => {\n      document.querySelectorAll(".multi-filter").forEach((other) => {\n        if (other !== component) {\n          $(other.id + "Menu").classList.add("hidden");\n          $(other.id + "Button").setAttribute("aria-expanded", "false");\n        }\n      });\n      const menu = $(id + "Menu");\n      menu.classList.toggle("hidden");\n      button.setAttribute("aria-expanded", String(!menu.classList.contains("hidden")));\n    });\n    component.addEventListener("change", (event) => {\n      if (!event.target.matches("input[data-filter-option]")) return;\n      const selected = new Set(state[key]);\n      if (event.target.checked) selected.add(event.target.value);\n      else selected.delete(event.target.value);\n      state[key] = selected;\n      renderFilter(id, key, state.filterOptions[key]);\n      state.updatePage = 1;\n      render();\n    });\n    component.addEventListener("click", (event) => {\n      const action = event.target.closest("button[data-filter-select-all], button[data-filter-clear]");\n      if (!action) return;\n      event.stopPropagation();\n      state[key] = action.hasAttribute("data-filter-select-all")\n        ? new Set(state.filterOptions[key]) : new Set();\n      renderFilter(id, key, state.filterOptions[key]);\n      state.updatePage = 1;\n      render();\n    });\n  });\n  document.addEventListener("click", (event) => {\n    if (event.target.closest(".multi-filter")) return;\n    document.querySelectorAll(".multi-filter").forEach((component) => {\n      $(component.id + "Menu").classList.add("hidden");\n      $(component.id + "Button").setAttribute("aria-expanded", "false");\n    });\n  });\n  document.addEventListener("keydown", (event) => {\n    if (event.key !== "Escape") return;\n    document.querySelectorAll(".multi-filter").forEach((component) => {\n      $(component.id + "Menu").classList.add("hidden");\n      $(component.id + "Button").setAttribute("aria-expanded", "false");\n    });\n  });\n  $("actionPrev").addEventListener("click", () => { state.updatePage = Math.max(1, state.updatePage - 1); renderUpdate(); });\n  $("actionNext").addEventListener("click", () => { state.updatePage += 1; renderUpdate(); });\n  $("updateAllBtn").addEventListener("click", updateAll);\n  $("refreshBtn").addEventListener("click", () => refreshData());\n  $("csvBtn").addEventListener("click", exportCsv);\n}\n\nload();\n'

# Shared workflow source is embedded by build_standalone.py for single-file deployment.
import sys
sys.path.insert(0, str(APP_DIR.parent))
import types
_workflow = types.ModuleType('workflow')
exec('"""Shared Partner → PSE → Team → Splits rules for local and hosted dashboards."""\nfrom __future__ import annotations\n\nimport datetime as dt\nfrom email.utils import format_datetime\n\nPARTNER_FIELD = \'Referring_Partner__c\'\nPARTNER_REL = \'Referring_Partner__r\'\nTEAM_ROLE = \'Partner Account Executive\'\nOPPORTUNITY_TYPES = (\'Renewal\', \'New Business\', \'Expansion\')\nSCOPE_VERSION = 4\nCLOSED_STAGE = \'08 - Closed\'\nAPI_VERSION = \'v60.0\'\nREP_FIELDS = (f\'{PARTNER_FIELD}, {PARTNER_REL}.Name, {PARTNER_REL}.OwnerId, \'\n              f\'{PARTNER_REL}.Owner.Name, {PARTNER_REL}.Owner.IsActive, \'\n              \'Partner_Sales_Rep__c, Partner_Sales_Rep__r.Name, Partner_Sales_Rep__r.IsActive, LastModifiedDate\')\n\n\ndef in_scope(record):\n    return (bool(record.get(PARTNER_FIELD)) and record.get(\'Non_Commissionable__c\') is False\n            and record.get(\'Type\') in OPPORTUNITY_TYPES and record.get(\'StageName\') == CLOSED_STAGE)\n\n\ndef rep_state(record, team=(), splits=()):\n    partner = record.get(PARTNER_REL) or {}\n    populate = not bool(record.get(\'Partner_Sales_Rep__c\'))\n    rep_id = partner.get(\'OwnerId\') if populate else record.get(\'Partner_Sales_Rep__c\')\n    rep = (partner.get(\'Owner\') or {}) if populate else (record.get(\'Partner_Sales_Rep__r\') or {})\n    valid_user = bool(rep_id and rep_id.startswith(\'005\'))\n    on_team, on_split = bool(rep_id and rep_id in team), bool(rep_id and rep_id in splits)\n    complete = not populate and on_team and on_split\n    missing = []\n    if populate: missing.append(\'Partner Sales Rep field\')\n    if not on_team: missing.append(\'Opportunity Team\')\n    if not on_split: missing.append(\'Opportunity Splits\')\n    return {\'stage\': record.get(\'StageName\'), \'partnerId\': record.get(PARTNER_FIELD), \'partnerName\': partner.get(\'Name\'),\n            \'partnerOwnerName\': (partner.get(\'Owner\') or {}).get(\'Name\'),\n            \'partnerRepId\': rep_id, \'partnerRepName\': rep.get(\'Name\') or \'(user unavailable)\',\n            \'partnerRepActive\': rep.get(\'IsActive\'), \'partnerRepNeedsPopulate\': populate,\n            \'partnerRepSource\': \'partner_owner\' if populate else \'existing\',\n            \'partnerRepOnTeam\': on_team, \'partnerRepOnSplit\': on_split,\n            \'blockedReason\': \'\' if valid_user else \'The Partner Owner could not be resolved to a Salesforce user.\',\n            \'bucket\': \'complete\' if complete else \'needs_call\',\n            \'reason\': \'PSE field, Team and Splits are complete\' if complete else \'Missing: \' + \', \'.join(missing)}\n\n\ndef is_complete(row):\n    return not row.get(\'partnerRepNeedsPopulate\') and row.get(\'partnerRepOnTeam\') and row.get(\'partnerRepOnSplit\')\n\n\ndef summary(rows):\n    complete = sum(bool(is_complete(row)) for row in rows)\n    return {\'total\': len(rows), \'tagged\': complete, \'needsUpdate\': len(rows) - complete,\n            \'needsPopulate\': sum(bool(row.get(\'partnerRepNeedsPopulate\')) for row in rows)}\n\n\ndef read_states(ids, query):\n    quoted = \',\'.join("\'" + value + "\'" for value in ids)\n    records = query(\'SELECT Id, Name, StageName, Type, Non_Commissionable__c, \' + REP_FIELDS +\n                    f\' FROM Opportunity WHERE Id IN ({quoted})\')\n    team, splits = {}, {}\n    for row in query(f\'SELECT OpportunityId, UserId FROM OpportunityTeamMember WHERE OpportunityId IN ({quoted})\'):\n        team.setdefault(row[\'OpportunityId\'], set()).add(row[\'UserId\'])\n    for row in query(f\'SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit WHERE OpportunityId IN ({quoted})\'):\n        splits.setdefault(row[\'OpportunityId\'], set()).add(row[\'SplitOwnerId\'])\n    by_id = {row[\'Id\']: row for row in records}\n    states = {key: rep_state(row, team.get(key, ()), splits.get(key, ()))\n              for key, row in by_id.items() if in_scope(row)}\n    return by_id, states\n\n\ndef execute(ids, allowed, query, rest, split_type, write=False):\n    """Preview or apply ordered rules; isolate failures and verify actual Salesforce state."""\n    unique_ids = list(dict.fromkeys(ids))\n    requested = [value for value in unique_ids if value in allowed]\n    states, errors, ids_to_add = {}, [], []\n    counts = {\'fieldPopulated\': 0, \'teamAdded\': 0, \'splitAdded\': 0, \'alreadyComplete\': 0,\n              \'excluded\': len(unique_ids) - len(requested), \'inactive\': 0}\n    failed, succeeded, changed = set(), set(), set()\n    resolved_split_type = None\n\n    def recheck(batch):\n        records, current = read_states(batch, query)\n        for key in batch:\n            if key not in current:\n                changed.add(key)\n                states[key] = {\'inScope\': False, \'reason\': \'Opportunity no longer meets the closed Partner scope or is no longer accessible.\'}\n        for key, value in current.items():\n            value[\'inScope\'] = True\n            changed.discard(key)\n        return records, current\n\n    def record_error(key, object_name, detail):\n        failed.add(key)\n        errors.append({\'oppId\': key, \'object\': object_name, \'message\': str(detail)[:500]})\n\n    def insert_many(object_name, records, counter):\n        if not records:\n            return\n        try:\n            status, result = rest(\'POST\', f\'/services/data/{API_VERSION}/composite/sobjects\',\n                {\'allOrNone\': False, \'records\': [{\'attributes\': {\'type\': object_name}, **row} for _, row in records]})\n            if status not in (200, 201) or not isinstance(result, list) or len(result) != len(records):\n                raise RuntimeError(f\'Salesforce {object_name} response could not be confirmed: {str(result)[:350]}\')\n        except (RuntimeError, OSError, ValueError) as error:\n            for key, _ in records:\n                record_error(key, object_name, error)\n            return\n        for (key, _), response in zip(records, result):\n            if isinstance(response, dict) and response.get(\'success\'):\n                counts[counter] += 1\n            else:\n                record_error(key, object_name, response.get(\'errors\', response) if isinstance(response, dict) else response)\n\n    def reconcile(batch, current):\n        states.update(current)\n        for key in batch:\n            if key in changed:\n                failed.discard(key)\n                succeeded.discard(key)\n            elif key in current and is_complete(current[key]):\n                succeeded.add(key)\n                failed.discard(key)\n            else:\n                failed.add(key)\n\n    for start in range(0, len(requested), 200):\n        batch = requested[start:start + 200]\n        try:\n            records, current = recheck(batch)\n            for key, value in current.items():\n                if is_complete(value):\n                    counts[\'alreadyComplete\'] += 1\n                elif value[\'blockedReason\']:\n                    record_error(key, \'User\', value[\'blockedReason\'])\n                else:\n                    ids_to_add.append(key)\n            if not write:\n                states.update(current)\n                continue\n            # Populate only blank fields. A concurrent Opportunity edit rejects this write.\n            for key, value in current.items():\n                if key in failed or not value[\'partnerRepNeedsPopulate\']:\n                    continue\n                try:\n                    modified = records[key].get(\'LastModifiedDate\')\n                    if not modified:\n                        raise ValueError(\'Missing record version; refresh before populating the PSE.\')\n                    timestamp = dt.datetime.strptime(modified, \'%Y-%m-%dT%H:%M:%S.%f%z\').astimezone(dt.timezone.utc)\n                    status, response = rest(\'PATCH\', f\'/services/data/{API_VERSION}/sobjects/Opportunity/{key}\',\n                        {\'Partner_Sales_Rep__c\': value[\'partnerRepId\']},\n                        headers={\'If-Unmodified-Since\': format_datetime(timestamp, usegmt=True)})\n                    if status not in (200, 204):\n                        raise RuntimeError(str(response))\n                    counts[\'fieldPopulated\'] += 1\n                except (RuntimeError, OSError, ValueError) as error:\n                    record_error(key, \'Opportunity\', error)\n            # Re-read after every stage to observe assignment changes and Salesforce automation.\n            _, current = recheck(batch)\n            members = [(key, {\'OpportunityId\': key, \'UserId\': value[\'partnerRepId\'],\n                        \'TeamMemberRole\': TEAM_ROLE, \'OpportunityAccessLevel\': \'Read\'})\n                       for key, value in current.items() if key not in failed and not value[\'blockedReason\']\n                       and not value[\'partnerRepNeedsPopulate\'] and not value[\'partnerRepOnTeam\']]\n            insert_many(\'OpportunityTeamMember\', members, \'teamAdded\')\n            _, current = recheck(batch)\n            pending_splits = [(key, value) for key, value in current.items()\n                              if key not in failed and not value[\'blockedReason\']\n                              and not value[\'partnerRepNeedsPopulate\'] and value[\'partnerRepOnTeam\']\n                              and not value[\'partnerRepOnSplit\']]\n            if pending_splits:\n                if resolved_split_type is None:\n                    resolved_split_type = split_type()\n                splits = [(key, {\'OpportunityId\': key, \'SplitOwnerId\': value[\'partnerRepId\'],\n                           \'SplitTypeId\': resolved_split_type, \'Opportunity_Team_Role__c\': TEAM_ROLE, \'SplitPercentage\': 100})\n                          for key, value in pending_splits]\n                insert_many(\'OpportunitySplit\', splits, \'splitAdded\')\n            _, current = recheck(batch)\n            reconcile(batch, current)\n        except (RuntimeError, OSError, ValueError) as error:\n            if not write:\n                raise\n            errors.append({\'message\': str(error)[:500]})\n            failed.update(batch)\n            # A timeout can occur after Salesforce accepts a write. Reconcile before reporting failure.\n            try:\n                _, latest = recheck(batch)\n                reconcile(batch, latest)\n            except (RuntimeError, OSError, ValueError):\n                pass\n    return {**counts, \'changed\': len(changed), \'states\': states, \'idsToAdd\': ids_to_add,\n            \'succeeded\': len(succeeded), \'succeededIds\': sorted(succeeded),\n            \'failed\': len(failed), \'errors\': errors[:50]}\n', _workflow.__dict__)
sys.modules['workflow'] = _workflow
from workflow import CLOSED_STAGE, REP_FIELDS, PARTNER_FIELD, SCOPE_VERSION, execute, in_scope, is_complete, rep_state, summary
WRITE_LOCK = threading.Lock()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax")

API_VERSION = "v60.0"
TEAM_ROLE = "Partner Account Executive"
TEAM_ACCESS = "Read"
SPLIT_LABEL = "Overlay"
SPLIT_PERCENTAGE = 100
OPPORTUNITY_TYPES = ("Renewal", "New Business", "Expansion")

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
    host = os.environ.get("DB_HOST") or os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    user = os.environ.get("DB_USER") or os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("DB_PASSWORD") or os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
    name = os.environ.get("DB_NAME") or os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB")
    port = os.environ.get("DB_PORT") or os.environ.get("PGPORT", "5432")
    if host and user and name:
        return URL.create("postgresql", username=user, password=password or None,
                          host=host, port=int(port), database=name).render_as_string(hide_password=False)
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


def sf_request(session_id: str, method: str, path: str, payload: dict | None = None, retry: bool = True, headers: dict | None = None):
    saved = _session_record(session_id)
    if not saved:
        raise NeedsLogin()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(saved["instance_url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + saved["access_token"])
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items(): req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        if error.code == 401 and retry:
            refreshed = _token_refresh(session_id, saved)
            return sf_request(session_id, method, path, payload, retry=False, headers=headers)
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


def _ids_soql(ids: list[str]) -> str:
    return ",".join("'" + value + "'" for value in ids)


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
    rows = sf_query(session_id, "SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit WHERE OpportunityId IN (" + _ids_soql(ids) + ")", "splits") if ids else []
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["OpportunityId"], set()).add(row["SplitOwnerId"])
    return result


def _batches(items: list[str], size: int = 500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _summary(rows: list[dict]) -> dict:
    return summary(rows)

def _refresh_data(session_id: str, progress=None) -> dict:
    split_type_id = _split_type_id(session_id)
    types = ", ".join("'" + value + "'" for value in OPPORTUNITY_TYPES)
    soql = (
        "SELECT Id, Name, StageName, CloseDate, Type, Amount, Non_Commissionable__c, "
        "Account.Name, Account.Owner.Name, Account_Owner_Market_Segment__c, Region__c, "
        + REP_FIELDS + f" FROM Opportunity WHERE {PARTNER_FIELD} != null AND Non_Commissionable__c = false "
        f"AND Type IN ({types}) AND StageName = '{CLOSED_STAGE}'"
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
        value = rep_state(record, team_by_opp.get(record["Id"], ()), split_by_opp.get(record["Id"], ()))
        rows.append({
            "id": record.get("Id"), "name": record.get("Name"),
            "account": (record.get("Account") or {}).get("Name"),
            "accountOwner": ((record.get("Account") or {}).get("Owner") or {}).get("Name"),
            "stage": record.get("StageName"), "closeDate": record.get("CloseDate"),
            "type": record.get("Type"), "segment": record.get("Account_Owner_Market_Segment__c"),
            "region": record.get("Region__c"), "amount": record.get("Amount"),
            "nonCommissionable": record.get("Non_Commissionable__c"),
            **value,
        })
    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "config": {"scope": "Commissionable 08 - Closed Renewal, New Business, and Expansion opportunities",
                   "scopeVersion": SCOPE_VERSION, "stage": CLOSED_STAGE, "partnerField": PARTNER_FIELD, "splitCheck": "any", "commissionableOnly": True,
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
                "config": {"scopeVersion": SCOPE_VERSION, "stage": CLOSED_STAGE, "partnerField": PARTNER_FIELD, "splitCheck": "any", "scope": "Commissionable 08 - Closed Renewal, New Business, and Expansion opportunities"},
                "summary": {"total": 0, "tagged": 0, "needsUpdate": 0}, "rows": []}
    return json.loads(row[0])


def _set_job(job_id: str, status: str, message: str, result: dict | None = None) -> None:
    with db().begin() as cx:
        cx.execute(text("UPDATE pse_split_jobs SET status=:status, message=:message, result=:result, updated_at=NOW() WHERE job_id=:id"),
                   {"id": job_id, "status": status, "message": message,
                    "result": json.dumps(result, ensure_ascii=False) if result is not None else None})


@contextmanager
def snapshot_operation():
    # PostgreSQL serializes snapshot operations across Gunicorn workers and sessions.
    with db().begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(7418773)"))
        yield


def _run_refresh(job_id: str, session_id: str) -> None:
    try:
        _set_job(job_id, "running", "Reading commissionable Renewal, New Business, and Expansion opportunities…")
        with snapshot_operation():
            payload = _refresh_data(session_id, lambda message: _set_job(job_id, "running", message))
        _set_job(job_id, "complete", "Dashboard refreshed.", {"summary": payload["summary"], "generatedAt": payload["generatedAt"]})
    except Exception as error:
        message = "Salesforce session expired. Click Refresh and sign in again." if isinstance(error, NeedsLogin) else str(error)[:1200]
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
            if row.get("id") and row.get("partnerId") and row.get("stage") == CLOSED_STAGE and row.get("nonCommissionable") is False
            and row.get("type") in OPPORTUNITY_TYPES and not is_complete(row)}


def _validate_ids(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("oppIds must be a list")
    ids = list(dict.fromkeys(str(item) for item in value if item))
    if not ids or len(ids) > 200000 or any(not OPP_ID_RE.fullmatch(value) for value in ids):
        raise ValueError("Invalid Opportunity IDs in request")
    return ids


def _update_snapshot_states(states: dict[str, dict]) -> None:
    if not states:
        return
    payload = _load_snapshot()
    for row in payload.get("rows", []):
        value = states.get(row.get("id"))
        if value: row.update(value)
    payload["summary"] = _summary(payload.get("rows", []))
    _save_snapshot(payload)


def _run_workflow(session_id, ids, write=False):
    with WRITE_LOCK, snapshot_operation():
        result = execute(ids, _eligible_ids(), lambda soql: sf_query(session_id, soql),
                         lambda method, path, body, **kw: sf_request(session_id, method, path, body, **kw),
                         lambda: _split_type_id(session_id), write=write)
        _update_snapshot_states(result["states"])
        return result


def _write_one(session_id, opp_id):
    result = _run_workflow(session_id, [opp_id], write=True)
    value = result["states"].get(opp_id, {})
    return {**value, "done": bool(value and is_complete(value)), "oppId": opp_id,
            "error": "; ".join(item.get("message", "") for item in result["errors"]), "results": result}


def _preview_many(session_id, ids):
    return _run_workflow(session_id, ids)


def _bulk_write(session_id, ids):
    return _run_workflow(session_id, ids, write=True)


def _audit(event: dict) -> None:
    with db().begin() as cx:
        cx.execute(text("INSERT INTO pse_split_audit (event) VALUES (:event)"), {"event": json.dumps(event, ensure_ascii=False)})


@app.before_request
def validate_json_body():
    if request.method == "POST" and not isinstance(request.get_json(silent=True), dict):
        return _json_error("Request body must be a JSON object.", 400)


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
    session["oauth_state"] = secrets.token_urlsafe(24)
    session["oauth_next"] = request.args.get("next", "/")
    params = {"response_type": "code", "client_id": SF_CLIENT_ID,
              "redirect_uri": _redirect_uri(), "scope": "api refresh_token",
              "state": session["oauth_state"]}
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
    expected_state = session.pop("oauth_state", None)
    returned_state = request.args.get("state", "")
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
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
    if (request.get_json(silent=True) or {}).get("manual") is not True:
        return _json_error("Click Refresh in the updated dashboard to refresh Salesforce data.", 400)
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
        result = _preview_many(sid, [opp_id])
        value = result["states"].get(opp_id, {})
        return jsonify({**value, "eligible": opp_id in result["idsToAdd"],
                        "alreadyComplete": bool(value and is_complete(value)), "oppId": opp_id,
                        "oppName": next((row.get("name") for row in _load_snapshot().get("rows", []) if row.get("id") == opp_id), opp_id),
                        "reason": value.get("blockedReason") or "Opportunity no longer meets the Partner scope."})
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
