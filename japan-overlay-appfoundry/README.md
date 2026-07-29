# Japan Overlay AE Review — App Foundry app

The same worklist as the local GitHub Pages version, but as a **Flask app** that
reads its data from the app's Postgres database. Data is loaded by dragging the
refresh script's CSV onto the `/admin` page — viewers just open the link, no
upload and no login needed.

## What it checks

Every Closed-Won Japan opportunity (Dir Team = Japan, segment Commercial or
Enterprise, **any** deal type, **commissionable**, **Total Booking ARR (USD) > 0**)
should have **Mari Takiyama** — the sole permitted AE Overlay — tagged in **both**
places:

1. the **Opportunity Team**, with role **"AE Overlay"**, **and**
2. an **Opportunity Split** of type **"Overlay"**.

Each opp is classified:

| Bucket | Meaning |
|---|---|
| **OK** | Mari is on the team (AE Overlay) **and** on an Overlay split. |
| **Needs Mari** | Mari is missing from the team, the split, or both (the reason says which). |
| **Someone else** | A **non-Mari** user holds the AE Overlay team role — a governance exception to escalate. |

By default it **detects and reports**. It can *also* **write** the fix straight
to Salesforce (add Mari to the team + the Overlay split) via an **"Update in
Salesforce"** button and an **"Update all"** bulk button — but only once the
one-time Connected App setup below is done. Until then those buttons stay
harmless deep links that open the opp in Salesforce for a manual edit. See
**Auto-write (optional)** below.

## What's here

| File | Purpose |
|---|---|
| **`app.py`** | ⭐ The whole app in one file — Flask routes + the classification logic + the viewer UI inline. Deploy this. |
| `requirements.txt` | Python dependencies |

Single self-contained file — deploy `app.py` as-is.

## Routes

- **`/`** — the worklist. Loads `/data.json` and renders the table; shows an empty
  state pointing to `/admin` if nothing is uploaded yet. Cards filter by bucket.
- **`/data.json`** — rebuilds the payload from the `japan_overlay` table,
  **re-deriving the OK / Needs Mari / Someone-else classification at render time**
  (same rule as `refresh.analyze`), so the view always reflects the current rule.
- **`/admin`** — drag-and-drop CSV upload. Open to anyone with the link.
- **`/debug`** — diagnostics: raw + normalized column headers, row count, sample
  row, last upload stamp. Visit this first when something looks off.

## The one table (upload this CSV)

`refresh.py` writes **`Japan_Overlay_AE_Automation.csv`** — **one row per
opportunity**, carrying the raw facts only (the app re-derives the verdict):

| Column | Notes |
|---|---|
| `opp_id`, `opp_name`, `account`, `owner`, `segment`, `dir_team` | the opportunity |
| `type`, `close_date`, `amount`, `stage` | deal metadata |
| `booking_arr_usd`, `non_commissionable` | scope facts (kept for reference; scope already filters on them) |
| `mari_on_team` | `True`/`False` — Mari holds the AE Overlay team role |
| `mari_on_split` | `True`/`False` — Mari owns an Overlay split |
| `other_overlay` | `;`-separated non-Mari users holding the AE Overlay role (governance exceptions) |
| `period_label`, `period_start`, `period_end` | the month being reviewed |

File name must be **`Japan_Overlay_AE_Automation.csv`** (matched against the app's
`EXPECTED_FILE`; it loads into the `japan_overlay` table). Header casing/spacing
doesn't matter — headers are normalized on upload. Re-uploading **replaces** the
table — a refresh is just dropping the new file.

## Config (environment variables)

