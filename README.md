# PSE Split Automation

The latest local Salesforce dashboard. Clone this repository to install the same dashboard, Process Flow, filters, manual Refresh, assignment review, and confirmed Salesforce updates.

Repository: https://github.com/justinemendez-svg/PSE-Split-Automation

## Install

See [TEAMMATE_SETUP.md](TEAMMATE_SETUP.md) for a complete copy-and-paste Codex prompt. Each person uses their own Salesforce account and permissions.

Requires Python 3.10+, Git and Salesforce CLI (`sf`). No Python packages or Node runtime are required to run the app. Node is used only for offline JavaScript checks.

```bash
git clone https://github.com/justinemendez-svg/PSE-Split-Automation.git
cd PSE-Split-Automation
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
python3 install_local_service.py
```

On macOS, open http://localhost:8773/ and click **Refresh** to load your first snapshot. The service starts at login and restarts if it stops; this does not schedule data refreshes. Double-click `serve.command` to reinstall/start the app and open Chrome (or your default browser if Chrome is unavailable). After pulling later updates, rerun the installer and reload the browser.

On Windows/Linux, run `python3 serve_local.py --org prod --port 8773` and keep that terminal open. The automatic background-service installer is macOS-only. Use `python` instead of `python3` if that is your Python 3 command.

## Current scope

```sql
Referring_Partner__c != NULL
AND Non_Commissionable__c = FALSE
AND StageName = '08 - Closed'
AND Partner_Type__c <> 'Startup Program Partner'
AND Referring_Partner__r.Name NOT IN ('AWS Marketplace')
```

All Opportunity Types are included. Close Date is restricted to the previous and current Zendesk fiscal quarters, based on today in Asia/Manila. FY2027 Q4 includes FY2027 Q3 + Q4 (1 August 2026–31 January 2027); FY2028 Q1 includes FY2027 Q4 + FY2028 Q1; FY2028 Q2 includes FY2028 Q1 + Q2. The window rolls forward without changing code. Saved snapshots are narrowed to the current window on load; fresh Salesforce data is still fetched only when Refresh is clicked. Partner Type is the Opportunity's `Partner_Type__c`; Partner Account Type is retained separately. Region uses `Referring_Partner__r.Region__c`.

## Using the dashboard

Read **Process Flow** before applying updates. Close returns to the dashboard.

- **Refresh** reads current Salesforce data only when clicked. Page reloads and filters use the saved snapshot.
- Four boxes show **Partner Opps with the Correct PSE Tagged**, **Partner Opps with No PSE Tagged**, **Partner Opps | Tagged PSE and Partner Owner is different**, and **All Opps to update**.
- Counts use unique Opportunity IDs, treating 15/18-character versions as the same Opportunity.
- Readiness buttons appear in this order: **All Opps**, **Need RVP confirmation**, **Ready to update**. They combine with category, search and filters.
- Search by Opportunity ID, Opportunity name or Account name. Multi-select filters: Fiscal Quarter–Year, Month, Opportunity Type, Market Segment, Region and PSE Status. Month options follow selected fiscal quarters. Zendesk's fiscal year runs February–January.
- Cards show the clickable Opportunity name, metadata, Partner and Partner Owner, PSE, and three Current / Required / Action sections. Different PSE and Partner Owner names are red. Pending actions are red; correct actions are green. Salesforce access `Read` displays as **Read Only**.
- **Export CSV** exports the filtered list. Other extracted fields remain in the snapshot and CSV, including Partner Region, Owner/PSE Profiles, Partner Deal Source, Deal Type and Total Booking ARR.

## Salesforce update rules

1. An active matching PSE can be ready. If the PSE field is blank, use the active Partner Owner.
2. A populated PSE is preserved unless an explicit assignment review confirms another active user. A different PSE requires review. An inactive PSE needing changes requires confirmation of RVP approval and an active covering PSE with **Partner Account Executive - Coverage**. The dashboard does not contact the RVP.
3. Missing Team membership is added with **Partner Account Executive** (or the reviewed Coverage role) and **Read Only** access. Existing incorrect/blank roles are corrected; existing access is preserved.
4. Missing Split membership is added as **Overlay**, **100%**, with the required role. Existing roles are corrected; existing split type and percentage are preserved. Multiple memberships or unresolved information are blocked for individual review.

**Update in Salesforce** affects one Opportunity. **Update all ready** affects ready records across the current filtered pages after confirmation. Review, RVP and blocked records are excluded until resolved. Every stage rechecks scope, assignment and user activity; a failed Team step stops Split changes. Earlier successful changes are retained and reported so remaining work can be retried. Already-correct matching assignments require no write, even if the user is now inactive.

Saved assignment reviews are local. Refresh revalidates them; assignment changes or deactivated selected users require a new review. Salesforce permissions and validation rules still apply.

## Local files and data

The single UI is `docs/index.html` + `docs/app.js`. `serve_local.py` serves it on loopback only. `local_scope.py` owns the current scope/profile; `local_updates.py` owns plans and confirmed writes. `refresh.py` performs the read-only extraction.

On macOS, the installer copies managed source into `~/Library/Application Support/com.zendesk.pse-split-automation` and removes known obsolete builds. It preserves the saved `docs/pse-data.json`, write audit and logs. The repo and installed app have separate snapshots; the dashboard Refresh uses the installed app's snapshot. No Salesforce data, credentials or write audits belong in Git.

The previous AppFoundry package and duplicate UI have been removed from this repository. Existing hosted deployments are separate and are not updated by a Git push. This is a local server app; serving only the HTML as a static website does not provide Refresh or Salesforce updates.

Optional read-only export of this same current data profile:

```bash
python3 export_for_upload.py --org prod --export-dir "<YOUR_ACTUAL_SYNCED_FOLDER_PATH>"
```

The folder must be your own accessible local path. This command replaces `pse-data.json` there; it does not change Salesforce or upload anything. It now exports the complete current profile, so an older hosted app may require a separate upgrade before accepting it.

## Offline checks

```bash
python3 -m unittest discover -s tests
node tests/test_ui_flow.js
node tests/test_manual_refresh.js
git diff --check
```

These checks use synthetic records and mocked Salesforce operations. They do not update Salesforce.

Questions: **Justine Mendez — justine.mendez@zendesk.com**.

## First-time setup and access

A new clone intentionally has no saved Salesforce data. It should open with **No Salesforce snapshot yet. Click Refresh to load data.** Click Refresh yourself to create your own snapshot. The same app and rules are installed for everyone; visible Opportunities and allowed changes depend on each person’s Salesforce access.

Your own Zendesk Salesforce account needs API access and read access to the queried Opportunity, Partner Account, User/Profile, Opportunity Team and Opportunity Split fields. Salesforce updates also require the appropriate create/edit access and must pass your org’s validation rules. Codex should explain permission errors; it cannot grant Salesforce access.

If `localhost` does not resolve on your computer, use http://127.0.0.1:8773/. If port 8773 is already occupied, identify the existing app before stopping anything; a foreground server can use another `--port`. If authentication expires, log in again with the documented Salesforce CLI command and restart the service. On macOS, the installer uses the same Python interpreter that ran it, validates your CLI session before replacing app files, and preserves your saved data during upgrades.
