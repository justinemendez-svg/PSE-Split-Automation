#!/usr/bin/env python3
"""Refresh the Partner Sales Rep team and Overlay split dashboard.

Reads commissionable 08 - Closed Renewal, New Business, and Expansion Opportunities with
a tagged Partner; resolves the existing PSE or Partner Owner and checks
the PSE field, Opportunity Team and Opportunity Splits. This refresh is read-only.

Run with: python3 refresh.py --org prod
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_OUT = os.path.join(HERE, "docs", "pse-data.json")
API_VERSION = "v60.0"
LIGHTNING_URL = "https://zendesk.lightning.force.com"
SPLIT_TYPE_LABEL = "Overlay"
SPLIT_PERCENTAGE = 100
OPPORTUNITY_TYPES = ("Renewal", "New Business", "Expansion")
from workflow import CLOSED_STAGE, PARTNER_FIELD, PARTNER_REL, REP_FIELDS, SCOPE_VERSION, rep_state, summary


def sf_session(org: str | None) -> tuple[str, str]:
    """Read the user's existing Salesforce CLI session without displaying it."""
    cmd = ["sf", "org", "display", "--json"]
    if org:
        cmd += ["--target-org", org]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: Salesforce CLI not found. Install it with `brew install sf`.")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Salesforce CLI could not read a session.").strip()
        sys.exit(f"ERROR: {detail}")

    try:
        result = json.loads(proc.stdout).get("result", {})
    except json.JSONDecodeError:
        sys.exit("ERROR: Salesforce CLI returned invalid session data.")
    token, instance = result.get("accessToken"), result.get("instanceUrl")
    if not token or not instance:
        sys.exit("ERROR: Salesforce session unavailable. Run `sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com`.")
    return token, instance.rstrip("/")


