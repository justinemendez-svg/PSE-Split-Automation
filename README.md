# PSE Split Automation

A local Salesforce dashboard for commissionable Opportunities with an active Partner Sales Rep who is missing from the Opportunity Team, the 100% Overlay split, or both.

## Clone and run

Requirements: Python 3, Salesforce CLI, and a Salesforce account with access to the Opportunities you need to review. The `serve.command` launcher is for macOS and opens Google Chrome; on other systems, use the manual server command below.

```bash
git clone https://github.com/justinemendez-svg/PSE-Split-Automation.git
cd PSE-Split-Automation
brew install sf
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
./serve.command
```

The script starts the local server and opens [http://localhost:8773](http://localhost:8773) in Google Chrome. On a fresh clone, click **Refresh data** once to create the local snapshot. The page updates in place when refresh finishes. You can also start the server without the launcher:

```bash
python3 serve_local.py --org prod
```

Then open `http://localhost:8773` manually. No Python packages need to be installed for the local version; it uses the Python standard library.

## Worklist and actions

The Salesforce refresh includes only `Non_Commissionable__c = false`, Opportunity Type **Renewal**, **New Business**, or **Expansion**, and an active user in `Partner_Sales_Rep__c`. Opportunities without an assigned PSE and those with an inactive PSE are excluded.

The dashboard shows opportunities missing the tagged active PSE from the Opportunity Team, the 100% Overlay split, or both. Each row shows Opportunity name, ID, stage, close date, type, account, account owner, market segment, region, and PSE. Search supports Opportunity name, Account name, and Opportunity ID. Type, Segment, and Region filters support multiple selections.

Individual **Update in Salesforce** and filtered **Update all** actions add the assigned PSE as an Opportunity Team Member with role **Partner Account Executive** and Read Only access, and create that PSE's 100% Overlay split. Salesforce writes happen only after confirmation; the server rechecks the current PSE assignment, active status, commissionability, and Opportunity Type before writing.

## Local files and privacy

- `refresh.py` pulls the current scoped Opportunity, Team, and Split data from Salesforce.
- `serve_local.py` serves the dashboard, provides the in-place refresh API, confirms and writes updates, and records a local audit log.
- `docs/index.html` and `docs/app.js` contain the dashboard UI.
- `docs/pse-data.json` stores the local Salesforce snapshot and is excluded from Git.
- `writes.log.jsonl` stores the local write audit and is excluded from Git.

The snapshot contains Opportunity and employee data, so it is generated on each user's machine and is not part of the clone. A refresh only reads Salesforce. Update actions write to Salesforce after confirmation.

## AppFoundry package

The separate [`pse-split-appfoundry/`](pse-split-appfoundry/README.md) package embeds the same UI and uses AppFoundry Postgres and Salesforce OAuth. It is optional for running the complete local version.
