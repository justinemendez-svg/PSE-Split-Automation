# Install your own PSE Split Automation dashboard

Open Codex, choose a local folder, then copy and paste this entire prompt. Approve the installation and browser-login prompts that match this setup. Use your own Salesforce account.

```text
Install the latest local PSE Split Automation app on my computer from:
https://github.com/justinemendez-svg/PSE-Split-Automation

Clone the repo, or pull its latest main branch if already cloned. Preserve my local changes and data. Read README.md and follow the current setup. Check/install Python 3.10+, Git and Salesforce CLI as needed.

Use MY OWN Salesforce account. If login is needed, run:
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
Guide me through Salesforce/Okta authentication. Never print or share tokens.

From the cloned repository folder, use a supported Python interpreter. On macOS, run python3 install_local_service.py with that interpreter, then open http://localhost:8773/ in Chrome or my default browser. On another OS, start the documented local server and explain that the terminal must stay open.

Keep the complete current dashboard, layout, category boxes, multi-select filters, Process Flow, assignment review, manual Refresh and confirmed individual/bulk Salesforce update functionality. Keep the rolling data scope: current and previous Zendesk fiscal quarters by Opportunity Close Date (fiscal year February–January). Use the current local server and do not schedule automatic refreshes.

Preserve both valid existing Split roles: Partner Account Executive and Partner Account Executive - Coverage. Display actual Coverage values and flag Coverage-role Splits only when unchecked. Do not tick Coverage or change Salesforce while checking data. Supported Salesforce changes must require an Update click and confirmation.

Verify the local health endpoint and that a first-time installation shows Click Refresh without needing copied data. If localhost does not resolve, try http://127.0.0.1:8773/. Show me Process Flow and explain that Refresh downloads current SFDC data only when clicked. Let me click Refresh to load my first data. Do not update any Salesforce records during setup, and never commit data or credentials. Do not copy Justine’s snapshot or Salesforce session. If my Salesforce permissions block access, explain exactly what access is missing.

If you have questions, contact Justine Mendez / justine.mendez@zendesk.com.
```

After setup, open http://localhost:8773/, read **Process Flow**, then click **Refresh**. Review each action before confirming a Salesforce update. To install a later version, ask Codex to pull the latest repository, rerun the installer and reload the dashboard while preserving your snapshot, audit and logs.
