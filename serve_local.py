#!/usr/bin/env python3
"""Local PSE dashboard with confirmed, audited team and split updates."""

from __future__ import annotations

import argparse
import errno
import datetime as dt
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workflow import CLOSED_STAGE, EXCLUDED_PARTNER_TYPE, is_complete
from local_scope import LOCAL_SCOPE_VERSION, unique_opportunities, local_summary, in_fiscal_window, scope_snapshot
from local_updates import execute_local, read_current, decorate, VALID_ROLES, COVERAGE_ROLE, retain_inactive_review

from refresh import (
    API_VERSION, SPLIT_PERCENTAGE,
    SPLIT_TYPE_LABEL,
    resolve_split_type_id, sf_query, sf_session,
)

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
DATA = DOCS / "pse-data.json"
AUDIT_LOG = HERE / "writes.log.jsonl"
TEAM_ROLE = "Partner Account Executive"
TEAM_ACCESS = "Read"
LIGHTNING_URL = "https://zendesk.lightning.force.com"
ID_PATTERN = re.compile(r"^006[A-Za-z0-9]{12,15}$")
SESSION = {"org": None, "token": None, "instance": None, "splitTypeId": None}
SESSION_LOCK = threading.Lock()
SNAPSHOT_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
WRITE_LOCK = threading.Lock()


def current_session() -> tuple[str, str]:
    with SESSION_LOCK:
        if not SESSION["token"] or not SESSION["instance"]:
            raise RuntimeError("Salesforce CLI session is unavailable. Restart the server after logging in with sf.")
        return SESSION["token"], SESSION["instance"]


def refresh_session() -> tuple[str, str]:
    try:
        token, instance = sf_session(SESSION["org"])
    except SystemExit as error:
        raise RuntimeError("Salesforce login is unavailable. Sign in with Salesforce CLI and retry.") from error
    with SESSION_LOCK:
        SESSION["token"], SESSION["instance"] = token, instance
    return token, instance


