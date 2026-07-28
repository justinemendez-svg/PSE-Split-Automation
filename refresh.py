#!/usr/bin/env python3
"""
Japan Overlay AE Review — refresh script.

Pulls last month's Closed/Won Japan Commercial+Enterprise opportunities (any
type) from Salesforce using the `sf` CLI (Okta SSO — same auth as the opp-split
dashboard; no Connected App, no admin needed), and for each opp checks whether
Mari Takiyama — the sole permitted AE Overlay — is tagged in BOTH places:
  1. the Opportunity Team, with role "AE Overlay", AND
  2. an Opportunity Split of type "Overlay".

Each opp is classified OK / Needs Mari / Someone else, then the script writes:
  • docs/data.json                     → the static GitHub Pages viewer (flags baked in)
  • Japan_Overlay_AE_Automation.csv    → raw rows for the App Foundry /admin upload
                                         (also copied to the GTM Ops Drive folder)

This is READ-ONLY: it detects and reports. No Salesforce writes are made.

Usage:
    python3 refresh.py                # last month, default sf org
    python3 refresh.py --org prod     # pick an sf CLI alias/username
    python3 refresh.py --month 2026-06   # override the target month (YYYY-MM)

Prereq (one time, no admin):
    brew install sf
    sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com

Adjust the CONFIG block below to match the org. All values were confirmed
against prod on 2026-07-28 via a read-only probe.
"""

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_OUT = os.path.join(HERE, "docs", "data.json")

# CSV to drag onto the App Foundry app's /admin page (one row per relevant
# overlay tag). The Flask app re-derives the classification at render time, so
# this CSV carries the raw facts only — the file name must match the app's
# EXPECTED_FILE.
CSV_NAME = "Japan_Overlay_AE_Automation.csv"
CSV_OUT = os.path.join(HERE, CSV_NAME)

# Also drop a copy in the GTM Ops shared Drive (Google Drive for Desktop syncs
# it). Set to None to skip the Drive copy.
DRIVE_DIR = ("/Users/justine.mendez/Library/CloudStorage/"
             "GoogleDrive-justine.mendez@zendesk.com/Shared drives/GTM Ops")

# ---- CONFIG — the review rule (confirmed against prod 2026-07-28) ------------
OVERLAY_PERSON = "Mari Takiyama"          # sole permitted AE Overlay
OVERLAY_PERSON_ID = "005PC00000uI2rAYAS"  # active user (an inactive "(OLD)" exists)

CLOSED_WON_STAGE = "08 - Closed"          # + IsWon = true  (NOT "Closed / Won")
DIR_TEAM = "Japan"                        # Opportunity.Dir_Team__c
IN_SCOPE_SEGMENTS = ["Commercial", "Enterprise"]   # Account_Owner_Market_Segment__c
# No Type filter — ANY closed-won type is in scope (Renewal, Expansion,
# Contraction, Services Only, New Business, Admin, …).

OVERLAY_TEAM_ROLE = "AE Overlay"          # OpportunityTeamMember.TeamMemberRole
OVERLAY_SPLIT_TYPE = "Overlay"            # OpportunitySplit SplitType.MasterLabel

SEGMENT_FIELD = "Account_Owner_Market_Segment__c"
DIR_TEAM_FIELD = "Dir_Team__c"

# Only commissionable bookings with real ARR are in scope — this excludes the
# $0-ARR auto-renewals (which are all Non-Commissionable). Confirmed against
# prod 2026-07-28: these two conditions together keep the opps with booking value.
NON_COMMISSIONABLE_FIELD = "Non_Commissionable__c"       # boolean; scope = false
BOOKING_ARR_FIELD = "Total_Booking_ARR_in_USD__c"        # double; scope = > 0


def last_month_range(today=None):
    today = today or dt.date.today()
    first_this = today.replace(day=1)
    end = first_this - dt.timedelta(days=1)          # last day of prev month
    start = end.replace(day=1)                        # first day of prev month
    return start.isoformat(), end.isoformat(), start.strftime("%B %Y")


