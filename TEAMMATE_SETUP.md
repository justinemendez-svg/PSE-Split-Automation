# Set up PSE Split Automation from GitHub

1. Clone the repository and open its folder:

   ```bash
   git clone https://github.com/justinemendez-svg/PSE-Split-Automation.git
   cd PSE-Split-Automation
   ```

2. Install Salesforce CLI if needed: `brew install sf`.
3. Log in through Zendesk SSO:

   ```bash
   sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
   ```

4. On macOS, double-click `serve.command` (or run `./serve.command`). The first run copies the runnable app into your user Application Support folder, installs a per-user background service, starts the dashboard, and opens it in Chrome. The service starts at login and restarts if it exits, so `http://localhost:8773` stays available while your Mac is on and awake. Run `serve.command` again after pulling repository changes to update the app copy.

   On other operating systems, start the local dashboard in a terminal and leave it running:

   ```bash
   python3 serve_local.py --org prod
   ```

5. Open [http://localhost:8773](http://localhost:8773). The dashboard automatically loads Salesforce data if its local snapshot is missing or more than 30 minutes old; **Refresh data** forces an immediate refresh. To remove the macOS background service later, run `python3 uninstall_local_service.py`.

The worklist includes only commissionable **Renewal**, **New Business**, and **Expansion** Opportunities at Stage **08 - Closed** with a tagged **Partner**, and a missing PSE field, Team membership, or Split membership. Keep an existing PSE; if blank, populate it from the tagged Partner’s Owner when Update is confirmed. Add missing Team members as **Partner Account Executive** with **Read Only** access. If the rep has no Split of any type, create a **100% Overlay** split with **Opportunity Team Role = Partner Account Executive**. Existing PSE values, Team entries, and Splits are preserved. User status is displayed rather than excluding inactive reps.

Use the clickable boxes to filter tagged or blank PSE worklists. **Fiscal Quarter–Year**, **Opportunity Type**, **Market Segment**, **Region**, and **PSE Status** support multiple selections. Fiscal quarters use Close Date and Zendesk’s February–January fiscal calendar. Search supports Opportunity name, Account name, and Opportunity ID. Individual updates apply to one Opportunity; bulk updates and CSV export follow the current box, search, and filters across all pages. **Process Flow** explains the full workflow.

The Salesforce snapshot and write audit stay local and are excluded from Git.

For the current field mapping and complete workflow, see [README.md](README.md#scope-and-workflow) or **Process Flow** in the dashboard.
