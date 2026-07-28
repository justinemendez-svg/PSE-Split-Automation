"""Japan Overlay AE Review — App Foundry Flask app.

Serves the same worklist as the local GitHub Pages version, but reads its data
from the app's Postgres database instead of a baked-in data.json. Data is loaded
by dragging the refresh script's CSV (Japan_Overlay_AE_Automation.csv) onto the
/admin page.

Flow:
  1. The local refresh (refresh.py) pulls last month's Closed/Won Japan
     Commercial+Enterprise opps (any type) from Salesforce and writes the CSV —
     one row per opp, carrying the raw facts (mari_on_team, mari_on_split,
     other_overlay), NOT the verdict.
  2. Drag that CSV onto this app's /admin page → it lands in Postgres.
  3. "/" rebuilds the worklist from the raw rows, re-deriving the
     OK / Needs Mari / Someone-else classification (the same logic as
     refresh.analyze) so the view always reflects the current rule — viewers
     need no login and never upload.

This is READ-ONLY: it detects and reports. No Salesforce writes are made.

Single uploadable file → table:
  Japan_Overlay_AE_Automation.csv → japan_overlay   (one row per opportunity)
"""

from __future__ import annotations

import html as html_module
import io
import os
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request
from sqlalchemy import create_engine, inspect, text

app = Flask(__name__)

TABLE = "japan_overlay"
# The file dropped on /admin (the automated pull names it this). It maps to the
# TABLE above — we match on this exact name rather than the table name so the
# Postgres identifier stays clean (no dashes) regardless of the file's name.
EXPECTED_FILE = "Japan_Overlay_AE_Automation.csv"
# The sole permitted AE Overlay. An opp is OK only when this person is tagged in
# BOTH the Opportunity Team (role "AE Overlay") and an Overlay split. Matches
# refresh.OVERLAY_PERSON.
OVERLAY_PERSON = os.environ.get("OVERLAY_PERSON", "Mari Takiyama")
# Base for the "open in Salesforce" opportunity links. Override with the
# SF_LIGHTNING_URL env var if the org's My Domain differs.
SF_LIGHTNING_URL = os.environ.get(
    "SF_LIGHTNING_URL", "https://zendesk.lightning.force.com"
).rstrip("/")


@app.errorhandler(Exception)
def _show_error(e):
    """Render the actual error text (not a blank 500) so DB/config problems are
    diagnosable in the browser."""
    import traceback
    msg = html_module.escape(str(e))
    detail = html_module.escape(traceback.format_exc())
    return (f"<!doctype html><meta charset=utf-8><title>Japan Overlay — Error</title>"
            f"<body style='font-family:-apple-system,sans-serif;max-width:820px;"
            f"margin:50px auto;color:#1b2733'>"
            f"<h1 style='color:#cf222e'>Something went wrong</h1>"
            f"<p style='font-size:15px'><b>{msg}</b></p>"
            f"<details><summary>Technical detail</summary>"
            f"<pre style='background:#f4f6f9;padding:14px;border-radius:8px;"
            f"overflow:auto;font-size:12px'>{detail}</pre></details>"
            f"<p><a href='/admin'>Go to upload page →</a></p></body>"), 500


# ---- Postgres connection (platform-injected) --------------------------------

_DB_URL_VARS = [
    "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "PG_URL",
    "DB_URL", "POSTGRES_CONNECTION_STRING", "DATABASE_CONNECTION_STRING",
]


def _resolve_db_url() -> str:
    """Find the Postgres URL however the platform exposes it: a full connection
    string env var, else assembled from PG*/POSTGRES_* parts. "" if none found."""
    for var in _DB_URL_VARS:
        v = os.environ.get(var)
        if v:
            return v

    def pick(*names, default=""):
        for n in names:
            if os.environ.get(n):
                return os.environ[n]
        return default

    host = pick("PGHOST", "POSTGRES_HOST", "DB_HOST")
    user = pick("PGUSER", "POSTGRES_USER", "DB_USER")
    pwd = pick("PGPASSWORD", "POSTGRES_PASSWORD", "DB_PASSWORD")
    name = pick("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DB_NAME")
    port = pick("PGPORT", "POSTGRES_PORT", "DB_PORT", default="5432")
    if host and user and name:
        cred = f"{user}:{pwd}@" if pwd else f"{user}@"
        return f"postgresql://{cred}{host}:{port}/{name}"
    return ""


