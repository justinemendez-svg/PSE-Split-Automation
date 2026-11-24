# PSE Split Automation for AppFoundry

The hosted dashboard uses manual JSON uploads. No Salesforce OAuth credentials, Salesforce CLI installation, or direct Salesforce connection are required in AppFoundry. Local Salesforce updates continue to work through the local dashboard.

## Refresh the export locally

From the cloned repository, with an existing Salesforce CLI login:

```bash
python3 export_for_upload.py --org prod
```

This runs the same read-only Salesforce extraction as the local dashboard and writes a validated `pse-data.json` to:

```text
/Users/justine.mendez/Library/CloudStorage/GoogleDrive-justine.mendez@zendesk.com/Shared drives/GTM Ops/MSS/PSE_Split_Automation/pse-data.json
```

It also refreshes the repository's local snapshot. Run it again whenever new Salesforce data is needed; nothing is scheduled automatically. Teammates can specify their own synced Google Drive location:

```bash
python3 export_for_upload.py --org prod --export-dir "/path/to/PSE_Split_Automation"
```

The portable export includes the complete dashboard worklist: closed, commissionable Renewal, New Business, and Expansion Opportunities with a tagged Partner where the PSE or proposed Partner Owner is missing from the Team or Splits. Already-complete membership records are omitted because no dashboard category displays them. The local snapshot retains all checked records. The compact JSON file contains no Salesforce access tokens or login credentials. It is operational Salesforce data; keep it in the intended shared folder and app audience.

## Upload the data

1. Open the hosted dashboard and click **Upload the data**.
2. Choose the generated **pse-data.json**. Review the extraction date and record count.
3. Click **Upload data to dashboard**. The server validates the complete file before replacing the saved shared snapshot.
4. Click **Close · Back to dashboard** or press Escape. The counts, list, filters and CSV use the uploaded data.

The maximum file size is 25 MB. The filtered CSV exported by the dashboard is not an upload file. Malformed JSON, old scope versions, duplicate Opportunity IDs, invalid membership checks and out-of-scope rows are rejected without replacing the saved snapshot. A valid empty export is supported.

Opening/reloading shows the saved uploaded snapshot. Changing the Google Drive file does not update AppFoundry automatically: rerun the local command and upload the new JSON. Uploaded data persists in PostgreSQL and is shared by users of this app.

The hosted dashboard supports categories, search, multi-select filters, Process Flow, CSV export and links to Salesforce. It does not run Salesforce updates or Salesforce refreshes. Use the local app for individual/bulk Salesforce updates. Uploads never write to Salesforce.

## Build and deploy

```bash
python3 pse-split-appfoundry/build_standalone.py
```

Use **Create → Python Web App → Flask** in AppFoundry. Paste the complete `standalone_app.py` as Main Application Code and the complete `requirements.txt` as Requirements. Leave Files to Bundle empty. Enable PostgreSQL Database, leave AI Model Access off, and leave Internal API Only off for normal browser access.

The bundle embeds the exact UI, workflow and snapshot validation code. For an existing app, replace its deployed `app.py` with the rebuilt standalone bundle and rebuild. Preserve its database. GitHub updates do not automatically update the separate Gitea deployment repository.

The portal injects `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`; PostgreSQL connection URL variables are also supported. Both `psycopg2-binary` and `psycopg[binary]` are included to support the configured connection driver. If a driver is missing, update requirements and rebuild.

Salesforce OAuth setup is no longer used. No `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `SF_REDIRECT_URI`, or `FLASK_SECRET_KEY` is needed for this upload workflow. AppFoundry's existing access gateway still controls access to the hosted app.
