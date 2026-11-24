#!/usr/bin/env python3
"""Local PSE dashboard with confirmed, audited team and split updates."""

from __future__ import annotations

import argparse
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

from workflow import CLOSED_STAGE, REP_FIELDS, PARTNER_FIELD, execute, in_scope, is_complete, summary

from refresh import (
    API_VERSION, OPPORTUNITY_TYPES, SCOPE_VERSION, SPLIT_PERCENTAGE,
    SPLIT_TYPE_LABEL, build_split_soql, build_team_soql,
    resolve_split_type_id, sf_query, sf_session, summarize,
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
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Refresh the dashboard data before updating Opps.") from error
    if (data.get("config") or {}).get("scopeVersion") != SCOPE_VERSION:
        raise RuntimeError("Refresh the dashboard data to load the commissionable 08 - Closed Opportunity scope.")
    return {row["id"] for row in data.get("rows", [])
            if row.get("id") and row.get("partnerId") and row.get("stage") == CLOSED_STAGE and row.get("nonCommissionable") is False
            and row.get("type") in OPPORTUNITY_TYPES and not is_complete(row)}


def run_workflow(opp_ids, write=False):
    # All write routes share the same lock and execution path.
    with WRITE_LOCK, REFRESH_LOCK:
        refresh_session()
        result = execute(opp_ids, allowed_missing_ids(), query_current,
                         sf_rest, current_split_type_id, write=write)
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
        for row in data.get("rows", []):
            state = states.get(row.get("id"))
            if state:
                row.update(state)
        data["summary"] = summary(data.get("rows", []))
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
                                        "splitType": SPLIT_TYPE_LABEL, "splitPercentage": SPLIT_PERCENTAGE, "scopeVersion": SCOPE_VERSION})
        if path == "/":
            path = "/index.html"
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
            "alreadyComplete": bool(value and is_complete(value)), "oppId": opp_id,
            "oppName": next((row.get("name") for row in json.loads(DATA.read_text()).get("rows", []) if row.get("id") == opp_id), opp_id),
            "reason": value.get("blockedReason") or "Opportunity no longer meets the Partner scope."})

    def _write_one(self, payload: dict):
        if payload.get("confirm") != "CONFIRM":
            return self.send_json(400, {"error": "confirmation required"})
        try:
            ids = valid_ids([payload.get("oppId")])
            if not ids: raise ValueError("Opportunity ID is required")
            result = recheck_and_write(ids)
            value = result["states"].get(ids[0], {})
            return self.send_json(200, {**value, "done": bool(value and is_complete(value)),
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
    servers = []
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        servers.append(server)
        ipv6_server = IPv6ThreadingHTTPServer(("::1", args.port), Handler)
        servers.append(ipv6_server)
    except OSError:
        for active_server in servers:
            active_server.server_close()
        raise
    threading.Thread(target=ipv6_server.serve_forever, daemon=True).start()
    print("PSE Split Automation — Salesforce Team & Split Update")
    print(f"Open http://localhost:{args.port}/")
    print("Listening on IPv4 and IPv6 loopback only.")
    print(f"Confirmed updates preserve the PSE or fill a blank PSE from the Partner Owner, then add missing Team and Split entries for {CLOSED_STAGE} Opportunities.")
    print("Every write is rechecked against Salesforce and logged locally. Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ipv6_server.shutdown()
        for active_server in servers:
            active_server.server_close()


if __name__ == "__main__":
    main()