def get_engine():
    url = _resolve_db_url()
    if not url:
        seen = sorted(
            k for k in os.environ
            if any(t in k.upper() for t in ("DATABASE", "POSTGRES", "PG", "DB"))
        )
        raise RuntimeError(
            "Could not find a Postgres connection. Checked "
            f"{_DB_URL_VARS} and PG*/POSTGRES_* parts. "
            f"DB-related env vars present: {seen or 'none'}."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return create_engine(url, pool_pre_ping=True)


# Map the uploaded CSV's column headers to the canonical names build_payload
# reads. Keyed by a normalized header (lowercased, only a-z0-9), so it accepts
# either the human-readable report names ("Opportunity ID", "Mari On Team") or
# the snake_case names refresh.py writes ("opp_id", "mari_on_team").
_COLUMN_ALIASES = {
    "oppid": "opp_id", "opportunityid": "opp_id", "opp_id": "opp_id",
    "oppname": "opp_name", "opportunity": "opp_name", "opportunityname": "opp_name",
    "opp_name": "opp_name",
    "account": "account", "accountname": "account",
    "owner": "owner", "ownername": "owner", "opportunityowner": "owner",
    "segment": "segment", "marketsegment": "segment",
    "accountownermarketsegment": "segment",
    "dirteam": "dir_team", "dir_team": "dir_team", "directorteam": "dir_team",
    "type": "type", "opptype": "type", "opportunitytype": "type",
    "closedate": "close_date", "close_date": "close_date",
    "amount": "amount",
    "stage": "stage", "stagename": "stage",
    "marionteam": "mari_on_team", "mari_on_team": "mari_on_team",
    "overlayonteam": "mari_on_team", "onteam": "mari_on_team",
    "marionsplit": "mari_on_split", "mari_on_split": "mari_on_split",
    "overlayonsplit": "mari_on_split", "onsplit": "mari_on_split",
    "otheroverlay": "other_overlay", "other_overlay": "other_overlay",
    "otheroverlays": "other_overlay",
    "periodlabel": "period_label", "period_label": "period_label",
    "periodstart": "period_start", "period_start": "period_start",
    "periodend": "period_end", "period_end": "period_end",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename CSV columns to the canonical names, matching on a normalized key
    so header casing/spacing/punctuation ('Mari On Team', 'mari_on_team') don't
    matter."""
    rename = {}
    for col in df.columns:
        key = "".join(ch for ch in str(col).lower() if ch.isalnum())
        if key in _COLUMN_ALIASES:
            rename[col] = _COLUMN_ALIASES[key]
    return df.rename(columns=rename)


def load_table(eng) -> pd.DataFrame | None:
    if TABLE not in set(inspect(eng).get_table_names()):
        return None
    # Normalize on read too, so data uploaded before the header-mapping fix
    # (stored with raw report headers) still renders without a re-upload.
    return _normalize_columns(pd.read_sql(text(f'SELECT * FROM "{TABLE}"'), eng))


def _ph_now_stamp() -> str:
    """Current time in Philippine time (Asia/Manila), shown as the upload stamp."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    ph = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Manila"))
    return ph.strftime("%-d %b %Y, %-I:%M %p MNL")


def _record_upload(eng):
    """Stamp the latest upload time (PH time) so the dashboard can show it. Keeps
    a serial id so the latest row is identifiable on Postgres (no implicit rowid)."""
    stamp = _ph_now_stamp()
    with eng.begin() as cx:
        cx.execute(text(
            "CREATE TABLE IF NOT EXISTS _upload_log (id SERIAL PRIMARY KEY, uploaded_at TEXT)"
        ))
        cx.execute(text("INSERT INTO _upload_log (uploaded_at) VALUES (:ts)"), {"ts": stamp})


def _last_upload_stamp(eng) -> str:
    # Order by the serial id (Postgres has no implicit rowid); fall back to a
    # plain unordered read for legacy tables created without the id column.
    for sql in ("SELECT uploaded_at FROM _upload_log ORDER BY id DESC LIMIT 1",
                "SELECT uploaded_at FROM _upload_log"):
        try:
            df = pd.read_sql(text(sql), eng)
            return str(df["uploaded_at"].iloc[-1]) if not df.empty else ""
        except Exception:
            continue
    return ""


# ---- analysis (mirror of refresh.analyze) -----------------------------------