def month_range(yyyy_mm):
    y, m = (int(x) for x in yyyy_mm.split("-"))
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), (m % 12) + 1, 1)) - dt.timedelta(days=1)
    return start.isoformat(), end.isoformat(), start.strftime("%B %Y")


def _quote(values):
    return ",".join("'" + v.replace("'", "\\'") + "'" for v in values)


def build_soql(start, end):
    """In-scope opps + BOTH child collections, each pre-filtered to the overlay
    role / overlay split type so we only pull the members that matter."""
    team_child = (
        "(SELECT Id, TeamMemberRole, User.Name, UserId "
        f"FROM OpportunityTeamMembers WHERE TeamMemberRole = '{OVERLAY_TEAM_ROLE}')"
    )
    split_child = (
        "(SELECT Id, SplitPercentage, SplitAmount, SplitOwner.Name, SplitOwnerId, "
        "SplitType.MasterLabel "
        f"FROM OpportunitySplits WHERE SplitType.MasterLabel = '{OVERLAY_SPLIT_TYPE}')"
    )
    return (
        f"SELECT Id, Name, CloseDate, Amount, StageName, Type, "
        f"Account.Name, Owner.Name, {SEGMENT_FIELD}, {DIR_TEAM_FIELD}, "
        f"{NON_COMMISSIONABLE_FIELD}, {BOOKING_ARR_FIELD}, "
        f"{team_child}, {split_child} "
        f"FROM Opportunity "
        f"WHERE StageName = '{CLOSED_WON_STAGE}' AND IsWon = true "
        f"AND {DIR_TEAM_FIELD} = '{DIR_TEAM}' "
        f"AND {SEGMENT_FIELD} IN ({_quote(IN_SCOPE_SEGMENTS)}) "
        f"AND {NON_COMMISSIONABLE_FIELD} = false "
        f"AND {BOOKING_ARR_FIELD} > 0 "
        f"AND CloseDate >= {start} AND CloseDate <= {end} "
        f"ORDER BY CloseDate DESC"
    )


def run_sf_query(soql, org=None):
    cmd = ["sf", "data", "query", "--query", soql, "--json"]
    if org:
        cmd += ["--target-org", org]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: `sf` CLI not found. Install it: brew install sf  "
                 "(then: sf org login web --alias prod)")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: Salesforce query failed.\n{e.stderr or e.stdout}")
    payload = json.loads(out.stdout)
    return payload.get("result", {}).get("records", [])


def _child(opp, key):
    return (opp.get(key) or {}).get("records") or []


def analyze(records):
    """One row per opp with the both-places verdict.

    Classification:
      someone_else — a non-Mari user holds the AE Overlay team role (governance
                     exception; Mari is the sole permitted AE Overlay).
      ok           — Mari is on the team (AE Overlay) AND on an Overlay split.
      missing      — otherwise; reason says which place(s) need Mari.
    """
    rows = []
    for opp in records:
        team = _child(opp, "OpportunityTeamMembers")   # already role-filtered
        splits = _child(opp, "OpportunitySplits")       # already type-filtered

        overlay_team = [((m.get("User") or {}).get("Name") or "").strip() for m in team]
        overlay_team = [n for n in overlay_team if n]
        mari_on_team = OVERLAY_PERSON in overlay_team
        other_overlay = sorted({n for n in overlay_team if n != OVERLAY_PERSON})

        split_owners = [((s.get("SplitOwner") or {}).get("Name") or "").strip() for s in splits]
        mari_on_split = OVERLAY_PERSON in split_owners

        if other_overlay:
            bucket = "someone_else"
            reason = "AE Overlay held by: " + ", ".join(other_overlay)
        elif mari_on_team and mari_on_split:
            bucket, reason = "ok", "Mari in team + split"
        else:
            missing = []
            if not mari_on_team:
                missing.append("team role")
            if not mari_on_split:
                missing.append("Overlay split")
            bucket = "missing"
            reason = "Needs Mari: " + " & ".join(missing)

        rows.append({
            "id": opp.get("Id"),
            "name": opp.get("Name"),
            "account": (opp.get("Account") or {}).get("Name"),
            "owner": (opp.get("Owner") or {}).get("Name"),
            "segment": opp.get(SEGMENT_FIELD),
            "dirTeam": opp.get(DIR_TEAM_FIELD),
            "type": opp.get("Type"),
            "closeDate": opp.get("CloseDate"),
            "amount": opp.get("Amount"),
            "bookingArr": opp.get(BOOKING_ARR_FIELD),
            "nonCommissionable": opp.get(NON_COMMISSIONABLE_FIELD),
            "stage": opp.get("StageName"),
            "mariOnTeam": mari_on_team,
            "mariOnSplit": mari_on_split,
            "otherOverlay": other_overlay,
            "overlayTeamMembers": overlay_team,
            "bucket": bucket,
            "reason": reason,
        })
    return rows


