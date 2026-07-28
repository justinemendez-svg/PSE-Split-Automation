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

This is **read-only**: it detects and reports. No Salesforce writes are made.

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

> Uploads are open to anyone with the link by design.

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
