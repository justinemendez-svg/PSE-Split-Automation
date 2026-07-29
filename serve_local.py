#!/usr/bin/env python3
"""
Japan Overlay AE — LOCAL write server (trial).

Serves the same static dashboard in docs/ AND adds a real Salesforce write path
for the "Overlay AE to update" worklist. When the dashboard is opened through
THIS server (http://localhost:8771), the "Update in Salesforce" button becomes
a two-step, safe auto-write:

    click  →  /api/preview   (dry-run: shows exactly what will be created)
           →  you Confirm
           →  /api/write     (creates the records via YOUR sf CLI session)
           →  append-only audit log (writes.log.jsonl)

It authenticates the SAME way refresh.py reads data: your local `sf` CLI token
(Okta SSO). No Connected App, no stored password, no admin. Whoever runs this
server writes as themselves — which is exactly the "no IT help" path.

For each in-scope opp that needs Mari it will, only on confirm, create:
  1. OpportunityTeamMember — Mari, TeamMemberRole 'AE Overlay', access 'Read'
  2. OpportunitySplit      — Mari, SplitTypeId <Overlay>, 100%
…and it SKIPS whichever of the two already exists (idempotent).

Run:
    python3 serve_local.py                 # points at the default sf org
    python3 serve_local.py --org prod      # explicit prod
Then open http://localhost:8771/

READ-ONLY safety: nothing is written until the button is clicked AND confirmed.
The static GitHub Pages copy has no server, so its button stays a deep link.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
AUDIT_LOG = os.path.join(HERE, "writes.log.jsonl")
API_VERSION = "v60.0"

# ---- The overlay rule (same as refresh.py) ----------------------------------
OVERLAY_PERSON = "Mari Takiyama"
OVERLAY_TEAM_ROLE = "AE Overlay"
OVERLAY_TEAM_ACCESS = "Read"          # OpportunityAccessLevel picklist API value
OVERLAY_SPLIT_TYPE_LABEL = "Overlay"  # SplitType.MasterLabel
OVERLAY_SPLIT_PERCENT = 100

# Filled once at startup from the target org (IDs are resolved live, never
# hardcoded).
SESSION = {"org": None, "instance_url": None, "access_token": None,
           "person_id": None, "split_type_id": None}


# ---- Salesforce access via the sf CLI token ---------------------------------
def _sf_json(args):
    """Run an `sf` command that emits --json and return the parsed result."""
    cmd = ["sf", *args, "--json"]
    if SESSION["org"]:
        cmd += ["--target-org", SESSION["org"]]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: `sf` CLI not found (brew install sf).")
    except subprocess.CalledProcessError as e:
        raise RuntimeError((e.stderr or e.stdout or "").strip())
    return json.loads(out.stdout).get("result", {})


def sf_query(soql):
    res = _sf_json(["data", "query", "--query", soql])
    return res.get("records", [])


def sf_rest(method, path, body=None):
    """Call the Salesforce REST API directly with the sf session token.

    Avoids the sf CLI's space-in-value quoting problems and gives us the raw
    error array Salesforce returns on a failed insert.
    """
    url = SESSION["instance_url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + SESSION["access_token"])
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw}


def bootstrap(org):
    """Resolve the token, instance URL, Mari's UserId, and the Overlay
    SplitTypeId — all read live from the target org."""
    SESSION["org"] = org
    disp = _sf_json(["org", "display"])
    SESSION["access_token"] = disp.get("accessToken")
    SESSION["instance_url"] = disp.get("instanceUrl")
    if not SESSION["access_token"] or not SESSION["instance_url"]:
        sys.exit("ERROR: could not read sf session. Run: sf org login web")

    # Mari's active user id in THIS org (an inactive "(OLD)" dupe may exist).
    users = sf_query(
        "SELECT Id, Name, IsActive FROM User "
        f"WHERE Name = '{OVERLAY_PERSON}' AND IsActive = true")
    if not users:
        sys.exit(f"ERROR: no active user named '{OVERLAY_PERSON}' in {org or 'default org'}.")
    if len(users) > 1:
        print(f"  WARNING: {len(users)} active users named '{OVERLAY_PERSON}'; using the first.")
    SESSION["person_id"] = users[0]["Id"]

    # The Overlay SplitTypeId — read it off any existing Overlay split.
    splits = sf_query(
        "SELECT SplitTypeId FROM OpportunitySplit "
        f"WHERE SplitType.MasterLabel = '{OVERLAY_SPLIT_TYPE_LABEL}' LIMIT 1")
    if splits:
        SESSION["split_type_id"] = splits[0]["SplitTypeId"]
    else:
        print("  WARNING: no existing Overlay split found to read SplitTypeId from; "
              "split creation will be skipped until one exists.")

    who = disp.get("username")
    print(f"  Org:         {org or 'default'}  ({SESSION['instance_url']})")
    print(f"  Writing as:  {who}")
    print(f"  {OVERLAY_PERSON}: {SESSION['person_id']}   Overlay SplitTypeId: {SESSION['split_type_id']}")


# ---- What's already there (so we only add what's missing) -------------------
def current_state(opp_id):
    """Return which of the two overlay tags Mari already has on this opp."""
    team = sf_query(
        "SELECT Id, UserId FROM OpportunityTeamMember "
        f"WHERE OpportunityId = '{opp_id}' AND TeamMemberRole = '{OVERLAY_TEAM_ROLE}' "
        f"AND UserId = '{SESSION['person_id']}'")
    split = sf_query(
        "SELECT Id, SplitOwnerId FROM OpportunitySplit "
        f"WHERE OpportunityId = '{opp_id}' "
        f"AND SplitType.MasterLabel = '{OVERLAY_SPLIT_TYPE_LABEL}' "
        f"AND SplitOwnerId = '{SESSION['person_id']}'")
    return {"mariOnTeam": bool(team), "mariOnSplit": bool(split)}


def plan_for(opp_id):
    """Build the list of records that WOULD be created for this opp."""
    have = current_state(opp_id)
    steps = []
    if not have["mariOnTeam"]:
        steps.append({
            "object": "OpportunityTeamMember",
            "label": f"Add {OVERLAY_PERSON} to the Opportunity Team as {OVERLAY_TEAM_ROLE} (Read access)",
            "values": {
                "OpportunityId": opp_id,
                "UserId": SESSION["person_id"],
                "TeamMemberRole": OVERLAY_TEAM_ROLE,
                "OpportunityAccessLevel": OVERLAY_TEAM_ACCESS,
            },
        })
    if not have["mariOnSplit"]:
        if not SESSION["split_type_id"]:
            steps.append({"object": "OpportunitySplit", "blocked":
                          "No Overlay SplitTypeId resolved in this org — cannot create the split."})
        else:
            steps.append({
                "object": "OpportunitySplit",
                "label": f"Add a {OVERLAY_SPLIT_TYPE_LABEL} split for {OVERLAY_PERSON} at {OVERLAY_SPLIT_PERCENT}%",
                "values": {
                    "OpportunityId": opp_id,
                    "SplitOwnerId": SESSION["person_id"],
                    "SplitTypeId": SESSION["split_type_id"],
                    "SplitPercentage": OVERLAY_SPLIT_PERCENT,
                },
            })
    return have, steps


def audit(entry):
    entry["at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    entry["org"] = SESSION["org"] or "default"
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---- HTTP -------------------------------------------------------------------
STATIC = {".html": "text/html", ".js": "application/javascript",
          ".json": "application/json", ".css": "text/css",
          ".command": "text/plain"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- static files + health --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._send(200, {
                "writeEnabled": True,
                "org": SESSION["org"] or "default",
                "instanceUrl": SESSION["instance_url"],
                "person": OVERLAY_PERSON,
                "personId": SESSION["person_id"],
                "splitReady": bool(SESSION["split_type_id"]),
            })
        if path == "/":
            path = "/index.html"
        target = os.path.normpath(os.path.join(DOCS, path.lstrip("/")))
        if not target.startswith(DOCS) or not os.path.isfile(target):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(target)[1]
        with open(target, "rb") as f:
            self._send(200, f.read(), STATIC.get(ext, "application/octet-stream"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            payload = self._body()
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad JSON"})
        opp_id = (payload.get("oppId") or "").strip()
        if not opp_id:
            return self._send(400, {"error": "oppId required"})

        # DRY-RUN preview — no writes.
        if path == "/api/preview":
            try:
                have, steps = plan_for(opp_id)
            except RuntimeError as e:
                return self._send(502, {"error": str(e)})
            return self._send(200, {
                "oppId": opp_id, "have": have, "steps": steps,
                "nothingToDo": len(steps) == 0,
            })

        # REAL write — requires an explicit confirm token.
        if path == "/api/write":
            if payload.get("confirm") != "CONFIRM":
                return self._send(400, {"error": "confirmation required"})
            try:
                have, steps = plan_for(opp_id)
            except RuntimeError as e:
                return self._send(502, {"error": str(e)})
            results = []
            for step in steps:
                if step.get("blocked"):
                    results.append({"object": step["object"], "ok": False,
                                    "error": step["blocked"]})
                    continue
                code, resp = sf_rest(
                    "POST", f"/services/data/{API_VERSION}/sobjects/{step['object']}/",
                    step["values"])
                ok = code in (200, 201) and resp.get("success")
                err = None
                if not ok:
                    err = resp.get("message") or (
                        "; ".join(e.get("message", "") for e in resp) if isinstance(resp, list) else str(resp))
                results.append({"object": step["object"], "ok": ok,
                                "id": resp.get("id") if ok else None, "error": err})
            after = current_state(opp_id)
            audit({"oppId": opp_id, "steps": steps, "results": results, "after": after})
            all_ok = all(r["ok"] for r in results) if results else True
            return self._send(200 if all_ok else 207, {
                "oppId": opp_id, "results": results, "after": after,
                "done": after["mariOnTeam"] and after["mariOnSplit"],
            })

        # BULK write — a list of opp ids, each confirmed as a batch.
        if path == "/api/write-all":
            if payload.get("confirm") != "CONFIRM":
                return self._send(400, {"error": "confirmation required"})
            ids = payload.get("oppIds") or []
            if not isinstance(ids, list) or not ids:
                return self._send(400, {"error": "oppIds (non-empty list) required"})
            batch = []
            for oid in ids:
                oid = str(oid).strip()
                if not oid:
                    continue
                try:
                    have, steps = plan_for(oid)
                except RuntimeError as e:
                    batch.append({"oppId": oid, "error": str(e), "results": []})
                    continue
                results = []
                for step in steps:
                    if step.get("blocked"):
                        results.append({"object": step["object"], "ok": False,
                                        "error": step["blocked"]})
                        continue
                    code, resp = sf_rest(
                        "POST", f"/services/data/{API_VERSION}/sobjects/{step['object']}/",
                        step["values"])
                    ok = code in (200, 201) and resp.get("success")
                    err = None
                    if not ok:
                        err = resp.get("message") or (
                            "; ".join(e.get("message", "") for e in resp) if isinstance(resp, list) else str(resp))
                    results.append({"object": step["object"], "ok": ok,
                                    "id": resp.get("id") if ok else None, "error": err})
                after = current_state(oid)
                audit({"oppId": oid, "bulk": True, "steps": steps,
                       "results": results, "after": after})
                batch.append({"oppId": oid, "results": results, "after": after,
                              "done": after["mariOnTeam"] and after["mariOnSplit"]})
            done = sum(1 for b in batch if b.get("done"))
            return self._send(200, {"count": len(batch), "done": done, "items": batch})

        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", help="sf CLI target org alias/username (default: your default org)")
    ap.add_argument("--port", type=int, default=8771)
    args = ap.parse_args()

    print("Japan Overlay AE — local write server")
    bootstrap(args.org)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\n  Open  →  http://localhost:{args.port}/")
    print(f"  Audit →  {os.path.relpath(AUDIT_LOG, HERE)}")
    print("  (Ctrl-C to stop. Nothing is written until you click Update AND confirm.)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
