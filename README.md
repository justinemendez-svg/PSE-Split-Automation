# PSE Split Automation

Repository: [justinemendez-svg/PSE-Split-Automation](https://github.com/justinemendez-svg/PSE-Split-Automation).

A local Salesforce dashboard for closed Opportunities that need a Partner Sales Rep (PSE) field, Opportunity Team, or Opportunity Split update.

## Scope and workflow

Include only Opportunities with all of the following:

- Stage exactly **08 - Closed**.
- Commissionable: `Non_Commissionable__c = false`.
- Opportunity Type **Renewal**, **New Business**, or **Expansion**.
- A populated **Partner** lookup: `Referring_Partner__c` in this Salesforce org.

For each Opportunity:

1. Check whether the closed Opportunity has a Partner tagged; otherwise skip it.
2. Preserve an existing `Partner_Sales_Rep__c`. If blank, populate it from the tagged Partner Account’s Owner when Update is confirmed.
3. If the PSE is missing from the Opportunity Team, add the user with **Team Role = Partner Account Executive** and **Opportunity Access = Read Only**.
4. If the PSE has no Opportunity Split of any type, create an **Overlay** split with **Opportunity Team Role = Partner Account Executive** (`Opportunity_Team_Role__c`) and **100.00%**.

Preserve existing PSE values, Team entries, and Split entries, including their existing role, access, type, and percentage. Inactive users remain visible and can be filtered. If a Partner Owner cannot be resolved or Salesforce rejects a step, report it and keep the Opportunity incomplete. A failed step stops later additions for that Opportunity; retry continues with the remaining steps. Blank PSE writes use a record-version condition to avoid overwriting a concurrent edit.

## Dashboard

The four clickable summary boxes filter the worklist:

| Box | Opportunities shown |
| --- | --- |
| Opps with tagged correct PSE | Partner Account Owner and tagged PSE are the same user; the PSE is missing from the Team, Splits, or both. |
| Opps with Partner, PSE different from Owner | Partner Account Owner and tagged PSE are different users; the PSE is missing from the Team, Splits, or both. Updates preserve the tagged PSE. |
| Partner but no tagged PSE | Partner and blank PSE; the proposed Partner Owner is missing from the Team, Splits, or both. |
| All Opps to update | Partner Opportunities with or without a tagged PSE that need a Team or Split update. |

The three PSE categories are mutually exclusive and follow the same search and filters. Matching uses Salesforce user IDs, with a name fallback for older exports without Partner Owner IDs. Records that cannot be compared remain in All Opps to update. If the existing PSE (or proposed Partner Owner for a blank PSE) is already on both the Team and Splits, the Opportunity is excluded from every box.

Cards show Opportunity name, ID, stage, Close Date, type, Account, Account Owner, segment, region, Partner, Partner Owner, PSE, and user status.

Filters support multiple selections and appear in this order: **Fiscal Quarter–Year**, **Opportunity Type**, **Market Segment**, **Region**, **PSE Status**. Their default labels omit “All.” Search supports Opportunity name, Account name, and Opportunity ID.

Fiscal Quarter–Year is derived from Opportunity Close Date using Zendesk’s February–January fiscal calendar and the fiscal ending year. For FY2027: Q1 is Feb–Apr 2026, Q2 May–Jul 2026, Q3 Aug–Oct 2026, and Q4 Nov 2026–Jan 2027. For a blank PSE, PSE Status refers to the proposed Partner Owner. If a Close Date or user status is unavailable, the relevant filter includes an unavailable option.

- **Update in Salesforce** applies the workflow to one Opportunity after confirmation.
- **Update all N in Salesforce** applies it to all results across all pages using the selected box, search, and filters. Each Opportunity uses its own PSE or Partner Owner.
- **Export CSV** exports that filtered worklist, including fiscal quarter.
- **Refresh** reads Salesforce and rebuilds the snapshot. It makes no Salesforce changes. Data refresh happens only when someone clicks **Refresh**. Opening or reloading the dashboard displays the saved snapshot, even if it is old. A missing snapshot prompts the user to click Refresh.
- **Process Flow** opens the full workflow and instructions. Close or Escape returns to the dashboard with filters and position preserved.

## AppFoundry upload version

The hosted app uses **Upload the data**, with no Salesforce OAuth connection. Run `python3 export_for_upload.py --org prod` locally to read Salesforce and save `pse-data.json` in `GTM Ops/MSS/PSE_Split_Automation` in the configured synced Google Drive. Open the upload page, choose that file and click **Upload data to dashboard**; Close or Escape returns to the dashboard. Uploads replace the shared saved snapshot and never change Salesforce records.

The hosted app retains search, category boxes, multi-select filters, Process Flow, and CSV export. Use the local dashboard for Salesforce updates. See [AppFoundry setup and upload instructions](pse-split-appfoundry/README.md).

## Clone and run

Requirements: Python 3, Salesforce CLI, and your own Salesforce access to the records and update operations. The local app uses only the Python standard library.

```bash
git clone https://github.com/justinemendez-svg/PSE-Split-Automation.git
cd PSE-Split-Automation
brew install sf
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
./serve.command
```

On macOS, `serve.command` copies the app into `~/Library/Application Support/com.zendesk.pse-split-automation`, installs a per-user LaunchAgent, starts the server, and opens Chrome at [http://localhost:8773](http://localhost:8773). The server starts at login and restarts if it stops. The Mac must be on and awake. Each user logs in with their own Salesforce CLI session.

After pulling updates, run `./serve.command` again to update the installed app copy. To remove automatic startup, run `python3 uninstall_local_service.py`.

For a temporary server or another operating system, keep this command running in a terminal and open the same URL:

```bash
python3 serve_local.py --org prod
```

See [TEAMMATE_SETUP.md](TEAMMATE_SETUP.md) for the teammate checklist.

## Source and local data

| Path | Purpose |
| --- | --- |
| `workflow.py` | Shared classification, scope, and ordered update rules. |
| `refresh.py` | Read-only Salesforce snapshot refresh. |
| `serve_local.py` | Local dashboard server, previews, confirmed updates, and audit logging. |
| `docs/index.html`, `docs/app.js` | Current dashboard and Process Flow UI. |
| `serve.command`, `install_local_service.py`, `uninstall_local_service.py` | macOS service setup and removal. |
| `tests/test_workflow.py` | Offline workflow, failure handling, session, and HTTP checks; no Salesforce writes. |
| `pse-split-appfoundry/` | Optional hosted package sharing the same workflow and UI. |

`docs/pse-data.json`, Salesforce credentials, logs, and `writes.log.jsonl` are excluded from Git. Each clone generates its own snapshot using the signed-in user’s access. No live Salesforce data is bundled in the repository.

Run offline checks with:

```bash
python3 -m unittest discover -s tests -v
node --check docs/app.js
node tests/test_manual_refresh.js
node tests/test_categories.js
node tests/test_ui_flow.js
```

The Python suite uses a temporary localhost server and mocked Salesforce operations. Hosted adapter checks run when the optional Flask/SQLAlchemy/cryptography dependencies are installed; otherwise they are skipped. JavaScript checks exercise the categories, fiscal calendar, UI update paths, and manual-only refresh with synthetic records.

The optional [AppFoundry package](pse-split-appfoundry/README.md) uses PostgreSQL and Salesforce OAuth. It is not required for the complete local dashboard.
