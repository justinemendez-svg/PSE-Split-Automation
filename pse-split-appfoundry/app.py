"""PSE Split Automation AppFoundry app.

The AppFoundry deployment keeps the latest Salesforce snapshot in Postgres.
Refresh reads commissionable Renewal, New Business, and Expansion opportunities
with a tagged Partner and resolves the existing PSE or Partner Owner. Salesforce writes remain previewed and
confirmed, matching the local dashboard behavior.
"""

from __future__ import annotations

import datetime as dt
import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, session
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR.parent / "docs"
INDEX_HTML = (ASSET_DIR / "index.html").read_text(encoding="utf-8") if (ASSET_DIR / "index.html").exists() else ""
APP_JS = (ASSET_DIR / "app.js").read_text(encoding="utf-8") if (ASSET_DIR / "app.js").exists() else ""

# Shared workflow source is embedded by build_standalone.py for single-file deployment.
import sys
sys.path.insert(0, str(APP_DIR.parent))
from workflow import CLOSED_STAGE, REP_FIELDS, PARTNER_FIELD, SCOPE_VERSION, execute, in_scope, is_complete, rep_state, summary
WRITE_LOCK = threading.Lock()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax")

API_VERSION = "v60.0"
TEAM_ROLE = "Partner Account Executive"
TEAM_ACCESS = "Read"
SPLIT_LABEL = "Overlay"
SPLIT_PERCENTAGE = 100
OPPORTUNITY_TYPES = ("Renewal", "New Business", "Expansion")

LIGHTNING_URL = os.environ.get("SF_LIGHTNING_URL", "https://zendesk.lightning.force.com").rstrip("/")
SF_LOGIN_URL = os.environ.get("SF_LOGIN_URL", "https://zendesk.my.salesforce.com").rstrip("/")
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")
SF_REDIRECT_URI = os.environ.get("SF_REDIRECT_URI", "").strip()
WRITE_CONFIGURED = bool(SF_CLIENT_ID and SF_CLIENT_SECRET)
OPP_ID_RE = re.compile(r"^006[A-Za-z0-9]{12,15}$")
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


class NeedsLogin(Exception):
    pass


def _database_url() -> str:
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "PG_URL", "DB_URL"):
        value = os.environ.get(key)
        if value:
            return value.replace("postgres://", "postgresql://", 1) if value.startswith("postgres://") else value
    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
    name = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB")
    port = os.environ.get("PGPORT", "5432")
    if host and user and name:
        credential = f"{user}:{password}@" if password else f"{user}@"
        return f"postgresql://{credential}{host}:{port}/{name}"
    raise RuntimeError("AppFoundry Postgres is required. Attach a PostgreSQL database to this app.")