| Var | Needed | Notes |
|---|---|---|
| Postgres connection | yes | **App Foundry injects this** when the app includes a DB. Auto-detected: tries `DATABASE_URL`, `POSTGRES_URL`, etc., else assembles from `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`/`PGPORT`. `postgres://` is rewritten to `postgresql://`. |
| `OVERLAY_PERSON` | optional | The permitted AE Overlay's name. Defaults to `Mari Takiyama`. |
| `SF_LIGHTNING_URL` | optional | Base for the "open in Salesforce" links. Defaults to `https://zendesk.lightning.force.com`. |
| `PORT` | optional | Defaults to 8080. |
| `SF_CLIENT_ID`, `SF_CLIENT_SECRET` | **for auto-write** | From the Connected App (see below). Setting *both* turns on the write buttons. |
| `SF_LOGIN_URL` | optional | The org's My Domain login host. Defaults to `https://zendesk.my.salesforce.com`. |
| `SF_REDIRECT_URI` | optional | OAuth callback. Leave unset to auto-derive `<app-url>/oauth/callback` from the request. |
| `FLASK_SECRET_KEY` | recommended w/ auto-write | Signs the session cookie holding each user's token. Set any long random string so logins survive restarts. |

> Uploads are open to anyone with the link by design.

## Auto-write (optional) — "Update in Salesforce" that actually writes

**How it works:** the person who clicks **Update** logs in with **their own**
Salesforce account (the usual Okta SSO), and the app writes **as them** — adding
Mari to the Opportunity Team (`AE Overlay`, Read access) and a 100% `Overlay`
split, skipping whatever already exists. No shared password, no integration user;
every write is attributed to the real clicker by Salesforce. This is the same
logic proven by the local `serve_local.py`, just using web OAuth instead of the
`sf` CLI token (a cloud server has no `sf` session, so it must use OAuth).

**It stays OFF until `SF_CLIENT_ID` + `SF_CLIENT_SECRET` are set.** With them
unset the buttons are plain deep links and nothing can write — safe to deploy now.

### One-time admin step (needs "Customize Application" / a Salesforce admin)

Ask a Salesforce admin to create a **Connected App** (Setup → App Manager → New
Connected App, or External Client App):

1. **Enable OAuth Settings** ✔
2. **Callback URL:** `https://<your-app-foundry-url>/oauth/callback`
   (exact match; add both the deployed URL and any custom domain).
3. **OAuth Scopes:** *Manage user data via APIs (`api`)* and *Perform requests
   at any time (`refresh_token`)*.
4. Save. Copy the **Consumer Key** → `SF_CLIENT_ID` and **Consumer Secret** →
   `SF_CLIENT_SECRET`.
5. (Typical) leave "Require Secret for Web Server Flow" on; set the app's
   permitted users / IP relaxation per Zendesk policy.

Then in App Foundry set `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `FLASK_SECRET_KEY`
(and `SF_REDIRECT_URI` only if the auto-derived URL isn't reachable), and
redeploy. The write buttons light up for everyone — each writing as themselves.

**Verify:** open `/api/health` — `writeConfigured: true` means it's live.

> ⚠️ **"Update all" writes to production for every opp in the current view.**
> Filter first (segment/type/search) to scope a smaller batch, and eyeball it,
> before running the whole list. The single-opp button shows a preview + confirm
> for each write.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL="sqlite:////tmp/japan.sqlite" .venv/bin/python app.py
# open http://localhost:8080/admin  → drop Japan_Overlay_AE_Automation.csv → http://localhost:8080/
```

(SQLite works locally; App Foundry gives you Postgres in production — the app is
DB-agnostic via SQLAlchemy.)

## Deploy on App Foundry

1. Template: **Python Web App → Flask**.
2. Paste **`app.py`** as the main code and `requirements.txt` as the deps.
3. Include the **PostgreSQL database**.
4. Start command (if asked): `gunicorn app:app` (the platform sets `$PORT`).
5. Deploy → open the app URL `/admin` → drop `Japan_Overlay_AE_Automation.csv` → share the base URL.

## Refresh workflow

1. The local refresh runs **daily at 6 AM Manila** (launchd) and writes the CSV
   to the GTM Ops Drive folder — or run `python3 ../refresh.py --org prod` by hand.
2. Download `Japan_Overlay_AE_Automation.csv` from Drive.
3. Drop it on this app's `/admin` page → the worklist updates for everyone.

The **local** viewer (the repo root, `../`) reads the same pull directly;
this App Foundry flavor is the shareable, no-login version.
