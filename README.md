# PSE Split Automation

A local Salesforce dashboard for commissionable Opportunities with a tagged Partner and a missing Partner Sales Rep field, Opportunity Team membership, or Opportunity Split membership.

## Clone and run

Requirements: Python 3, Salesforce CLI, and a Salesforce account with access to the Opportunities you need to review. The `serve.command` launcher is for macOS and opens Google Chrome; on other systems, use the manual server command below.

```bash
git clone https://github.com/justinemendez-svg/PSE-Split-Automation.git
cd PSE-Split-Automation
brew install sf
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
./serve.command
```

The first run copies the runnable dashboard into your user Application Support folder, installs a per-user macOS LaunchAgent, starts the server, and opens [http://localhost:8773](http://localhost:8773) in Google Chrome. After that, the server starts automatically at login and restarts if it exits, so the same URL stays available while you use your Mac. The dashboard automatically loads Salesforce data when its snapshot is missing or more than 30 minutes old. **Refresh data** still forces an immediate refresh. Run `./serve.command` again after pulling repository changes to update the local app copy.

To remove automatic startup, run:

```bash
python3 uninstall_local_service.py
```

For a temporary/manual run (or on another OS), start the server in a terminal and leave it open:

```bash
python3 serve_local.py --org prod
```

Then open `http://localhost:8773` manually. No Python packages need to be installed for the local version; it uses the Python standard library.

## Worklist and actions

The scope is `Non_Commissionable__c = false`, Opportunity Type **Renewal**, **New Business**, or **Expansion**, and a populated **Partner** lookup. In this org the Partner label maps to `Referring_Partner__c` (the requested flow called it `Partner__c`). Only Stage **08 - Closed** is included; all close dates are included.

1. Require Stage **08 - Closed** and a tagged Partner; otherwise skip the Opportunity.
2. Keep an existing `Partner_Sales_Rep__c`. If blank, use the tagged Partner Account’s Owner and populate the field when Update is confirmed.
3. If that rep is absent from the Opportunity Team, add them as **Partner Account Executive**, with **Read Only** access.
4. If that rep has no Opportunity Split of any type, add an **Overlay** split with **Opportunity Team Role = Partner Account Executive** (`Opportunity_Team_Role__c`) and **100.00%**. Preserve any existing split’s type, role, and percentage.

An inactive user is displayed rather than filtered by the new flow. Unresolvable Partner Owners and Salesforce validation failures are reported. A failed step stops subsequent additions for that Opportunity. Retry applies remaining missing steps. Blank-field writes use a record-version condition so concurrent edits reject the write rather than overwrite a newly populated PSE.

**Process Flow** at the top opens a full-page guide. Close or Escape returns to the dashboard with search, filters, pagination, and scroll position preserved.

The list shows Opportunities missing a PSE field, Team membership, or Split membership. Each card includes Partner, Partner Owner, effective PSE and status, plus Opportunity and Account details. Fiscal Quarter–Year, Opportunity Type, Market Segment, Region, and PSE Status filters support multiple selections. Search supports Opportunity name, Account name, and Opportunity ID.

**Update in Salesforce** processes one Opportunity. **Update all N in Salesforce** processes all filtered results across all pages, using each Opportunity’s own PSE or Partner Owner. Both use the same workflow and require confirmation. Refresh only reads Salesforce; it never populates fields or adds records. Every update rechecks scope, current assignment, Team membership, and Split membership.

Offline workflow checks: `python3 -m unittest discover -s tests -v`.

## Local files and privacy

- `workflow.py` shares the classification and ordered update rules between local and hosted versions.
- `refresh.py` pulls the current scoped Opportunity, Team, and Split data from Salesforce.
- `serve_local.py` serves the dashboard, provides the in-place refresh API, confirms and writes updates, and records a local audit log.
- `docs/index.html` and `docs/app.js` contain the dashboard UI.
- `docs/pse-data.json` stores the local Salesforce snapshot and is excluded from Git.
- `writes.log.jsonl` stores the local write audit and is excluded from Git.

The snapshot contains Opportunity and employee data, so it is generated on each user's machine and is not part of the clone. A refresh only reads Salesforce. Update actions write to Salesforce after confirmation.

## AppFoundry package

The separate [`pse-split-appfoundry/`](pse-split-appfoundry/README.md) package embeds the same UI and uses AppFoundry Postgres and Salesforce OAuth. It is optional for running the complete local version.

The summary boxes are interactive filters: **Opportunities with tagged PSE** and **Partner but no tagged PSE** show records missing the selected rep from the Team, Splits, or both. **All Opportunities to update** restores the full worklist, including field-only updates. Box counts follow search and other filters. Bulk updates and CSV export follow the selected box.

**Fiscal Quarter–Year** appears before Opportunity Type and supports multiple selections based on Opportunity Close Date. Zendesk fiscal years run February–January and use the ending year: FY2027 Q1 is Feb–Apr 2026; Q4 is Nov 2026–Jan 2027. The filter applies to cards, list results, bulk updates, and CSV exports. CSV includes the derived `fiscalQuarter` column.
