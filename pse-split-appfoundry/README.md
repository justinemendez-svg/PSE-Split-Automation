# PSE Split Automation for AppFoundry

This is a separate AppFoundry deployment package for **PSE Split Automation**. It uses the same dashboard HTML and JavaScript as the local website, including the opportunity cards, search, multi-select Type/Segment/Region filters, individual and bulk Salesforce updates, and CSV export.

## Refresh behavior

Clicking **Refresh data** opens Salesforce OAuth the first time, then starts a read-only Salesforce refresh. The app queries commissionable Renewal, New Business, and Expansion Opportunities with an active `Partner_Sales_Rep__c`, checks the matching Opportunity Team and Overlay split, saves the new snapshot in AppFoundry Postgres, and updates the open dashboard without a browser reload. Refresh progress appears beside the button.

Individual and bulk updates use the signed-in Salesforce user's access. Each update rechecks the current PSE and opportunity scope, adds only missing entries, and updates the shared Postgres snapshot. The team member uses role `Partner Account Executive` and Salesforce API access value `Read`; the Overlay split is owned by the tagged PSE at 100%.

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

The first successful refresh creates the shared snapshot table automatically. Refreshes replace that snapshot, so use a Salesforce user whose record access matches the intended dashboard audience. The AppFoundry app itself has not been deployed by this repository change; it is ready for deployment after its AppFoundry URL and Salesforce Connected App values are available.

## Rebuild after dashboard changes

The shared UI source remains in `../docs/index.html` and `../docs/app.js`. Run `python3 build_standalone.py` again after changing either file, then deploy the regenerated `standalone_app.py`.