def _truthy(v) -> bool:
    return str(v).strip().lower() in {"true", "t", "1", "yes", "y"}


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str(v):
    """NaN/NaT/None → None, else a stripped string. Keeps NaN out of the JSON
    payload (a literal NaN token is invalid JSON and breaks the browser parse)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def _fmt_period(iso: str) -> str:
    """'2026-06-30' → '30 Jun 2026'; raw string if unparseable."""
    from datetime import datetime
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%-d %b %Y")
    except (ValueError, TypeError):
        return iso


def _classify(mari_on_team: bool, mari_on_split: bool, other_overlay: list) -> tuple:
    """Same rule as refresh.analyze:
      someone_else — a non-Mari user holds the AE Overlay team role.
      ok           — Mari is on the team (AE Overlay) AND on an Overlay split.
      missing      — otherwise; reason says which place(s) need Mari.
    """
    if other_overlay:
        return "someone_else", "AE Overlay held by: " + ", ".join(other_overlay)
    if mari_on_team and mari_on_split:
        return "ok", f"{OVERLAY_PERSON} in team + split"
    missing = []
    if not mari_on_team:
        missing.append("team role")
    if not mari_on_split:
        missing.append("Overlay split")
    return "missing", f"Needs {OVERLAY_PERSON}: " + " & ".join(missing)


def build_payload(df: pd.DataFrame) -> dict:
    """One row per opportunity with the both-places verdict re-derived from the
    raw booleans in the CSV (the same rule as refresh.analyze)."""
    period_label = period_start = period_end = ""
    rows = []
    counts = {"ok": 0, "missing": 0, "someone_else": 0}
    total_amt = 0.0
    for _, r in df.iterrows():
        if not period_label and pd.notna(r.get("period_label")):
            period_label = str(r.get("period_label"))
            period_start = str(r.get("period_start"))[:10] if pd.notna(r.get("period_start")) else ""
            period_end = str(r.get("period_end"))[:10] if pd.notna(r.get("period_end")) else ""

        other_raw = _str(r.get("other_overlay")) or ""
        other_overlay = [n.strip() for n in other_raw.split(";") if n.strip()]
        mari_on_team = _truthy(r.get("mari_on_team"))
        mari_on_split = _truthy(r.get("mari_on_split"))
        bucket, reason = _classify(mari_on_team, mari_on_split, other_overlay)
        counts[bucket] += 1
        amount = _num(r.get("amount"))
        total_amt += amount or 0

        rows.append({
            "id": _str(r.get("opp_id")),
            "name": _str(r.get("opp_name")),
            "account": _str(r.get("account")),
            "owner": _str(r.get("owner")),
            "segment": _str(r.get("segment")),
            "dirTeam": _str(r.get("dir_team")),
            "type": _str(r.get("type")),
            "closeDate": (str(r.get("close_date"))[:10] if pd.notna(r.get("close_date")) else None),
            "amount": amount,
            "stage": _str(r.get("stage")),
            "mariOnTeam": mari_on_team,
            "mariOnSplit": mari_on_split,
            "otherOverlay": other_overlay,
            "bucket": bucket,
            "reason": reason,
        })

    # If the CSV carried no explicit period columns, derive the review window from
    # the data itself: earliest → latest close date across all opps.
    close_dates = sorted(d for d in (o["closeDate"] for o in rows) if d)
    if close_dates:
        period_start = period_start or close_dates[0]
        period_end = period_end or close_dates[-1]
        if not period_label:
            period_label = (f"{_fmt_period(close_dates[0])} – {_fmt_period(close_dates[-1])}"
                            if close_dates[0] != close_dates[-1]
                            else _fmt_period(close_dates[0]))

    return {
        "period": {"start": period_start, "end": period_end, "label": period_label},
        "config": {"overlayPerson": OVERLAY_PERSON},
        "summary": {
            "total": len(rows),
            "ok": counts["ok"],
            "missing": counts["missing"],
            "someoneElse": counts["someone_else"],
            "totalAmount": total_amt,
        },
        "sfBaseUrl": SF_LIGHTNING_URL,
        "rows": rows,
    }


# ---- routes -----------------------------------------------------------------

@app.route("/data.json")
def data_json():
    empty = {"period": {"label": ""}, "uploadedAt": "",
             "config": {"overlayPerson": OVERLAY_PERSON},
             "summary": {"total": 0, "ok": 0, "missing": 0, "someoneElse": 0, "totalAmount": 0},
             "rows": []}
    # If the DB isn't connected yet, serve an empty payload (not a 500) so the
    # viewer shows its clean "no data — go to /admin" state instead of breaking.
    try:
        eng = get_engine()
        df = load_table(eng)
    except Exception:  # noqa: BLE001
        return jsonify(empty)
    if df is None or df.empty:
        return jsonify(empty)
    # Past this point the table exists with rows, so a failure is a real bug in
    # payload-building — surface it (500 with detail) instead of masking it as an
    # empty "no data" state, which sends the analyst to re-upload data that's fine.
    payload = build_payload(df)
    payload["uploadedAt"] = _last_upload_stamp(eng)
    return jsonify(payload)


@app.route("/")
def index():
    return VIEWER_HTML


@app.route("/debug")
def debug():
    """Quick diagnostic: what's actually in the DB right now. Visit /debug to see
    the raw column headers, normalized headers, row count, and a sample row."""
    info = {"table": TABLE, "expected_file": EXPECTED_FILE}
    try:
        eng = get_engine()
        info["db_connected"] = True
    except Exception as e:  # noqa: BLE001
        return jsonify({**info, "db_connected": False, "error": str(e)})
    try:
        if TABLE not in set(inspect(eng).get_table_names()):
            return jsonify({**info, "table_exists": False,
                            "note": "No data uploaded yet — drop the CSV on /admin."})
        raw = pd.read_sql(text(f'SELECT * FROM "{TABLE}"'), eng)
        norm = _normalize_columns(raw)
        info.update({
            "table_exists": True,
            "row_count": int(len(raw)),
            "raw_columns": list(map(str, raw.columns)),
            "normalized_columns": list(map(str, norm.columns)),
            "sample_row": {k: (None if pd.isna(v) else str(v))
                           for k, v in raw.iloc[0].items()} if len(raw) else {},
            "last_upload": _last_upload_stamp(eng),
        })
        return jsonify(info)
    except Exception as e:  # noqa: BLE001
        return jsonify({**info, "error": str(e)})


@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Reach the DB up front, but never let a connection problem 500 the whole
    # import page — show it with a clear banner so the page is still usable and
    # the cause is visible (common on the very first deploy before the DB binds).
    msgs = []
    try:
        eng = get_engine()
        eng.connect().close()
    except Exception as e:  # noqa: BLE001
        msgs.append(("err", f"Database not connected yet — uploads are disabled "
                            f"until it is. Detail: {e}"))
        return _admin_html("— database not connected", msgs)

    if request.method == "POST":
        for f in request.files.getlist("files"):
            if not f.filename:
                continue
            if not f.filename.lower().endswith(".csv"):
                msgs.append(("err", f"{f.filename}: not a .csv — skipped."))
                continue
            if Path(f.filename).name.lower() != EXPECTED_FILE.lower():
                msgs.append(("err", f"{f.filename}: unexpected file name. "
                                    f"Expected '{EXPECTED_FILE}'."))
                continue
            try:
                # Auto-detect the delimiter (comma / tab / semicolon / pipe) so
                # tab-separated exports load correctly too. sep=None + the python
                # engine sniffs it; fall back to a plain comma read if that fails.
                data = f.read()
                try:
                    df = pd.read_csv(io.BytesIO(data), sep=None, engine="python")
                except Exception:  # noqa: BLE001
                    df = pd.read_csv(io.BytesIO(data))
                df = _normalize_columns(df)
                missing = [c for c in ("opp_id", "mari_on_team", "mari_on_split")
                           if c not in df.columns]
                if missing:
                    msgs.append(("err", f"{f.filename}: missing expected column(s) "
                                        f"{', '.join(missing)}. Got headers: "
                                        f"{', '.join(map(str, df.columns))}."))
                    continue
                df.to_sql(TABLE, eng, if_exists="replace", index=False)
                _record_upload(eng)
                msgs.append(("ok", f"{f.filename}: loaded {len(df):,} opportunities."))
            except Exception as e:  # noqa: BLE001
                msgs.append(("err", f"{f.filename}: failed — {e}"))
    return _admin_html(_status(eng), msgs)


def _status(eng) -> str:
    df = load_table(eng)
    if df is None:
        return "— not uploaded yet"
    opps = df["opp_id"].nunique() if "opp_id" in df.columns else len(df)
    return f"{opps:,} opportunities"


def _admin_html(status: str, msgs: list) -> str:
    flash = "".join(
        f"<p class='msg {c}'>{html_module.escape(m)}</p>" for c, m in msgs
    )
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Japan Overlay — Upload</title>
<style>
 :root{{
   --bg:#f7f8fa; --panel:#ffffff; --line:#e3e7eb;
   --pink:#78A300; --pink-soft:#17494D; --txt:#1b2733; --muted:#68737d;
   --ok:#3f8c00; --err:#d92d20;
 }}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--txt);
   font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}}
 .wrap{{max-width:720px;margin:48px auto;padding:0 22px}}
 .bar{{height:4px;border-radius:99px;background:linear-gradient(90deg,var(--pink),#17494D);margin-bottom:26px}}
 h1{{font-size:21px;font-weight:680;letter-spacing:.2px;margin:0 0 6px}}
 .sub{{color:var(--muted);font-size:13px;margin:0 0 26px}}
 .card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px}}
 .drop{{border:2px dashed var(--pink);border-radius:12px;padding:34px;text-align:center;
   background:rgba(120,163,0,.06);transition:background .15s}}
 .drop:hover{{background:rgba(120,163,0,.12)}}
 .drop .big{{font-size:15px;font-weight:600;color:var(--pink-soft)}}
 .drop small{{color:var(--muted)}}
 input[type=file]{{color:var(--txt);margin:12px 0 4px;font:inherit}}
 input[type=file]::file-selector-button{{background:rgba(120,163,0,.15);color:var(--pink-soft);
   border:1px solid var(--pink);border-radius:8px;padding:7px 13px;font:inherit;font-weight:600;cursor:pointer;margin-right:10px}}
 button{{font:inherit;font-weight:680;cursor:pointer;border:none;border-radius:9px;padding:11px 20px;
   color:#0d1211;background:var(--pink);margin-top:18px;transition:transform .08s,box-shadow .15s,background .15s}}
 button:hover{{background:var(--pink-soft);box-shadow:0 4px 18px rgba(120,163,0,.35)}}
 button:active{{transform:translateY(1px)}}
 .status{{margin-top:20px;padding:13px 16px;border-radius:10px;background:#f3f5f7;border:1px solid var(--line);color:var(--muted)}}
 .status b{{color:var(--txt)}}
 .msg{{padding:11px 14px;border-radius:9px;margin-bottom:14px;font-weight:600}}
 .msg.ok{{color:var(--ok);background:rgba(63,140,0,.08);border:1px solid rgba(63,140,0,.25)}}
 .msg.err{{color:var(--err);background:rgba(217,45,32,.08);border:1px solid rgba(217,45,32,.25)}}
 code{{background:#eef1f3;color:var(--pink-soft);padding:2px 7px;border-radius:5px;font-size:12.5px}}
 a{{color:var(--pink-soft);text-decoration:none}} a:hover{{text-decoration:underline}}
 .foot{{margin-top:24px;font-size:12px;color:var(--muted)}}
</style></head>
<body><div class="wrap">
<div class="bar"></div>
<h1>Japan Overlay AE Review — Data Upload</h1>
<p class="sub">Drag <code>{html_module.escape(EXPECTED_FILE)}</code> (produced by <code>refresh.py</code>) onto the box.
Re-uploading <b>replaces</b> the data. Anyone with the link can upload here.</p>
<div class="card">
{flash}
<form method=post enctype=multipart/form-data>
  <div class="drop">
    <div class="big">Drop {html_module.escape(EXPECTED_FILE)} here</div>
    <input type=file name=files accept=.csv multiple>
    <div><small>File name must be <code>{html_module.escape(EXPECTED_FILE)}</code> · you can drop multiple at once</small></div>
  </div>
  <button type=submit>Upload</button>
</form>
<div class="status"><b>Currently loaded:</b> {html_module.escape(status)}</div>
</div>
<div class="foot"><a href="/">← View dashboard</a></div>
</div></body></html>"""