def sf_query(soql: str, token: str, instance: str, label: str = "records") -> list[dict]:
    """Fetch every page of a Salesforce REST query."""
    url = (f"{instance}/services/data/{API_VERSION}/query/?q="
           f"{urllib.parse.quote(soql, safe='')}")
    rows: list[dict] = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    page = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    errors = json.loads(body)
                    detail = "; ".join(item.get("message", "") for item in errors)
                except (json.JSONDecodeError, AttributeError):
                    detail = body[:500]
                if exc.code in {429, 500, 502, 503, 504} and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Salesforce query failed ({exc.code}): {detail or exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Could not reach Salesforce: {exc}") from exc
        rows.extend(page.get("records", []))
        if page.get("nextRecordsUrl"):
            print(f"  Fetched {len(rows):,} {label}…", flush=True)
        next_url = page.get("nextRecordsUrl")
        url = f"{instance}{next_url}" if next_url else ""
    return rows


def resolve_split_type_id(token: str, instance: str) -> str:
    """Resolve the org-specific Overlay SplitType ID from an existing split."""
    rows = sf_query(
        "SELECT SplitTypeId FROM OpportunitySplit "
        f"WHERE SplitType.MasterLabel = '{SPLIT_TYPE_LABEL}' LIMIT 1",
        token, instance, "Overlay split type",
    )
    if not rows or not rows[0].get("SplitTypeId"):
        raise RuntimeError("No existing Overlay split was found, so the Overlay SplitType ID could not be resolved.")
    return rows[0]["SplitTypeId"]


def classify(record: dict, team: list[dict], splits: list[dict]) -> dict:
    value = rep_state(record, {member.get("UserId") for member in team},
                      {split.get("SplitOwnerId") for split in splits})
    return {
        "id": record.get("Id"),
        "name": record.get("Name"),
        "account": (record.get("Account") or {}).get("Name"),
        "accountOwner": ((record.get("Account") or {}).get("Owner") or {}).get("Name"),
        "stage": record.get("StageName"),
        "closeDate": record.get("CloseDate"),
        "type": record.get("Type"),
        "segment": record.get("Account_Owner_Market_Segment__c"),
        "region": record.get("Region__c"),
        "amount": record.get("Amount"),
        "nonCommissionable": record.get("Non_Commissionable__c"),
        **value,
    }


def build_soql() -> str:
    types = ", ".join("'" + value + "'" for value in OPPORTUNITY_TYPES)
    return (
        "SELECT Id, Name, StageName, CloseDate, Type, Amount, Non_Commissionable__c, Account.Name, Account.Owner.Name, "
        "Account_Owner_Market_Segment__c, Region__c, " + REP_FIELDS +
        f" FROM Opportunity WHERE {PARTNER_FIELD} != null "
        "AND Non_Commissionable__c = false "
        f"AND Type IN ({types}) AND StageName = '{CLOSED_STAGE}'"
    )


def build_team_soql(opportunity_ids: list[str]) -> str:
    ids = ",".join("'" + value + "'" for value in opportunity_ids)
    return (
        "SELECT OpportunityId, UserId FROM OpportunityTeamMember "
        f"WHERE OpportunityId IN ({ids})"
    )


def build_split_soql(opportunity_ids: list[str], split_type_id: str) -> str:
    ids = ",".join("'" + value + "'" for value in opportunity_ids)
    return (
        "SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit "
        f"WHERE OpportunityId IN ({ids})"
    )


def summarize(active_rows: list[dict]) -> dict:
    return summary(active_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the PSE Split Automation dashboard.")
    parser.add_argument("--org", help="Salesforce CLI org alias or username")
    parser.add_argument("--export-dir", help="Also save a portable pse-data.json in this folder for manual dashboard upload")
    args = parser.parse_args()

    token, instance = sf_session(args.org)
    try:
        split_type_id = resolve_split_type_id(token, instance)
        records = sf_query(build_soql(), token, instance, "opportunities")
        print(f"Found {len(records):,} commissionable Renewal, New Business, or Expansion 08 - Closed opportunities with a tagged Partner. Fetching team and split membership…", flush=True)
        team_by_opp: dict[str, list[dict]] = {}
        splits_by_opp: dict[str, list[dict]] = {}
        ids = [record["Id"] for record in records if record.get("Id")]
        batch_size = 500
        batches = [ids[start:start + batch_size] for start in range(0, len(ids), batch_size)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            for batch in batches:
                futures[pool.submit(sf_query, build_team_soql(batch), token, instance, "team members")] = ("team", batch)
                futures[pool.submit(sf_query, build_split_soql(batch, split_type_id), token, instance, "splits")] = ("split", batch)
            for completed, future in enumerate(as_completed(futures), start=1):
                kind, _ = futures[future]
                for member in future.result():
                    target = team_by_opp if kind == "team" else splits_by_opp
                    target.setdefault(member.get("OpportunityId"), []).append(member)
                if completed % 20 == 0 or completed == len(futures):
                    print(f"  Completed {completed:,}/{len(futures):,} team and split queries.", flush=True)
    except RuntimeError as exc:
        sys.exit(f"ERROR: {exc}")

    classified = [classify(record, team_by_opp.get(record.get("Id"), []), splits_by_opp.get(record.get("Id"), [])) for record in records]
    rows = classified
    summary = summarize(rows)
    data = {
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": {"scope": "Commissionable 08 - Closed Renewal, New Business, and Expansion opportunities",
                   "scopeVersion": SCOPE_VERSION, "stage": CLOSED_STAGE, "partnerField": PARTNER_FIELD, "splitCheck": "any", "commissionableOnly": True,
                   "opportunityTypes": list(OPPORTUNITY_TYPES), "splitType": SPLIT_TYPE_LABEL,
                   "splitTypeId": split_type_id, "splitPercentage": SPLIT_PERCENTAGE},
        "summary": summary,
        "rows": rows,
    }
    os.makedirs(os.path.dirname(DATA_OUT), exist_ok=True)
    with open(DATA_OUT + ".tmp", "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)

    os.replace(DATA_OUT + ".tmp", DATA_OUT)
    if args.export_dir:
        exported = export_snapshot(data, args.export_dir)
        print(f"Saved upload file: {exported}")
    print(f"Checked {len(records):,} closed opportunities with a tagged Partner.")
    print(f"Partner opportunities: {summary['total']:,}")
    print(f"PSE field, Opportunity Team and Splits already complete: {summary['tagged']:,}")
    print(f"PSE to update: {summary['needsUpdate']:,}")
    print(f"Wrote local dashboard data to {os.path.relpath(DATA_OUT, HERE)}")


def export_snapshot(data, directory):
    from snapshot import MAX_UPLOAD_BYTES, validate_snapshot
    checked = validate_snapshot(data)
    # These are exactly the records eligible for the dashboard's category boxes.
    checked['rows'] = [row for row in checked['rows'] if not row['partnerRepOnTeam'] or not row['partnerRepOnSplit']]
    payload = validate_snapshot(checked)
    contents = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(contents.encode('utf-8')) > MAX_UPLOAD_BYTES:
        raise ValueError('The dashboard worklist export exceeds the 25 MB upload limit. The previous export was kept.')
    folder = Path(directory).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'pse-data.json'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(contents)
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists(): temporary.unlink()
    return target


if __name__ == "__main__":
    main()