def sf_rest(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(2):
        token, instance = current_session()
        request = urllib.request.Request(instance + path, data=data, method=method,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            if error.code == 401 and attempt == 0:
                refresh_session()
                continue
            try:
                details = json.loads(raw)
            except json.JSONDecodeError:
                details = {"message": raw[:500]}
            message = "; ".join(str(item.get("message", "")) for item in details) if isinstance(details, list) else str(details.get("message", details))
            return error.code, {"message": message, "details": details}


def query_current(soql):
    token, instance = current_session()
    try:
        return sf_query(soql, token, instance)
    except RuntimeError as error:
        if "(401)" not in str(error) and "INVALID_SESSION_ID" not in str(error):
            raise
        token, instance = refresh_session()
        return sf_query(soql, token, instance)


def current_split_type_id() -> str:
    with SESSION_LOCK:
        split_type_id = SESSION.get("splitTypeId")
    if split_type_id:
        return split_type_id
    token, instance = current_session()
    split_type_id = resolve_split_type_id(token, instance)
    with SESSION_LOCK:
        SESSION["splitTypeId"] = split_type_id
    return split_type_id


def valid_ids(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("oppIds must be a list")
    ids = list(dict.fromkeys(str(item) for item in value if item))
    if len(ids) > 200_000:
        raise ValueError("A maximum of 200,000 Opps may be updated in one request")
    if any(not ID_PATTERN.fullmatch(opp_id) for opp_id in ids):
        raise ValueError("Invalid Opportunity ID in request")
    return ids


def allowed_missing_ids() -> set[str]:
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        data['rows']=unique_opportunities(data.get('rows',[]))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Refresh the dashboard data before updating Opps.") from error
    if (data.get("config") or {}).get("scopeVersion") != LOCAL_SCOPE_VERSION:
        raise RuntimeError("Refresh the dashboard data to load the commissionable 08 - Closed Opportunity scope.")
    return {row["id"] for row in data.get("rows", [])
            if row.get("id") and row.get("partnerId") and row.get("stage") == CLOSED_STAGE and row.get("nonCommissionable") is False
            and 'partnerType' in row and row.get('partnerType') != EXCLUDED_PARTNER_TYPE
            and row.get('partnerName') != 'AWS Marketplace'
            and in_fiscal_window(row.get('closeDate'))
            and (row.get('updatePlan',{}).get('status') != 'complete' if row.get('updatePlan') else not is_complete(row))}


def run_workflow(opp_ids, write=False):
    # All write routes share the same lock and execution path.
    with WRITE_LOCK, REFRESH_LOCK:
        refresh_session()
        saved=json.loads(DATA.read_text())
        approvals={row['id']:row['assignmentReview'] for row in saved.get('rows',[]) if row.get('assignmentReview')}
        result = execute_local(opp_ids, allowed_missing_ids(), query_current,
                               sf_rest, current_split_type_id, write=write, approvals=approvals)
        update_local_snapshot(result["states"])
        if write:
            audit({"type": "partner_pse_team_split_update", "requested": len(opp_ids),
                   **{key: value for key, value in result.items() if key != "states"}})
        return result


def preview_missing_ids(opp_ids):
    return run_workflow(opp_ids)


def update_local_snapshot(states: dict[str, dict]) -> None:
    if not states:
        return
    with SNAPSHOT_LOCK:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        data['rows']=unique_opportunities(data.get('rows',[]))
        for row in data.get("rows", []):
            state = states.get(row.get("id"))
            if state:
                row.update(state)
        data["summary"] = local_summary(data.get("rows", []))
        temp = DATA.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, DATA)


def audit(event: dict) -> None:
    event["at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    event["org"] = SESSION["org"] or "default"
    with open(AUDIT_LOG, "a", encoding="utf-8") as output:
        output.write(json.dumps(event, ensure_ascii=False) + "\n")


def recheck_and_write(opp_ids: list[str]) -> dict:
    return run_workflow(opp_ids, write=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "PSESplitAutomation/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_body(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def send_json(self, status: int, payload: dict):
        self.send_body(status, json.dumps(payload).encode("utf-8"))

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"refreshEnabled": True, "writeEnabled": True,
                                        "org": SESSION["org"] or "default",
                                        "teamRole": TEAM_ROLE, "access": TEAM_ACCESS,
                                        "splitType": SPLIT_TYPE_LABEL, "splitPercentage": SPLIT_PERCENTAGE, "scopeVersion": LOCAL_SCOPE_VERSION})
        if path == "/":
            path = "/index.html"
        if path == "/pse-data.json" and DATA.is_file():
            try:
                return self.send_json(200, scope_snapshot(json.loads(DATA.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError) as error:
                return self.send_json(500, {"error": "Saved snapshot could not be read: " + str(error)})
        target = (DOCS / path.lstrip("/")).resolve()
        try:
            target.relative_to(DOCS.resolve())
        except ValueError:
            return self.send_json(404, {"error": "not found"})
        if not target.is_file():
            return self.send_json(404, {"error": "not found"})
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        return self.send_body(200, target.read_bytes(), content_type)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 4_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _refresh(self):
        command = [sys.executable, str(HERE / "refresh.py")]
        if SESSION["org"]:
            command += ["--org", SESSION["org"]]
        with REFRESH_LOCK:
            result = subprocess.run(command, cwd=HERE, capture_output=True, text=True, timeout=900)
            if result.returncode:
                detail = (result.stderr or result.stdout or "Salesforce refresh failed").strip()
                return self.send_json(502, {"error": detail[-1500:]})
            data = json.loads(DATA.read_text(encoding="utf-8"))
            return self.send_json(200, {"ok": True, "summary": data.get("summary", {})})

    def _preview(self, opp_id: str):
        if not ID_PATTERN.fullmatch(opp_id):
            return self.send_json(400, {"error": "Invalid Opportunity ID"})
        if opp_id not in allowed_missing_ids():
            return self.send_json(409, {"error": "Opportunity is no longer in the PSE update worklist. Refresh the dashboard."})
        result = preview_missing_ids([opp_id])
        value = result["states"].get(opp_id, {})
        return self.send_json(200, {**value, "eligible": opp_id in result["idsToAdd"],
            "alreadyComplete": value.get('updatePlan',{}).get('status')=='complete', "oppId": opp_id,
            "oppName": next((row.get("name") for row in json.loads(DATA.read_text()).get("rows", []) if row.get("id") == opp_id), opp_id),
            "reason": value.get('updatePlan',{}).get('reason') or value.get("blockedReason") or "Opportunity no longer meets the Partner scope."})

    def _write_one(self, payload: dict):
        if payload.get("confirm") != "CONFIRM":
            return self.send_json(400, {"error": "confirmation required"})
        try:
            ids = valid_ids([payload.get("oppId")])
            if not ids: raise ValueError("Opportunity ID is required")
            result = recheck_and_write(ids)
            value = result["states"].get(ids[0], {})
            return self.send_json(200, {**value, "done": value.get('updatePlan',{}).get('status')=='complete' if value.get('updatePlan') else bool(value and is_complete(value)),
                "oppId": ids[0], "results": result,
                "error": "; ".join(item.get("message", "") for item in result["errors"]) or
                         ("Opportunity changed or is no longer in the worklist." if not value else None)})
        except (ValueError, RuntimeError, OSError) as error:
            return self.send_json(502, {"error": str(error)})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._body()
        except (ValueError, UnicodeError):
            return self.send_json(400, {"error": "Invalid request body"})
        if not isinstance(payload, dict):
            return self.send_json(400, {"error": "Request body must be an object"})
        if path == '/api/users':
            try:
                term=str(payload.get('search') or '').strip()
                if len(term)<2:return self.send_json(400,{'error':'Enter at least two characters.'})
                term=term[:80].replace('\\','\\\\').replace("'","\\'")
                refresh_session()
                users=query_current("SELECT Id, Name, IsActive FROM User WHERE IsActive = true AND Name LIKE '%"+term+"%' ORDER BY Name LIMIT 30")
                return self.send_json(200,{'users':[{'id':user['Id'],'name':user['Name']} for user in users]})
            except (RuntimeError,OSError,ValueError) as error:return self.send_json(502,{'error':str(error)})
        if path == '/api/review-inactive-pse':
            try:
                ids=valid_ids([payload.get('oppId')])
                if not ids or payload.get('confirmed') is not True:
                    raise ValueError('Confirm that this Opportunity must use its identified inactive PSE.')
                with WRITE_LOCK,REFRESH_LOCK:
                    refresh_session()
                    if ids[0] not in allowed_missing_ids():
                        raise ValueError('Opportunity is outside the current worklist.')
                    _,states=read_current(ids,query_current)
                    row=states[ids[0]]
                    review=retain_inactive_review(row)
                    decorate(row,review)
                    update_local_snapshot({ids[0]:row})
                    audit({'type':'local_retain_inactive_pse_review','oppId':ids[0],'review':review})
                return self.send_json(200,row)
            except ValueError as error:return self.send_json(400,{'error':str(error)})
            except (RuntimeError,OSError) as error:return self.send_json(502,{'error':str(error)})
        if path == '/api/review':
            try:
                ids=valid_ids([payload.get('oppId')]);user_id=str(payload.get('userId') or '')
                role=payload.get('role')
                if not ids or not re.fullmatch(r'005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?',user_id) or role not in VALID_ROLES or payload.get('confirmed') is not True:
                    raise ValueError('Select an active PSE, choose the role and confirm the assignment.')
                if role==COVERAGE_ROLE and payload.get('coverageConfirmed') is not True:
                    raise ValueError('Tick coverage box before confirming a Coverage role assignment.')
                with WRITE_LOCK,REFRESH_LOCK:
                    refresh_session()
                    if ids[0] not in allowed_missing_ids():raise ValueError('Opportunity is outside the current worklist.')
                    _,states=read_current(ids,query_current,keep_all_memberships=True)
                    row=states[ids[0]]
                    if row.get('inScope') is False:raise ValueError('Opportunity no longer meets the current scope.')
                    requires_rvp=row.get('partnerRepActive') is False
                    if requires_rvp and (payload.get('rvpConfirmed') is not True or role!=COVERAGE_ROLE):
                        raise ValueError('RVP confirmation and Partner Account Executive - Coverage are required.')
                    if (row.get('partnerRepNeedsPopulate') or user_id==row.get('partnerOwnerId')) and not requires_rvp and role!=TEAM_ROLE:
                        raise ValueError('Matching PSE or blank-PSE assignments require Partner Account Executive.')
                    users=query_current("SELECT Id, Name, IsActive FROM User WHERE Id = '"+user_id+"' AND IsActive = true")
                    if len(users)!=1:raise ValueError('The selected PSE is not an accessible active Salesforce user.')
                    approval={'userId':user_id,'name':users[0]['Name'],'active':True,'role':role,'confirmed':True,
                              'baseRepId':row.get('partnerRepId'),'baseOwnerId':row.get('partnerOwnerId'),
                              'requiresRvp':requires_rvp,'rvpConfirmed':payload.get('rvpConfirmed') is True,
                              'coverageConfirmed':payload.get('coverageConfirmed') is True}
                    decorate(row,approval);update_local_snapshot({ids[0]:row})
                    audit({'type':'local_assignment_review','oppId':ids[0],'review':approval})
                    return self.send_json(200,row)
            except ValueError as error:return self.send_json(400,{'error':str(error)})
            except (RuntimeError,OSError) as error:return self.send_json(502,{'error':str(error)})
        if path == "/api/refresh":
            if payload.get("manual") is not True:
                return self.send_json(400, {"error": "Click Refresh in the updated dashboard to refresh Salesforce data."})
            try:
                return self._refresh()
            except subprocess.TimeoutExpired:
                return self.send_json(504, {"error": "Salesforce refresh timed out after 15 minutes"})
            except (OSError, json.JSONDecodeError) as error:
                return self.send_json(500, {"error": str(error)})
        if path == "/api/preview":
            try:
                return self._preview(str(payload.get("oppId") or ""))
            except (RuntimeError, OSError, ValueError) as error:
                return self.send_json(502, {"error": str(error)})
        if path == "/api/preview-all":
            try:
                ids = valid_ids(payload.get("oppIds"))
                if not ids:
                    return self.send_json(400, {"error": "oppIds must not be empty"})
                return self.send_json(200, preview_missing_ids(ids))
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                return self.send_json(502, {"error": str(error)})
        if path == "/api/write":
            return self._write_one(payload)
        if path == "/api/write-all":
            if payload.get("confirm") != "CONFIRM":
                return self.send_json(400, {"error": "confirmation required"})
            try:
                ids = valid_ids(payload.get("oppIds"))
                if not ids:
                    return self.send_json(400, {"error": "oppIds must not be empty"})
                result = recheck_and_write(ids)
                return self.send_json(200, result)
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                return self.send_json(502, {"error": str(error)})
        return self.send_json(404, {"error": "not found"})


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def create_servers(port):
    """Always bind IPv4; allow hosts where IPv6 loopback is unavailable."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        ipv6_server = IPv6ThreadingHTTPServer(("::1", port), Handler)
    except OSError as error:
        unavailable = {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL, errno.ENOPROTOOPT}
        if error.errno not in unavailable:
            server.server_close()
            raise
        ipv6_server = None
    return server, ipv6_server


def main():
    parser = argparse.ArgumentParser(description="Serve PSE Split Automation locally.")
    parser.add_argument("--org", help="Salesforce CLI org alias or username")
    parser.add_argument("--port", type=int, default=8773)
    args = parser.parse_args()
    SESSION["org"] = args.org
    try:
        token, instance = sf_session(args.org)
    except SystemExit as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    SESSION["token"], SESSION["instance"] = token, instance
    try:
        server, ipv6_server = create_servers(args.port)
    except OSError as error:
        sys.exit(f"Could not start the dashboard on port {args.port}: {error}. Stop the conflicting server or choose another --port.")
    servers = [server] + ([ipv6_server] if ipv6_server else [])
    if ipv6_server:
        threading.Thread(target=ipv6_server.serve_forever, daemon=True).start()
    print("PSE Split Automation — Salesforce Team & Split Update")
    print(f"Open http://localhost:{args.port}/")
    print("Listening on loopback only. If localhost does not resolve, open " + f"http://127.0.0.1:{args.port}/")
    print(f"Confirmed updates preserve the PSE or fill a blank PSE from the Partner Owner, then add missing Team and Split entries for {CLOSED_STAGE} Opportunities.")
    print("Every write is rechecked against Salesforce and logged locally. Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if ipv6_server:
            ipv6_server.shutdown()
        for active_server in servers:
            active_server.server_close()


if __name__ == "__main__":
    main()
