# Existing AppFoundry deployment

App URL: https://pse-split-automation.internal.zenai-apps.com/

This adapter builds from the current local dashboard and its scope/update rules. It does not change the local installation or copy a Salesforce CLI session. AppFoundry uses its existing PostgreSQL database and deployment repository; the public GitHub repository alone does not deploy the app.

## Salesforce admin setup

Live Salesforce access requires an approved Salesforce External Client App or Connected App. Ask the Salesforce admin to configure:

- Salesforce environment: Zendesk production, https://zendesk.my.salesforce.com
- OAuth flow: authorization code with a confidential client, PKCE S256, and client-secret verification for token exchange and refresh.
- Callback URL, exactly: `https://pse-split-automation.internal.zenai-apps.com/oauth/callback`
- OAuth scopes: `api` and `refresh_token` (the Salesforce UI may label the latter “Perform requests at any time” / `offline_access`).
- Access restricted to the intended approved users under company policy. Users retain their own Salesforce record and field permissions.
- Required reads: Opportunities and the current Partner/Owner/PSE fields, Users, Opportunity Teams, Opportunity Splits, their roles, and the read-only Coverage formula.
- Required writes for Update: Opportunity Partner Sales Rep, Opportunity Team members/roles, and Opportunity Splits/roles. Existing scope and Salesforce validations still apply. Never grant write access to the calculated Coverage field.

The admin provides the consumer/client ID, consumer/client secret, and production Organization ID through the approved secure channel. Do not paste secrets into chat, source code, GitHub, or uploaded JSON.

The AppFoundry owner adds these names in the existing app's **Secrets Management** panel:

| Secret | Value |
| --- | --- |
| `SF_CLIENT_ID` | Approved OAuth consumer/client ID |
| `SF_CLIENT_SECRET` | Approved OAuth consumer/client secret |
| `SF_EXPECTED_ORG_ID` | Zendesk production Organization ID, beginning `00D` |
| `PSE_SESSION_SECRET` | Random signing secret, at least 32 characters |
| `PSE_TOKEN_KEY` | A Fernet key generated with Python cryptography |

Generate the two app-specific secrets in a trusted local terminal, then enter their values directly into AppFoundry Secrets. Keep them out of logs and commits:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Restart/redeploy through AppFoundry after secrets are available to its runtime. Before completion, verify sign-in, the expected org restriction, and read-only Refresh with an authorized user. A successful production sign-in and Refresh cannot be verified before the admin setup. Test writes only against an explicitly approved record and an explicit Update confirmation.

## What users do

1. Click **Connect to Salesforce** and sign in with their own approved Salesforce/Okta account.
2. Click **Refresh** when they want fresh data. Connecting and opening the page do not fetch a new snapshot.
3. Read **Process Flow** in the dashboard. Assignment reviews save a session decision. Only **Update** and its confirmation write to Salesforce.
4. Use **Disconnect Salesforce** when finished. Connected sessions expire after eight hours, requiring sign-in and a fresh snapshot.

Each connected browser session has its own encrypted Salesforce tokens, snapshot, and assignment reviews in PostgreSQL. Reviews uploaded from a laptop are cleared rather than treated as approvals for another session. Disconnected visitors can use the existing shared manual upload dashboard. A connected user's upload replaces their session snapshot; a disconnected upload replaces the shared snapshot. Old snapshots remain stored but are not displayed when their schema cannot support the current checks.

## Build and deploy

```sh
python3 deployment/appfoundry/build.py --output /tmp/app.py
python3 deployment/appfoundry/test_hosted.py
```

Replace only `app.py` in the existing internal deployment repository. Retain its Dockerfile, requirements, database configuration, and `.deploy` manifests. The existing requirements include Flask, SQLAlchemy, PostgreSQL drivers, cryptography and Gunicorn. Set the production server request timeout long enough for a manual refresh or bulk update (the provided `start.sh` uses 900 seconds); the platform's request timeout must also support the operation. Do not schedule refreshes.

The build includes code and static instructions only. It does not include snapshots, secrets, logs, CLI tokens, or installation files. Synthetic offline tests check scopes, confirmation, CSRF, session isolation, OAuth state/PKCE, and the shared update workflow; they do not replace live OAuth validation.