# ---- the viewer (same UI as the GitHub Pages version, data from /data.json) -

VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Japan Overlay AE Review</title>
<style>
  :root {
    --bg: #f7f8fa; --panel: #ffffff; --panel2: #f3f5f7; --line: #e3e7eb;
    --txt: #1b2733; --muted: #68737d; --accent: #78A300; --accent2: #17494D;
    --warn: #b26a00; --bad: #d92d20; --ok: #3f8c00; --chip: #eef1f3;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--txt);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 16px 24px; background: var(--panel); border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 10; }
  header h1 { font-size: 17px; margin: 0; font-weight: 650; letter-spacing: .2px; }
  header .sub { color: var(--muted); font-size: 12px; }
  .spacer { flex: 1; }
  button { font: inherit; font-weight: 600; cursor: pointer; border: none; border-radius: 8px;
    padding: 9px 16px; color: #fff; background: var(--accent2); transition: background .15s, opacity .15s; }
  button:hover { background: var(--accent); }
  button.ghost { background: transparent; border: 1px solid var(--line); color: var(--txt); }
  button.ghost:hover { background: var(--panel2); }
  main { padding: 24px; max-width: 1320px; margin: 0 auto; }
  .banner { background: rgba(34,197,94,.08); border: 1px solid rgba(34,197,94,.3); color: var(--ok);
    border-radius: 10px; padding: 10px 14px; margin-bottom: 18px; font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 22px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
  .card.click { cursor: pointer; }
  .card.click:hover { border-color: var(--accent); }
  .card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .card .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }
  .card .v { font-size: 28px; font-weight: 700; margin-top: 6px; }
  .card.ok .v { color: var(--ok); }
  .card.warn .v { color: var(--warn); }
  .card.bad .v { color: var(--bad); }
  .toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
  .toolbar input[type=search] { background: var(--panel2); border: 1px solid var(--line); color: var(--txt);
    border-radius: 8px; padding: 8px 12px; min-width: 260px; font: inherit; }
  .toolbar select { background: var(--panel2); border: 1px solid var(--line); color: var(--txt);
    border-radius: 8px; padding: 8px 12px; font: inherit; cursor: pointer; }
  table { width: 100%; table-layout: fixed; border-collapse: collapse; background: var(--panel); border-radius: 12px; overflow: hidden; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: break-word; word-break: break-word; }
  col.c-opp { width: 22%; } col.c-acct { width: 13%; } col.c-type { width: 9%; }
  col.c-close { width: 9%; } col.c-amount { width: 9%; } col.c-team { width: 8%; }
  col.c-split { width: 8%; } col.c-status { width: 22%; }
  th { background: var(--panel2); color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .4px; cursor: pointer; }
  th:hover { color: var(--txt); }
  tr:last-child td { border-bottom: none; }
  tr.b-missing td:first-child { box-shadow: inset 3px 0 0 var(--warn); }
  tr.b-someone_else td:first-child { box-shadow: inset 3px 0 0 var(--bad); }
  tr.b-ok td:first-child { box-shadow: inset 3px 0 0 var(--ok); }
  .opp-link { font-weight: 600; }
  .opp-link:hover { text-decoration: underline; }
  .opp-id { color: var(--muted); font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin-top: 3px; }
  .meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .chip { display: inline-block; background: var(--chip); border-radius: 999px; padding: 2px 9px; font-size: 11px; font-weight: 600; }
  .chip.ok { background: rgba(34,197,94,.15); color: var(--ok); }
  .chip.warn { background: rgba(245,158,11,.15); color: var(--warn); }
  .chip.bad { background: rgba(239,68,68,.15); color: var(--bad); }
  .yn { font-weight: 700; }
  .yn.y { color: var(--ok); } .yn.n { color: var(--warn); }
  .status-line { margin-top: 5px; }
  .empty, .status { color: var(--muted); padding: 40px; text-align: center; }
  .status.err { color: var(--bad); }
  a { color: var(--accent2); text-decoration: none; }
  a:hover { text-decoration: underline; }
  footer { color: var(--muted); font-size: 12px; padding: 20px 24px; text-align: center; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Japan Overlay AE Review</h1>
    <div class="sub">Is <b id="hPerson">Mari Takiyama</b> tagged as AE Overlay in BOTH the Opportunity Team and the Overlay split? · Japan · Commercial + Enterprise · any type</div>
  </div>
  <div class="spacer"></div>
  <span id="who" class="sub"></span>
  <button class="ghost" id="csvBtn">Export CSV</button>
</header>

<main>
  <div id="status" class="status">Loading…</div>

  <div id="dashboard" class="hidden">
    <div class="banner">
      <b>Read-only.</b> This worklist detects &amp; reports only — no Salesforce writes. Data is from the last monthly CSV upload.
    </div>

    <div class="cards">
      <div class="card click active" data-filter="" id="cardTotal"><div class="k">In-scope opps</div><div class="v" id="cTotal">–</div></div>
      <div class="card ok click" data-filter="ok"><div class="k">OK — Mari in both</div><div class="v" id="cOk">–</div></div>
      <div class="card warn click" data-filter="missing"><div class="k">Needs Mari</div><div class="v" id="cMissing">–</div></div>
      <div class="card bad click" data-filter="someone_else"><div class="k">Someone else tagged</div><div class="v" id="cSomeone">–</div></div>
      <div class="card"><div class="k">Period</div><div class="v" id="cPeriod" style="font-size:18px">–</div></div>
    </div>

    <div class="toolbar">
      <input type="search" id="search" placeholder="Search opp, account, owner, ID…" />
      <select id="typeFilter" title="Filter by deal type"><option value="">All types</option></select>
      <span id="filterNote" class="sub"></span>
      <div class="spacer"></div>
    </div>

    <table id="tbl">
      <colgroup>
        <col class="c-opp" /><col class="c-acct" /><col class="c-type" />
        <col class="c-close" /><col class="c-amount" /><col class="c-team" />
        <col class="c-split" /><col class="c-status" />
      </colgroup>
      <thead>
        <tr>
          <th data-sort="name">Opportunity</th>
          <th data-sort="account">Account</th>
          <th data-sort="type">Type</th>
          <th data-sort="closeDate">Close Date</th>
          <th data-sort="amount">Amount</th>
          <th data-sort="mariOnTeam">Team role</th>
          <th data-sort="mariOnSplit">Overlay split</th>
          <th data-sort="bucket">Status</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
    <div id="emptyRows" class="empty hidden">No opportunities match the current filter.</div>
  </div>
</main>

<footer>
  Read-only monthly worklist · Dir Team = Japan · Commercial + Enterprise · Closed-Won, any type.
  &nbsp;·&nbsp; <a href="/admin">Upload data →</a>
</footer>

<script>
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let STATE = { rows: [], period: null, summary: null, sfBaseUrl: "", overlayPerson: "Mari Takiyama" };
  let sortKey = "closeDate", sortDir = -1;
  let bucketFilter = "", typeFilter = "";

  const fmtMoney = (n) => n == null ? "—" : n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const fmtDate = (s) => (s ? new Date(s + "T00:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function setStatus(msg, isErr) {
    const el = $("status"); el.innerHTML = msg; el.classList.remove("hidden"); el.classList.toggle("err", !!isErr);
  }
  const yn = (v) => v ? '<span class="yn y">Yes</span>' : '<span class="yn n">No</span>';

  function statusCell(r) {
    if (r.bucket === "ok") return '<span class="chip ok">OK</span>';
    if (r.bucket === "someone_else")
      return '<span class="chip bad">Someone else</span><div class="status-line meta">' + esc(r.reason) + "</div>";
    return '<span class="chip warn">Needs Mari</span><div class="status-line meta">' + esc(r.reason) + "</div>";
  }

  function filtered() {
    const q = $("search").value.trim().toLowerCase();
    let rows = STATE.rows.slice();
    if (bucketFilter) rows = rows.filter((r) => r.bucket === bucketFilter);
    if (typeFilter) rows = rows.filter((r) => r.type === typeFilter);
    if (q) rows = rows.filter((r) =>
      [r.name, r.account, r.owner, r.id, r.reason, (r.otherOverlay || []).join(" ")]
        .some((v) => String(v == null ? "" : v).toLowerCase().includes(q)));
    rows.sort((a, b) => {
      let x = a[sortKey], y = b[sortKey];
      if (typeof x === "boolean") { x = x ? 1 : 0; y = y ? 1 : 0; }
      if (sortKey === "amount") { x = Number(x) || 0; y = Number(y) || 0; }
      if (x == null) x = ""; if (y == null) y = "";
      return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
    });
    return rows;
  }

  function render() {
    const rows = filtered();
    $("tbody").innerHTML = rows.map((r) => {
      const link = STATE.sfBaseUrl && r.id
        ? '<a class="opp-link" href="' + esc(STATE.sfBaseUrl) + "/lightning/r/Opportunity/" + esc(r.id) + '/view" target="_blank" rel="noopener">' + esc(r.name) + "</a>"
        : '<span class="opp-link">' + esc(r.name) + "</span>";
      return '<tr class="b-' + r.bucket + '">' +
        "<td>" + link + '<div class="opp-id">' + esc(r.id || "") + "</div>" +
          '<div class="meta">Owner: ' + esc(r.owner || "—") + " · " + esc(r.segment || "—") + "</div></td>" +
        "<td>" + esc(r.account || "—") + "</td>" +
        "<td>" + esc(r.type || "—") + "</td>" +
        "<td>" + fmtDate(r.closeDate) + "</td>" +
        "<td>" + fmtMoney(r.amount) + "</td>" +
        "<td>" + yn(r.mariOnTeam) + "</td>" +
        "<td>" + yn(r.mariOnSplit) + "</td>" +
        "<td>" + statusCell(r) + "</td></tr>";
    }).join("");
    $("emptyRows").classList.toggle("hidden", rows.length > 0);
    $("tbl").classList.toggle("hidden", rows.length === 0);
    $("filterNote").textContent = rows.length + " of " + STATE.rows.length + " opps shown";
  }

  function exportCsv() {
    const rows = filtered();
    const head = ["opp_id", "opp_name", "account", "owner", "segment", "dir_team",
      "type", "close_date", "amount", "mari_on_team", "mari_on_split",
      "other_overlay", "status", "reason"];
    const cell = (v) => { const s = String(v == null ? "" : v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const lines = [head.join(",")];
    for (const r of rows) lines.push([r.id, r.name, r.account, r.owner, r.segment, r.dirTeam,
      r.type, r.closeDate, r.amount, r.mariOnTeam, r.mariOnSplit,
      (r.otherOverlay || []).join("; "), r.bucket, r.reason].map(cell).join(","));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "japan_overlay_ae_review.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function load() {
    try {
      const res = await fetch("data.json?t=" + Date.now());
      if (!res.ok) throw new Error("no data");
      const data = await res.json();
      STATE = {
        rows: data.rows || [], period: data.period, summary: data.summary || {},
        uploadedAt: data.uploadedAt, sfBaseUrl: data.sfBaseUrl || "",
        overlayPerson: (data.config && data.config.overlayPerson) || "Mari Takiyama",
      };
      $("who").textContent = STATE.uploadedAt ? "data uploaded: " + STATE.uploadedAt : "";
      $("hPerson").textContent = STATE.overlayPerson;
      if (!STATE.rows.length) {
        $("dashboard").classList.add("hidden");
        setStatus("<b>No data yet.</b> Drag <code>Japan_Overlay_AE_Automation.csv</code> onto the <a href='/admin'>upload page</a>.", true);
        return;
      }
      const sm = STATE.summary;
      $("cTotal").textContent = sm.total ?? STATE.rows.length;
      $("cOk").textContent = sm.ok ?? 0;
      $("cMissing").textContent = sm.missing ?? 0;
      $("cSomeone").textContent = sm.someoneElse ?? 0;
      $("cPeriod").textContent = STATE.period && STATE.period.label ? STATE.period.label : "—";

      const types = [...new Set(STATE.rows.map((r) => r.type).filter(Boolean))].sort();
      const sel = $("typeFilter");
      for (const t of types) { const o = document.createElement("option"); o.value = t; o.textContent = t; sel.appendChild(o); }

      $("status").classList.add("hidden");
      $("dashboard").classList.remove("hidden");
      render();
    } catch (e) {
      $("dashboard").classList.add("hidden");
      setStatus("<b>Couldn't load data.</b> The data feed errored — check <a href='/debug'>/debug</a>, or drag <code>Japan_Overlay_AE_Automation.csv</code> onto the <a href='/admin'>upload page</a>.", true);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("csvBtn").addEventListener("click", exportCsv);
    $("search").addEventListener("input", render);
    $("typeFilter").addEventListener("change", (e) => { typeFilter = e.target.value; render(); });

    document.querySelectorAll(".card.click").forEach((card) => {
      card.addEventListener("click", () => {
        bucketFilter = card.dataset.filter || "";
        document.querySelectorAll(".card.click").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        render();
      });
    });
    document.querySelectorAll("th[data-sort]").forEach((th) =>
      th.addEventListener("click", () => {
        const k = th.getAttribute("data-sort");
        if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = 1; }
        render();
      }));
    load();
  });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
