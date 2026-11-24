# PSE Split Automation for AppFoundry

This is a separate AppFoundry deployment package for **PSE Split Automation**. It uses the same dashboard HTML and JavaScript as the local website, including the opportunity cards, search, clickable PSE worklist boxes, Fiscal Quarter–Year/Type/Segment/Region/PSE Status filters, Process Flow guide, individual and bulk Salesforce updates, and CSV export.

## Refresh behavior

**Refresh** runs only when clicked. Opening or reloading the page displays the saved snapshot. The button uses Salesforce OAuth and performs a read-only pull of commissionable **Renewal**, **New Business**, and **Expansion** Opportunities at Stage exactly **08 - Closed** with a tagged **Partner** (`Referring_Partner__c`). The shared workflow resolves the existing PSE or proposes the Partner Account’s Owner when the PSE field is blank. Team and Split membership are checked, the snapshot is saved in PostgreSQL, and the open dashboard updates. Refresh progress appears beside the button.

Individual and bulk updates use the signed-in Salesforce user’s access. After confirmation, each Opportunity preserves its existing PSE or fills a blank PSE from its Partner’s Owner, adds a missing Team member as **Partner Account Executive / Read Only**, and adds an **Overlay / Partner Account Executive / 100.00%** split only if that rep has no existing Split of any type. Existing entries are preserved. Inactive users remain visible and can be filtered. The same scope and ordered checks are used by the local app.

The boxes, search, and multi-select filters apply to counts, results, bulk updates, and CSV exports. Fiscal Quarter–Year uses Close Date and Zendesk’s February–January fiscal calendar. See the root [README](../README.md) for the current workflow and filter details.

## Deploy

1. In AppFoundry, create a **Python Web App → Flask** app and attach PostgreSQL.
2. Build the single-file app from this repository:

   ```bash
   python3 build_standalone.py
   ```

3. Deploy `standalone_app.py` with `requirements.txt`. Set the start command to:

   ```bash
   gunicorn --workers 2 --timeout 1800 standalone_app:app
   ```

4. Add these AppFoundry environment variables:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | AppFoundry's injected PostgreSQL URL |
   | `FLASK_SECRET_KEY` | A persistent random secret for signed login sessions and encrypting Salesforce tokens at rest |
   | `SF_CLIENT_ID` | Salesforce Connected App consumer key |
   | `SF_CLIENT_SECRET` | Salesforce Connected App consumer secret |
   | `SF_REDIRECT_URI` | `https://<appfoundry-host>/oauth/callback` |
   | `SF_LOGIN_URL` | `https://zendesk.my.salesforce.com` |
   | `SF_LIGHTNING_URL` | `https://zendesk.lightning.force.com` |

5. Configure the Salesforce Connected App callback URL to exactly match `SF_REDIRECT_URI`. Enable OAuth scopes `api` and `refresh_token`. Then redeploy and open the AppFoundry app.

The first successful refresh creates the shared snapshot table automatically. Refreshes replace that snapshot, so use a Salesforce user whose record access matches the intended dashboard audience. Deploying the local repository does not deploy this optional hosted app; it requires its own AppFoundry environment and Salesforce Connected App configuration.

## Rebuild after dashboard changes

The shared UI source remains in `../docs/index.html` and `../docs/app.js`. Run `python3 build_standalone.py` again after changing either file, then deploy the regenerated `standalone_app.py`.