def summarize(rows):
    def n(b):
        return sum(1 for r in rows if r["bucket"] == b)
    by_type = {}
    for r in rows:
        t = r["type"] or "—"
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": len(rows),
        "ok": n("ok"),
        "missing": n("missing"),
        "someoneElse": n("someone_else"),
        "byType": by_type,
        "totalAmount": sum(r["amount"] or 0 for r in rows),
    }


def write_csv(rows, start, end, label, path=CSV_OUT):
    """One row per opp for upload to the App Foundry /admin page.

    The Flask app re-derives the classification from these raw booleans, so we
    deliberately do NOT bake `bucket`/`reason` in — only the facts each opp has.
    period_* columns let the app show the right month.
    """
    head = ["opp_id", "opp_name", "account", "owner", "segment", "dir_team",
            "type", "close_date", "amount", "booking_arr_usd", "non_commissionable",
            "stage", "mari_on_team", "mari_on_split", "other_overlay",
            "period_label", "period_start", "period_end"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r in rows:
            w.writerow([
                r["id"], r["name"], r["account"], r["owner"], r["segment"],
                r["dirTeam"], r["type"], r["closeDate"], r["amount"],
                r["bookingArr"], r["nonCommissionable"], r["stage"],
                r["mariOnTeam"], r["mariOnSplit"], "; ".join(r["otherOverlay"]),
                label, start, end,
            ])


def copy_to_drive(path):
    if not DRIVE_DIR:
        return
    try:
        os.makedirs(DRIVE_DIR, exist_ok=True)
        dest = os.path.join(DRIVE_DIR, CSV_NAME)
        shutil.copyfile(path, dest)
        print(f"  Copied CSV → {dest}")
    except OSError as e:
        print(f"  WARNING: could not copy CSV to Drive ({e}). "
              f"Is Google Drive for Desktop running?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", help="sf CLI target org alias or username")
    ap.add_argument("--month", help="target month YYYY-MM (default: last month)")
    args = ap.parse_args()

    start, end, label = month_range(args.month) if args.month else last_month_range()

    print(f"Pulling Japan Closed-Won opps for {label} ({start} … {end}) …")
    records = run_sf_query(build_soql(start, end), org=args.org)
    rows = analyze(records)
    summary = summarize(rows)

    data = {
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {"start": start, "end": end, "label": label},
        "config": {"overlayPerson": OVERLAY_PERSON, "dirTeam": DIR_TEAM,
                   "segments": IN_SCOPE_SEGMENTS},
        "summary": summary,
        "rows": rows,
    }
    os.makedirs(os.path.dirname(DATA_OUT), exist_ok=True)
    with open(DATA_OUT, "w") as f:
        json.dump(data, f, indent=2)
    write_csv(rows, start, end, label)

    print(f"  {summary['total']} in-scope opps · OK {summary['ok']} · "
          f"Needs Mari {summary['missing']} · Someone else {summary['someoneElse']}")
    print(f"  Wrote {os.path.relpath(DATA_OUT, HERE)}")
    print(f"  Wrote {os.path.relpath(CSV_OUT, HERE)}  (drag onto the app's /admin page)")
    copy_to_drive(CSV_OUT)
    print("\nNext, either:")
    print(f"  • App Foundry app: open the app's /admin page and drop {CSV_NAME}, OR")
    print("  • GitHub Pages: git add docs/data.json && git commit -m \"Refresh\" && git push")


if __name__ == "__main__":
    main()
