# Install your own PSE Split Automation dashboard

Open Codex, choose a local folder, then copy and paste this entire prompt. Approve the installation and browser-login prompts that match this setup. Use your own Salesforce account.

```text
Install the latest local PSE Split Automation app on my computer from:
https://github.com/justinemendez-svg/PSE-Split-Automation

Clone the repo, or pull its latest main branch if already cloned. Preserve my local changes and data. Read README.md and follow the current setup. Check/install Python 3.10+, Git and Salesforce CLI as needed.

Use MY OWN Salesforce account. If login is needed, run:
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
Guide me through Salesforce/Okta authentication. Never print or share tokens.

On macOS, run python3 install_local_service.py, then open http://localhost:8773/ in Chrome. On another OS, start the documented local server and explain that the terminal must stay open.

Keep the complete current dashboard, layout, filters, Process Flow, manual Refresh and confirmed individual/bulk Salesforce update functionality. Do not deploy the older AppFoundry build or schedule automatic refreshes.

Verify the app opens correctly. Show me Process Flow and explain that Refresh downloads current SFDC data only when clicked. Let me click Refresh to load my first data. Do not update any Salesforce records during setup, and never commit data or credentials.

If you have questions, contact Justine Mendez / justine.mendez@zendesk.com.
```

After setup, open http://localhost:8773/, read **Process Flow**, then click **Refresh**. Review each action before confirming a Salesforce update. To install a later version, ask Codex to pull the latest repository, rerun the installer and reload the dashboard while preserving your snapshot, audit and logs.