def db():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = create_engine(_database_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)
                with _ENGINE.begin() as cx:
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_snapshot (
                            id SMALLINT PRIMARY KEY CHECK (id = 1),
                            payload TEXT NOT NULL,
                            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_sessions (
                            session_id TEXT PRIMARY KEY,
                            access_token TEXT NOT NULL,
                            refresh_token TEXT,
                            instance_url TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_jobs (
                            job_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            message TEXT NOT NULL,
                            result TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
                    cx.execute(text("""
                        CREATE TABLE IF NOT EXISTS pse_split_audit (
                            id BIGSERIAL PRIMARY KEY,
                            event TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """))
    return _ENGINE


def _session_id() -> str | None:
    return session.get("sf_session_id")


def _token_cipher() -> Fernet:
    secret = app.secret_key.decode() if isinstance(app.secret_key, bytes) else str(app.secret_key)
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _session_record(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    with db().begin() as cx:
        row = cx.execute(text("SELECT access_token, refresh_token, instance_url FROM pse_split_sessions WHERE session_id=:id"), {"id": session_id}).mappings().first()
    if not row:
        return None
    try:
        cipher = _token_cipher()
        saved = dict(row)
        saved["access_token"] = cipher.decrypt(saved["access_token"].encode()).decode()
        if saved.get("refresh_token"):
            saved["refresh_token"] = cipher.decrypt(saved["refresh_token"].encode()).decode()
        return saved
    except InvalidToken:
        return None


def _store_tokens(session_id: str, payload: dict) -> None:
    cipher = _token_cipher()
    with db().begin() as cx:
        cx.execute(text("""
            INSERT INTO pse_split_sessions (session_id, access_token, refresh_token, instance_url)
            VALUES (:id, :access, :refresh, :instance)
            ON CONFLICT (session_id) DO UPDATE SET access_token=EXCLUDED.access_token,
                refresh_token=COALESCE(EXCLUDED.refresh_token, pse_split_sessions.refresh_token),
                instance_url=EXCLUDED.instance_url, updated_at=NOW()
        """), {"id": session_id,
              "access": cipher.encrypt(payload["access_token"].encode()).decode(),
              "refresh": cipher.encrypt(payload["refresh_token"].encode()).decode() if payload.get("refresh_token") else None,
              "instance": payload["instance_url"].rstrip("/")})


def _redirect_uri() -> str:
    if SF_REDIRECT_URI:
        return SF_REDIRECT_URI
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/oauth/callback"


def _token_refresh(session_id: str, saved: dict) -> dict:
    refresh_token = saved.get("refresh_token")
    if not refresh_token:
        raise NeedsLogin()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": SF_CLIENT_ID,
        "client_secret": SF_CLIENT_SECRET, "refresh_token": refresh_token,
    }).encode()
    req = urllib.request.Request(SF_LOGIN_URL + "/services/oauth2/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise NeedsLogin() from error
    result.setdefault("refresh_token", refresh_token)
    _store_tokens(session_id, result)
    return _session_record(session_id)


def sf_request(session_id: str, method: str, path: str, payload: dict | None = None, retry: bool = True, headers: dict | None = None):
    saved = _session_record(session_id)
    if not saved:
        raise NeedsLogin()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(saved["instance_url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + saved["access_token"])
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items(): req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        if error.code == 401 and retry:
            refreshed = _token_refresh(session_id, saved)
            return sf_request(session_id, method, path, payload, retry=False, headers=headers)
        try:
            details = json.loads(raw)
        except json.JSONDecodeError:
            details = {"message": raw[:1000]}
        if isinstance(details, list):
            details = {"message": "; ".join(x.get("message", "") for x in details)}
        return error.code, details


def sf_query(session_id: str, soql: str, label: str = "records") -> list[dict]:
    path = "/services/data/" + API_VERSION + "/query/?q=" + urllib.parse.quote(soql, safe="")
    records = []
    while path:
        status, result = sf_request(session_id, "GET", path)
        if status != 200:
            raise RuntimeError(result.get("message") or f"Salesforce {label} query failed ({status})")
        records.extend(result.get("records", []))
        next_url = result.get("nextRecordsUrl")
        path = next_url if next_url else ""
    return records


def _ids_soql(ids: list[str]) -> str:
    return ",".join("'" + value + "'" for value in ids)


def _team_members(session_id: str, ids: list[str]) -> dict[str, set[str]]:
    rows = sf_query(session_id, "SELECT OpportunityId, UserId FROM OpportunityTeamMember WHERE OpportunityId IN (" + _ids_soql(ids) + ")", "team members") if ids else []
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["OpportunityId"], set()).add(row["UserId"])
    return result


def _split_type_id(session_id: str) -> str:
    rows = sf_query(session_id, "SELECT SplitTypeId FROM OpportunitySplit WHERE SplitType.MasterLabel = 'Overlay' LIMIT 1", "Overlay split type")
    if not rows or not rows[0].get("SplitTypeId"):
        raise RuntimeError("This Salesforce org has no Overlay Opportunity Split type.")
    return rows[0]["SplitTypeId"]


def _split_owners(session_id: str, ids: list[str], split_type_id: str) -> dict[str, set[str]]:
    rows = sf_query(session_id, "SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit WHERE OpportunityId IN (" + _ids_soql(ids) + ")", "splits") if ids else []
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["OpportunityId"], set()).add(row["SplitOwnerId"])
    return result


def _batches(items: list[str], size: int = 500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _summary(rows: list[dict]) -> dict:
    return summary(rows)

def _refresh_data(session_id: str, progress=None) -> dict:
    split_type_id = _split_type_id(session_id)
    types = ", ".join("'" + value + "'" for value in OPPORTUNITY_TYPES)
    soql = (
        "SELECT Id, Name, StageName, CloseDate, Type, Amount, Non_Commissionable__c, "
        "Account.Name, Account.Owner.Name, Account_Owner_Market_Segment__c, Region__c, "
        + REP_FIELDS + f" FROM Opportunity WHERE {PARTNER_FIELD} != null AND Non_Commissionable__c = false "
        f"AND Type IN ({types}) AND StageName = '{CLOSED_STAGE}'"
    )
    records = sf_query(session_id, soql, "eligible opportunities")
    ids = [record["Id"] for record in records if record.get("Id")]
    if progress:
        progress(f"Checking team and split assignments for {len(ids):,} opportunities…")
    team_by_opp: dict[str, set[str]] = {}
    split_by_opp: dict[str, set[str]] = {}
    chunks = list(_batches(ids))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        for batch in chunks:
            futures[pool.submit(_team_members, session_id, batch)] = "team"
            futures[pool.submit(_split_owners, session_id, batch, split_type_id)] = "split"
        total = len(futures)
        for done, future in enumerate(as_completed(futures), start=1):
            target = team_by_opp if futures[future] == "team" else split_by_opp
            for opp_id, owners in future.result().items():
                target.setdefault(opp_id, set()).update(owners)
            if progress and (done % 20 == 0 or done == total):
                progress(f"Checking Salesforce team/split data… {done:,} of {total:,} batches")

    rows = []
    for record in records:
        value = rep_state(record, team_by_opp.get(record["Id"], ()), split_by_opp.get(record["Id"], ()))
        rows.append({
            "id": record.get("Id"), "name": record.get("Name"),
            "account": (record.get("Account") or {}).get("Name"),
            "accountOwner": ((record.get("Account") or {}).get("Owner") or {}).get("Name"),
            "stage": record.get("StageName"), "closeDate": record.get("CloseDate"),
            "type": record.get("Type"), "segment": record.get("Account_Owner_Market_Segment__c"),
            "region": record.get("Region__c"), "amount": record.get("Amount"),
            "nonCommissionable": record.get("Non_Commissionable__c"),
            **value,
        })
    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "config": {"scope": "Commissionable 08 - Closed Renewal, New Business, and Expansion opportunities",
                   "scopeVersion": SCOPE_VERSION, "stage": CLOSED_STAGE, "partnerField": PARTNER_FIELD, "splitCheck": "any", "commissionableOnly": True,
                   "opportunityTypes": list(OPPORTUNITY_TYPES), "splitType": SPLIT_LABEL,
                   "splitTypeId": split_type_id, "splitPercentage": SPLIT_PERCENTAGE},
        "summary": _summary(rows), "rows": rows,
    }
    _save_snapshot(payload)
    return payload


def _save_snapshot(payload: dict) -> None:
    with db().begin() as cx:
        cx.execute(text("""
            INSERT INTO pse_split_snapshot (id, payload, refreshed_at) VALUES (1, :payload, NOW())
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, refreshed_at=NOW()
        """), {"payload": json.dumps(payload, ensure_ascii=False)})


def _load_snapshot() -> dict:
    with db().begin() as cx:
        row = cx.execute(text("SELECT payload FROM pse_split_snapshot WHERE id=1")).first()
    if not row:
        return {"generatedAt": None,
                "config": {"scopeVersion": SCOPE_VERSION, "stage": CLOSED_STAGE, "partnerField": PARTNER_FIELD, "splitCheck": "any", "scope": "Commissionable 08 - Closed Renewal, New Business, and Expansion opportunities"},
                "summary": {"total": 0, "tagged": 0, "needsUpdate": 0}, "rows": []}
    return json.loads(row[0])


def _set_job(job_id: str, status: str, message: str, result: dict | None = None) -> None:
    with db().begin() as cx:
        cx.execute(text("UPDATE pse_split_jobs SET status=:status, message=:message, result=:result, updated_at=NOW() WHERE job_id=:id"),
                   {"id": job_id, "status": status, "message": message,
                    "result": json.dumps(result, ensure_ascii=False) if result is not None else None})


def _run_refresh(job_id: str, session_id: str) -> None:
    try:
        _set_job(job_id, "running", "Reading commissionable Renewal, New Business, and Expansion opportunities…")
        payload = _refresh_data(session_id, lambda message: _set_job(job_id, "running", message))
        _set_job(job_id, "complete", "Dashboard refreshed.", {"summary": payload["summary"], "generatedAt": payload["generatedAt"]})
    except Exception as error:
        message = "Salesforce session expired. Click Refresh data and sign in again." if isinstance(error, NeedsLogin) else str(error)[:1200]
        _set_job(job_id, "failed", message or "Salesforce refresh failed.")


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _require_login():
    session_id = _session_id()
    if not _session_record(session_id):
        return None, (_json_error("Salesforce login is required.", 401)[0], 401)
    return session_id, None


def _eligible_ids() -> set[str]:
    payload = _load_snapshot()
    if (payload.get("config") or {}).get("scopeVersion") != SCOPE_VERSION:
        return set()
    return {row["id"] for row in payload.get("rows", [])
            if row.get("id") and row.get("partnerId") and row.get("stage") == CLOSED_STAGE and row.get("nonCommissionable") is False
            and row.get("type") in OPPORTUNITY_TYPES and not is_complete(row)}


def _validate_ids(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("oppIds must be a list")
    ids = list(dict.fromkeys(str(item) for item in value if item))
    if not ids or len(ids) > 200000 or any(not OPP_ID_RE.fullmatch(value) for value in ids):
        raise ValueError("Invalid Opportunity IDs in request")
    return ids


def _update_snapshot_states(states: dict[str, dict]) -> None:
    if not states:
        return
    payload = _load_snapshot()
    for row in payload.get("rows", []):
        value = states.get(row.get("id"))
        if value: row.update(value)
    payload["summary"] = _summary(payload.get("rows", []))
    _save_snapshot(payload)


def _run_workflow(session_id, ids, write=False):
    with WRITE_LOCK:
        result = execute(ids, _eligible_ids(), lambda soql: sf_query(session_id, soql),
                         lambda method, path, body, **kw: sf_request(session_id, method, path, body, **kw),
                         lambda: _split_type_id(session_id), write=write)
        _update_snapshot_states(result["states"])
        return result


def _write_one(session_id, opp_id):
    result = _run_workflow(session_id, [opp_id], write=True)
    value = result["states"].get(opp_id, {})
    return {**value, "done": bool(value and is_complete(value)), "oppId": opp_id,
            "error": "; ".join(item.get("message", "") for item in result["errors"]), "results": result}


def _preview_many(session_id, ids):
    return _run_workflow(session_id, ids)


def _bulk_write(session_id, ids):
    return _run_workflow(session_id, ids, write=True)


def _audit(event: dict) -> None:
    with db().begin() as cx:
        cx.execute(text("INSERT INTO pse_split_audit (event) VALUES (:event)"), {"event": json.dumps(event, ensure_ascii=False)})


@app.get("/")
def index():
    if not INDEX_HTML:
        return "AppFoundry bundle is missing the embedded dashboard assets.", 500
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/app.js")
def javascript():
    return Response(APP_JS, mimetype="application/javascript")


@app.get("/pse-data.json")
def data_json():
    try:
        return jsonify(_load_snapshot())
    except Exception as error:
        return _json_error(str(error), 500)


@app.get("/api/health")
def health():
    try:
        sid = _session_id()
        record = _session_record(sid)
        return jsonify({"refreshEnabled": WRITE_CONFIGURED, "writeEnabled": WRITE_CONFIGURED,
                        "authMode": "oauth", "loggedIn": bool(record),
                        "org": (record or {}).get("instance_url", "Salesforce")})
    except Exception as error:
        return _json_error(str(error), 500)


@app.get("/oauth/login")
def oauth_login():
    if not WRITE_CONFIGURED:
        return _json_error("Set SF_CLIENT_ID and SF_CLIENT_SECRET in AppFoundry first.", 503)
    state["oauth_state"] = secrets.token_urlsafe(24)
    session["oauth_next"] = request.args.get("next", "/")
    params = {"response_type": "code", "client_id": SF_CLIENT_ID,
              "redirect_uri": _redirect_uri(), "scope": "api refresh_token",
              "state": state["oauth_state"]}
    return redirect(SF_LOGIN_URL + "/services/oauth2/authorize?" + urllib.parse.urlencode(params))


def _oauth_result(error: str | None = None):
    payload = json.dumps({"sfAuth": True, "error": error})
    message = "Salesforce login complete. You can close this window." if not error else "Salesforce login failed. Close this window and retry."
    return Response("<!doctype html><meta charset=utf-8><title>Salesforce login</title>"
                    "<body style='font:16px system-ui;padding:32px'>" + html.escape(message) +
                    "<script>try{window.opener&&window.opener.postMessage(" + payload + ",location.origin)}catch(e){};window.close()</script></body>",
                    mimetype="text/html")


@app.get("/oauth/callback")
def oauth_callback():
    if request.args.get("error"):
        return _oauth_result(request.args.get("error_description") or request.args["error"])
    if request.args.get("state") != session.get("oauth_state"):
        return _oauth_result("Login state mismatch")
    code = request.args.get("code")
    if not code:
        return _oauth_result("No authorization code returned")
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                   "client_id": SF_CLIENT_ID, "client_secret": SF_CLIENT_SECRET,
                                   "redirect_uri": _redirect_uri()}).encode()
    req = urllib.request.Request(SF_LOGIN_URL + "/services/oauth2/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            token = json.loads(resp.read().decode())
        if not token.get("access_token") or not token.get("instance_url"):
            return _oauth_result("Salesforce did not return an access token")
        sid = _session_id() or secrets.token_urlsafe(32)
        _store_tokens(sid, token)
        session["sf_session_id"] = sid
        session.pop("oauth_state", None)
        return _oauth_result()
    except Exception as error:
        return _oauth_result(str(error)[:300])


@app.post("/api/refresh")
def refresh():
    if (request.get_json(silent=True) or {}).get("manual") is not True:
        return _json_error("Click Refresh in the updated dashboard to refresh Salesforce data.", 400)
    sid, error = _require_login()
    if error:
        return error
    job_id = secrets.token_urlsafe(18)
    with db().begin() as cx:
        cx.execute(text("INSERT INTO pse_split_jobs (job_id, session_id, status, message) VALUES (:id, :sid, 'queued', 'Queued Salesforce refresh…')"),
                   {"id": job_id, "sid": sid})
    threading.Thread(target=_run_refresh, args=(job_id, sid), daemon=True).start()
    return jsonify({"jobId": job_id}), 202


@app.get("/api/refresh-status")
def refresh_status():
    sid = _session_id()
    job_id = request.args.get("jobId", "")
    with db().begin() as cx:
        row = cx.execute(text("SELECT status, message, result FROM pse_split_jobs WHERE job_id=:id AND session_id=:sid"),
                         {"id": job_id, "sid": sid or ""}).mappings().first()
    if not row:
        return _json_error("Refresh job not found.", 404)
    return jsonify({"status": row["status"], "message": row["message"],
                    "result": json.loads(row["result"]) if row["result"] else None})


@app.post("/api/preview")
def preview_one():
    sid, error = _require_login()
    if error: return error
    opp_id = str((request.get_json(silent=True) or {}).get("oppId") or "")
    if not OPP_ID_RE.fullmatch(opp_id): return _json_error("Invalid Opportunity ID")
    if opp_id not in _eligible_ids(): return _json_error("Opportunity is no longer in the PSE update worklist. Refresh first.", 409)
    try:
        result = _preview_many(sid, [opp_id])
        value = result["states"].get(opp_id, {})
        return jsonify({**value, "eligible": opp_id in result["idsToAdd"],
                        "alreadyComplete": bool(value and is_complete(value)), "oppId": opp_id,
                        "oppName": next((row.get("name") for row in _load_snapshot().get("rows", []) if row.get("id") == opp_id), opp_id),
                        "reason": value.get("blockedReason") or "Opportunity no longer meets the Partner scope."})
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.post("/api/write")
def write_one():
    sid, error = _require_login()
    if error: return error
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "CONFIRM": return _json_error("Confirmation required.")
    try:
        opp_id = str(body.get("oppId") or "")
        if not OPP_ID_RE.fullmatch(opp_id): return _json_error("Invalid Opportunity ID")
        result = _write_one(sid, opp_id)
        _audit({"type": "single_team_and_split_update", "oppId": opp_id, **result})
        return jsonify(result)
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 409)


@app.post("/api/preview-all")
def preview_many():
    sid, error = _require_login()
    if error: return error
    try:
        ids = _validate_ids((request.get_json(silent=True) or {}).get("oppIds"))
        return jsonify(_preview_many(sid, ids))
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.post("/api/write-all")
def write_many():
    sid, error = _require_login()
    if error: return error
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "CONFIRM": return _json_error("Confirmation required.")
    try:
        ids = _validate_ids(body.get("oppIds"))
        result = _bulk_write(sid, ids)
        _audit({"type": "bulk_team_and_split_update", "requested": len(ids), **result})
        return jsonify(result)
    except NeedsLogin:
        return jsonify({"needsLogin": True, "error": "Salesforce login expired."}), 401
    except Exception as exc:
        return _json_error(str(exc), 502)


@app.errorhandler(Exception)
def app_error(error):
    app.logger.exception("AppFoundry request failed")
    return _json_error(str(error), 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
