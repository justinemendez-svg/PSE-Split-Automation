#!/usr/bin/env python3
"""Local PSE dashboard with confirmed, audited team and split updates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


def current_session() -> tuple[str, str]:
    with SESSION_LOCK:
        if not SESSION["token"] or not SESSION["instance"]:
            raise RuntimeError("Salesforce CLI session is unavailable. Restart the server after logging in with sf.")
        return SESSION["token"], SESSION["instance"]


def refresh_session() -> tuple[str, str]:
    token, instance = sf_session(SESSION["org"])
    with SESSION_LOCK:
        SESSION["token"], SESSION["instance"] = token, instance
    return token, instance


def sf_rest(method: str, path: str, body: dict | None = None):
    token, instance = current_session()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(instance + path, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(raw)
        except json.JSONDecodeError:
            details = {"message": raw[:500]}
        if error.code == 401:
            token, instance = refresh_session()
            request = urllib.request.Request(instance + path, data=data, method=method)
            request.add_header("Authorization", "Bearer " + token)
            request.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else {})
        if isinstance(details, list):
            message = "; ".join(item.get("message", "") for item in details)
        else:
            message = details.get("message", str(details))
        return error.code, {"message": message, "details": details}


def query_opp_ids(opp_ids: list[str]) -> dict[str, dict]:
    token, instance = current_session()
    ids = ",".join("'" + opp_id + "'" for opp_id in opp_ids)
    soql = (
        "SELECT Id, Name, Type, Non_Commissionable__c, Partner_Sales_Rep__c, Partner_Sales_Rep__r.Name, "
        "Partner_Sales_Rep__r.IsActive FROM Opportunity "
        f"WHERE Id IN ({ids})"
    )
    rows = sf_query(soql, token, instance)
    return {row["Id"]: row for row in rows if row.get("Id")}


def is_in_scope_opportunity(opportunity: dict) -> bool:
    return (opportunity.get("Non_Commissionable__c") is False
            and opportunity.get("Type") in OPPORTUNITY_TYPES)


def query_team(opp_ids: list[str]) -> dict[str, set[str]]:
    token, instance = current_session()
    rows = sf_query(build_team_soql(opp_ids), token, instance)
    members: dict[str, set[str]] = {}
    for row in rows:
        if row.get("OpportunityId") and row.get("UserId"):
            members.setdefault(row["OpportunityId"], set()).add(row["UserId"])
    return members


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


def query_splits(opp_ids: list[str]) -> dict[str, set[str]]:
    token, instance = current_session()
    rows = sf_query(build_split_soql(opp_ids, current_split_type_id()), token, instance, "Overlay splits")
    owners: dict[str, set[str]] = {}
    for row in rows:
        if row.get("OpportunityId") and row.get("SplitOwnerId"):
            owners.setdefault(row["OpportunityId"], set()).add(row["SplitOwnerId"])
    return owners


def valid_ids(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("oppIds must be a list")
    ids = list(dict.fromkeys(str(item) for item in value if item))
    if len(ids) > 50_000:
        raise ValueError("A maximum of 50,000 Opps may be updated in one request")
    if any(not ID_PATTERN.fullmatch(opp_id) for opp_id in ids):
        raise ValueError("Invalid Opportunity ID in request")
    return ids


def allowed_missing_ids() -> set[str]:
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Refresh the dashboard data before updating Opps.") from error
    if (data.get("config") or {}).get("scopeVersion") != SCOPE_VERSION:
        raise RuntimeError("Refresh the dashboard data to load the commissionable Renewal, New Business, and Expansion scope.")
    return {
        row.get("id") for row in data.get("rows", [])
        if row.get("id") and row.get("partnerRepActive") is True
        and row.get("nonCommissionable") is False
        and row.get("type") in OPPORTUNITY_TYPES
        and (not row.get("partnerRepOnTeam") or not row.get("partnerRepOnSplit"))
    }


def preview_missing_ids(opp_ids: list[str]) -> dict:
    """Recheck a requested worklist against current Salesforce state without writing."""
    allowed = allowed_missing_ids()
    requested = [opp_id for opp_id in opp_ids if opp_id in allowed]
    excluded = len(opp_ids) - len(requested)
    ids_to_add: list[str] = []
    states: dict[str, dict] = {}
    already_complete = inactive = changed = 0
    chunk_size = 200

    for start in range(0, len(requested), chunk_size):
        batch = requested[start:start + chunk_size]
        opportunities = query_opp_ids(batch)
        team = query_team(batch)
        splits = query_splits(batch)
        for opp_id in batch:
            opportunity = opportunities.get(opp_id)
            if not opportunity or not is_in_scope_opportunity(opportunity) or not opportunity.get("Partner_Sales_Rep__c"):
                changed += 1
                continue
            rep = opportunity.get("Partner_Sales_Rep__r") or {}
            if rep.get("IsActive") is not True:
                inactive += 1
                continue
            rep_id = opportunity["Partner_Sales_Rep__c"]
            on_team = rep_id in team.get(opp_id, set())
            on_split = rep_id in splits.get(opp_id, set())
            states[opp_id] = {"partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split}
            if on_team and on_split:
                already_complete += 1
                continue
            ids_to_add.append(opp_id)

    update_local_snapshot(states)
    return {"idsToAdd": ids_to_add, "alreadyComplete": already_complete, "inactive": inactive,
            "changed": changed, "excluded": excluded, "states": states}


def add_records(object_type: str, records: list[dict]) -> list[dict]:
    if not records:
        return []
    status, result = sf_rest("POST", f"/services/data/{API_VERSION}/composite/sobjects", {
        "allOrNone": False,
        "records": [{"attributes": {"type": object_type}, **record} for record in records],
    })
    if status not in (200, 201):
        raise RuntimeError(result.get("message") or f"Salesforce returned HTTP {status}")
    if not isinstance(result, list) or len(result) != len(records):
        raise RuntimeError("Salesforce returned an incomplete composite update response")
    return result


def update_local_snapshot(states: dict[str, dict]) -> None:
    if not states:
        return
    with SNAPSHOT_LOCK:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            state = states.get(row.get("id"))
            if state:
                row["partnerRepOnTeam"] = state.get("partnerRepOnTeam", row.get("partnerRepOnTeam", False))
                row["partnerRepOnSplit"] = state.get("partnerRepOnSplit", row.get("partnerRepOnSplit", False))
                complete = row["partnerRepOnTeam"] and row["partnerRepOnSplit"]
                row["bucket"] = "complete" if complete else "needs_call"
                missing = []
                if not row["partnerRepOnTeam"]:
                    missing.append("Opportunity Team")
                if not row["partnerRepOnSplit"]:
                    missing.append(f"{SPLIT_TYPE_LABEL} split")
                row["reason"] = f"PSE is on the Opportunity Team and {SPLIT_TYPE_LABEL} split" if complete else "Active partner rep is missing from " + " and ".join(missing)
        active_rows = [row for row in data.get("rows", [])
                       if row.get("partnerRepActive") is True
                       and row.get("nonCommissionable") is False
                       and row.get("type") in OPPORTUNITY_TYPES]
        data["summary"] = summarize(active_rows)
        temp = DATA.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, DATA)


def audit(event: dict) -> None:
    event["at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    event["org"] = SESSION["org"] or "default"
    with open(AUDIT_LOG, "a", encoding="utf-8") as output:
        output.write(json.dumps(event, ensure_ascii=False) + "\n")


def recheck_and_write(opp_ids: list[str]) -> dict:
    allowed = allowed_missing_ids()
    requested = [opp_id for opp_id in opp_ids if opp_id in allowed]
    excluded = len(opp_ids) - len(requested)
    succeeded_ids: set[str] = set()
    states: dict[str, dict] = {}
    team_added = split_added = already_complete = inactive = changed = 0
    failed_ids: set[str] = set()
    errors: list[dict] = []
    chunk_size = 200

    for start in range(0, len(requested), chunk_size):
        batch = requested[start:start + chunk_size]
        try:
            opportunities = query_opp_ids(batch)
            team = query_team(batch)
            splits = query_splits(batch)
        except (RuntimeError, OSError, TimeoutError) as error:
            failed_ids.update(batch)
            errors.append({"message": str(error)[:500]})
            continue
        team_records, team_record_ids = [], []
        split_records, split_record_ids = [], []
        for opp_id in batch:
            opportunity = opportunities.get(opp_id)
            if not opportunity or not is_in_scope_opportunity(opportunity) or not opportunity.get("Partner_Sales_Rep__c"):
                changed += 1
                continue
            rep = opportunity.get("Partner_Sales_Rep__r") or {}
            if rep.get("IsActive") is not True:
                inactive += 1
                continue
            rep_id = opportunity["Partner_Sales_Rep__c"]
            on_team = rep_id in team.get(opp_id, set())
            on_split = rep_id in splits.get(opp_id, set())
            if on_team and on_split:
                already_complete += 1
                states[opp_id] = {"partnerRepOnTeam": True, "partnerRepOnSplit": True}
                continue
            if not on_team:
                team_records.append({
                    "OpportunityId": opp_id,
                    "UserId": rep_id,
                    "TeamMemberRole": TEAM_ROLE,
                    "OpportunityAccessLevel": TEAM_ACCESS,
                })
                team_record_ids.append(opp_id)
            if not on_split:
                split_records.append({
                    "OpportunityId": opp_id,
                    "SplitOwnerId": rep_id,
                    "SplitTypeId": current_split_type_id(),
                    "SplitPercentage": SPLIT_PERCENTAGE,
                })
                split_record_ids.append(opp_id)

        operation_states = {
            opp_id: {"partnerRepOnTeam": rep_id in team.get(opp_id, set()),
                     "partnerRepOnSplit": rep_id in splits.get(opp_id, set())}
            for opp_id, record in opportunities.items()
            if is_in_scope_opportunity(record)
            and (rep_id := record.get("Partner_Sales_Rep__c"))
            and (record.get("Partner_Sales_Rep__r") or {}).get("IsActive") is True
        }
        for object_type, records, record_ids, state_key in (
            ("OpportunityTeamMember", team_records, team_record_ids, "partnerRepOnTeam"),
            ("OpportunitySplit", split_records, split_record_ids, "partnerRepOnSplit"),
        ):
            if not records:
                continue
            try:
                results = add_records(object_type, records)
            except RuntimeError as error:
                errors.append({"object": object_type, "message": str(error)[:500]})
                failed_ids.update(record_ids)
                continue
            for opp_id, result in zip(record_ids, results):
                if result.get("success"):
                    operation_states[opp_id][state_key] = True
                    if state_key == "partnerRepOnTeam":
                        team_added += 1
                    else:
                        split_added += 1
                else:
                    failed_ids.add(opp_id)
                    errors.append({"oppId": opp_id, "object": object_type,
                                   "message": "; ".join(str(item) for item in (result.get("errors") or []))[:500]})

        for opp_id, state in operation_states.items():
            states[opp_id] = state
            if state["partnerRepOnTeam"] and state["partnerRepOnSplit"]:
                succeeded_ids.add(opp_id)
            else:
                failed_ids.add(opp_id)

    update_local_snapshot(states)
    audit({"type": "bulk_team_and_split_update", "requested": len(opp_ids), "succeeded": len(succeeded_ids),
           "teamAdded": team_added, "splitAdded": split_added, "alreadyComplete": already_complete,
           "inactive": inactive, "changedOrMissing": changed,
           "excludedFromSnapshot": excluded, "failed": len(failed_ids), "errors": errors[:50]})
    return {"succeeded": len(succeeded_ids), "succeededIds": sorted(succeeded_ids),
            "states": states, "teamAdded": team_added, "splitAdded": split_added,
            "alreadyComplete": already_complete, "inactive": inactive, "changed": changed,
            "excluded": excluded, "failed": len(failed_ids), "errors": errors[:20]}


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
                                        "splitType": SPLIT_TYPE_LABEL, "splitPercentage": SPLIT_PERCENTAGE})
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
        if length > 4_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _refresh(self):
        command = [sys.executable, str(HERE / "refresh.py")]
        if SESSION["org"]:
            command += ["--org", SESSION["org"]]
        result = subprocess.run(command, cwd=HERE, capture_output=True, text=True, timeout=900)
        if result.returncode:
            detail = (result.stderr or result.stdout or "Salesforce refresh failed").strip()
            return self.send_json(502, {"error": detail[-1500:]})
        data = json.loads(DATA.read_text(encoding="utf-8"))
        return self.send_json(200, {"ok": True, "summary": data.get("summary", {}), "log": result.stdout[-1500:]})

    def _preview(self, opp_id: str):
        if not ID_PATTERN.fullmatch(opp_id):
            return self.send_json(400, {"error": "Invalid Opportunity ID"})
        if opp_id not in allowed_missing_ids():
            return self.send_json(409, {"error": "Opportunity is no longer in the PSE update worklist. Refresh the dashboard."})
        opportunities = query_opp_ids([opp_id])
        opportunity = opportunities.get(opp_id)
        if not opportunity:
            return self.send_json(404, {"error": "Opportunity not found or not accessible"})
        if not is_in_scope_opportunity(opportunity):
            return self.send_json(409, {"error": "Opportunity no longer meets the commissionable Renewal, New Business, or Expansion scope"})
        rep = opportunity.get("Partner_Sales_Rep__r") or {}
        rep_id = opportunity.get("Partner_Sales_Rep__c")
        if not rep_id:
            return self.send_json(200, {"eligible": False, "reason": "Partner Sales Rep is no longer assigned"})
        if rep.get("IsActive") is not True:
            return self.send_json(200, {"eligible": False, "reason": "Assigned Partner Sales Rep is inactive"})
        team = query_team([opp_id])
        splits = query_splits([opp_id])
        on_team = rep_id in team.get(opp_id, set())
        on_split = rep_id in splits.get(opp_id, set())
        already_complete = on_team and on_split
        update_local_snapshot({opp_id: {"partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split}})
        return self.send_json(200, {"eligible": not already_complete, "alreadyComplete": already_complete,
                                    "oppId": opp_id, "oppName": opportunity.get("Name"),
                                    "partnerRepId": rep_id, "partnerRepName": rep.get("Name"),
                                    "partnerRepOnTeam": on_team, "partnerRepOnSplit": on_split,
                                    "teamRole": TEAM_ROLE, "access": TEAM_ACCESS,
                                    "splitType": SPLIT_TYPE_LABEL, "splitPercentage": SPLIT_PERCENTAGE})

    def _write_one(self, payload: dict):
        if payload.get("confirm") != "CONFIRM":
            return self.send_json(400, {"error": "confirmation required"})
        opp_id = str(payload.get("oppId") or "")
        try:
            if opp_id not in allowed_missing_ids():
                return self.send_json(409, {"error": "Opportunity is no longer in the active PSE update worklist. Refresh the dashboard."})
            opportunities = query_opp_ids([opp_id])
            opportunity = opportunities.get(opp_id)
            if not opportunity or not is_in_scope_opportunity(opportunity):
                return self.send_json(409, {"error": "Opportunity no longer meets the commissionable Renewal, New Business, or Expansion scope"})
            if not opportunity.get("Partner_Sales_Rep__c"):
                return self.send_json(409, {"error": "Partner Sales Rep is no longer assigned"})
            rep = opportunity.get("Partner_Sales_Rep__r") or {}
            if rep.get("IsActive") is not True:
                return self.send_json(409, {"error": "Partner Sales Rep is no longer active"})
            rep_id = opportunity["Partner_Sales_Rep__c"]
            team = query_team([opp_id])
            splits = query_splits([opp_id])
            on_team = rep_id in team.get(opp_id, set())
            on_split = rep_id in splits.get(opp_id, set())
            if on_team and on_split:
                update_local_snapshot({opp_id: {"partnerRepOnTeam": True, "partnerRepOnSplit": True}})
                return self.send_json(200, {"done": True, "alreadyComplete": True, "oppId": opp_id,
                                            "partnerRepOnTeam": True, "partnerRepOnSplit": True})

            results = []
            if not on_team:
                status, result = sf_rest("POST", f"/services/data/{API_VERSION}/sobjects/OpportunityTeamMember/", {
                    "OpportunityId": opp_id,
                    "UserId": rep_id,
                    "TeamMemberRole": TEAM_ROLE,
                    "OpportunityAccessLevel": TEAM_ACCESS,
                })
                success = status in (200, 201) and result.get("success", True)
                results.append({"object": "OpportunityTeamMember", "success": success,
                                "error": result.get("message") or str(result.get("details") or result) if not success else None})
            if not on_split:
                split_type_id = current_split_type_id()
                status, result = sf_rest("POST", f"/services/data/{API_VERSION}/sobjects/OpportunitySplit/", {
                    "OpportunityId": opp_id,
                    "SplitOwnerId": rep_id,
                    "SplitTypeId": split_type_id,
                    "SplitPercentage": SPLIT_PERCENTAGE,
                })
                success = status in (200, 201) and result.get("success", True)
                results.append({"object": "OpportunitySplit", "success": success,
                                "error": result.get("message") or str(result.get("details") or result) if not success else None})

            team_after = query_team([opp_id])
            splits_after = query_splits([opp_id])
            on_team_after = rep_id in team_after.get(opp_id, set())
            on_split_after = rep_id in splits_after.get(opp_id, set())
            update_local_snapshot({opp_id: {"partnerRepOnTeam": on_team_after, "partnerRepOnSplit": on_split_after}})
            done = on_team_after and on_split_after
            errors = [result["error"] for result in results if not result["success"]]
            audit({"type": "single_team_and_split_update", "oppId": opp_id, "partnerRepId": rep_id,
                   "partnerRepName": rep.get("Name"), "success": done,
                   "partnerRepOnTeam": on_team_after, "partnerRepOnSplit": on_split_after,
                   "results": results, "teamRole": TEAM_ROLE, "access": TEAM_ACCESS,
                   "splitType": SPLIT_TYPE_LABEL, "splitPercentage": SPLIT_PERCENTAGE})
            return self.send_json(200, {"done": done, "oppId": opp_id,
                                        "partnerRepOnTeam": on_team_after,
                                        "partnerRepOnSplit": on_split_after,
                                        "error": "; ".join(errors) if errors else None,
                                        "results": results})
        except (RuntimeError, OSError, json.JSONDecodeError) as error:
            return self.send_json(502, {"error": str(error)})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "Invalid request body"})
        if not isinstance(payload, dict):
            return self.send_json(400, {"error": "Request body must be an object"})
        if path == "/api/refresh":
            try:
                return self._refresh()
            except subprocess.TimeoutExpired:
                return self.send_json(504, {"error": "Salesforce refresh timed out after 15 minutes"})
            except (OSError, json.JSONDecodeError) as error:
                return self.send_json(500, {"error": str(error)})
        if path == "/api/preview":
            try:
                return self._preview(str(payload.get("oppId") or ""))
            except RuntimeError as error:
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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("PSE Split Automation — Salesforce Team & Split Update")
    print(f"Open http://localhost:{args.port}/")
    print(f"Updates add the assigned PSE as {TEAM_ROLE} with {TEAM_ACCESS} access and a {SPLIT_PERCENTAGE}% {SPLIT_TYPE_LABEL} split after confirmation.")
    print("Every write is rechecked against Salesforce and logged locally. Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
