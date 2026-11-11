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

The worklist includes only commissionable **Renewal**, **New Business**, and **Expansion** Opportunities with an active assigned PSE missing from the Opportunity Team, the 100% **Overlay** split, or both. Search supports Opportunity name, Account name, and Opportunity ID. Type, Segment, and Region filters support multiple selections. Individual and filtered bulk actions add each rep with role **Partner Account Executive** and **Read** access, and create that PSE's 100% Overlay split, after confirmation. Existing entries are kept without duplicates. Inactive reps and Opportunities without an assigned PSE are excluded.

The Salesforce snapshot and write audit stay local and are excluded from Git.
