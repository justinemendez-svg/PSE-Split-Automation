# PSE Split Automation for AppFoundry

Deploy the current dashboard as a Python/Flask application. The package embeds exactly the same HTML, JavaScript, and workflow used by the local dashboard. It uses PostgreSQL for the saved snapshot and Salesforce OAuth for each user’s Salesforce connection.

The AppFoundry portal options below were inspected on November 24, 2026 at [portal.idp.zenai-apps.com](https://portal.idp.zenai-apps.com/catalog).

## What stays the same

- Dashboard layout, three clickable categories, search, multi-select filters, CSV export, and Process Flow.
- Scope: commissionable Renewal, New Business, and Expansion Opportunities at Stage **08 - Closed**, with a Partner tagged.
- Preserve an existing PSE; fill a blank PSE from the Partner’s Owner when Update is confirmed.
- Add missing Team members as **Partner Account Executive / Read Only**. If the rep has no Split of any type, add **Overlay / Partner Account Executive / 100.00%**.
- **Refresh runs only when clicked.** Opening or reloading the page displays the saved data.
- Individual and bulk updates require confirmation and use the signed-in Salesforce user’s access.

The hosted app stores data in PostgreSQL and signs into Salesforce through OAuth. The local dashboard continues to use its local snapshot and Salesforce CLI login.

## Build the upload code

From the repository root:

```bash
python3 pse-split-appfoundry/build_standalone.py
```

Use the complete contents of **`standalone_app.py`** as the Main Application Code. This bundle already contains the UI and shared workflow, so it works without the repository’s other source files. Do not paste `docs/index.html` or the unbundled `app.py` as the entire application.

## Create the app in the portal

1. Open **Create → Python Web App**.
2. **Provide App Details:** use an available app name such as `pse-split-automation`, enter a description, and choose **Flask**.
3. **Paste Your Python Code:** paste the complete `standalone_app.py` contents into **Main Application Code**. Paste `requirements.txt` into **Requirements**.
4. **Upload Artifacts:** the standalone bundle needs no additional artifacts. Do not upload local snapshots, logs, Salesforce CLI authentication files, or credentials.
5. **Database Requirements:** enable **Include PostgreSQL Database**.
6. **AI & Model Access:** leave **Enable AI Model Access** unchecked; this dashboard does not require it.
7. **Deployment Configuration:** leave **Internal API Only (Service-to-Service)** unchecked so users can open the dashboard through AppFoundry’s normal user access route.
8. Review and create the app. Record its actual HTTPS app URL for the Salesforce callback configuration below.

The portal provides `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`. The package recognizes these automatically. Existing PostgreSQL URL variables and `PG*` variables are also supported; no database password needs to be placed in source code.

## Configure Salesforce access once

A Salesforce administrator must provide an approved OAuth Connected App for this hosted dashboard, with OAuth scopes **api** and **refresh_token**. Its callback URL must exactly match:

```text
https://<actual-appfoundry-app-host>/oauth/callback
```

In the new app’s catalog page, open **Secrets Management → Add Secret** and configure:

| Secret key | Purpose |
| --- | --- |
| `FLASK_SECRET_KEY` | A persistent random secret, shared by all app workers, for browser sessions and encryption of stored Salesforce tokens. Preserve it across redeployments. |
| `SF_CLIENT_ID` | The Salesforce Connected App consumer key. |
| `SF_CLIENT_SECRET` | The Salesforce Connected App consumer secret. |
| `SF_REDIRECT_URI` | The exact HTTPS callback URL registered in Salesforce. |

The built-in defaults use `https://zendesk.my.salesforce.com` for login and `https://zendesk.lightning.force.com` for record links. Set `SF_LOGIN_URL` and `SF_LIGHTNING_URL` only when a different org is intended. An explicit `DATABASE_URL` is optional because the portal already injects its database variables.

Restart or redeploy as required by AppFoundry after adding the secrets so the application receives them. Never paste secret values into Main Application Code, Requirements, Upload Artifacts, GitHub, or chat.

## How Refresh works after deployment

1. The user clicks **Refresh**.
2. If necessary, a Salesforce login popup opens. The user signs in through the approved Salesforce/SSO flow.
3. The app reads the scoped Opportunities, Partner Owners, Team members, and Split members from Salesforce.
4. It saves the resulting snapshot in PostgreSQL. Progress is shown beside the Refresh button.
5. The dashboard reloads that saved snapshot and updates the counts and list.

Refresh does not populate fields or add Team members or Splits. Those changes occur only through the confirmed Update actions. A new app has no local snapshot to inherit; its first data load uses the Refresh button.

The hosted snapshot is shared by the app’s users. Keep the app’s audience and the refreshing Salesforce user’s record visibility aligned with the intended dashboard audience. Each update still uses the updating user’s Salesforce access.

## Verification and updates

Before treating a deployment as ready, verify Salesforce login, one manual Refresh, progress completion, persistence after reopening the app, and the existing filter behavior. Live Salesforce writes require a separately authorized validation; the offline checks do not perform them. The actual deployed environment must also allow Salesforce HTTPS traffic and enough request/runtime time for the intended bulk workload.

The package has offline checks for OAuth state, manual-only refresh, AppFoundry database-variable mapping, a standalone import without the repository, identical UI assets, and a mocked Refresh → PostgreSQL snapshot → dashboard reload cycle. These establish package behavior, not a completed live deployment.

GitHub updates do not automatically replace code pasted into AppFoundry. After changing the dashboard or shared workflow, regenerate `standalone_app.py` and update the hosted app through its deployment process. Preserve the PostgreSQL database and stable `FLASK_SECRET_KEY` when updating.
