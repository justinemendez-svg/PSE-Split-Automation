# Japan Overlay AE Review

A governance worklist that checks whether **Mari Takiyama** — the sole permitted
AE Overlay — is tagged as **"AE Overlay"** in **both** the Opportunity Team **and**
an Opportunity Split (type "Overlay") on every in-scope Japan opportunity, and
flags the ones that are missing her (or that have *someone else* in the role).

**Read-only.** It detects and reports; it makes **no** Salesforce writes.

## Scope (the SFDC filter)

Any **Closed-Won** opportunity (**any** type — New Business, Expansion, Renewal,
Admin, Contraction, Services Only, …) where:

- **Dir Team** = `Japan` (`Opportunity.Dir_Team__c`)
- **Segment** = `Commercial` or `Enterprise` (`Account_Owner_Market_Segment__c`)
- **Non-Commissionable** = `False` (`Non_Commissionable__c` — i.e. commissionable)
- **Total Booking ARR (in USD)** > 0 (`Total_Booking_ARR_in_USD__c`)

Stage is `08 - Closed` **and** `IsWon = true` (that's how "Closed-Won" is stored
in this org — there is no literal "Closed / Won" stage).

> The commissionable + ARR>0 pair excludes the $0-ARR auto-renewals (which are
> all Non-Commissionable), so the worklist covers only booking deals with value.

## The rule

| Bucket | Meaning |
|---|---|
| **OK** | Mari is on the team (role AE Overlay) **and** on an Overlay split. |
| **Needs Mari** | Mari is missing from the team, the split, or both. The reason says which. |
| **Someone else** | A non-Mari user holds the AE Overlay team role — a governance exception to escalate. |

## Two flavors (same rule, two hosts)

This mirrors the MSS Opp Split dashboard's architecture: **one pull, two
artifacts**, and the classification lives in **both** Python and Flask so each host
reflects the rule independently — the JS only renders.

| Flavor | Where | Data source | For |
|---|---|---|---|
| **Local** (this folder) | `docs/` static viewer | `docs/data.json` (flags baked in by `refresh.py`) | You, to check the numbers |
| **App Foundry** | `japan-overlay-appfoundry/` | `Japan_Overlay_AE_Automation.csv` → Postgres (flags re-derived at render) | The shareable, no-login version |

## What's here

| File | Purpose |
|---|---|
| **`refresh.py`** | ⭐ The engine. Pulls from Salesforce via the `sf` CLI, classifies each opp, writes `docs/data.json` **and** `Japan_Overlay_AE_Automation.csv`, and copies the CSV to the GTM Ops Drive folder. |
| `docs/index.html`, `docs/app.js` | The local static viewer (dark theme, sortable, bucket cards, type filter, CSV export). |
| `docs/data.json` | The latest analysis (written by `refresh.py`). |
| `Japan_Overlay_AE_Automation.csv` | Raw one-row-per-opp CSV for the App Foundry `/admin` upload (also copied to Drive). |
| `serve.command` | Double-click → serves `docs/` on `http://localhost:8770` and opens it (needed because browsers block `fetch()` over `file://`). |
| `auto_refresh.sh` | launchd wrapper — sets PATH, runs `refresh.py --org prod`, logs to `logs/`. |
| `com.zendesk.japan-overlay.refresh.plist` | LaunchAgent — runs `auto_refresh.sh` **daily at 6 AM Manila**. |

## Prerequisites (one time, no admin)

```bash
brew install sf
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
```

Uses your existing Okta SSO — no Connected App, no admin needed. Same auth as the
opp-split dashboard.

## Use it

```bash
python3 refresh.py --org prod          # last month
python3 refresh.py --org prod --month 2026-06   # a specific month
```

Then, to check locally: **double-click `serve.command`** (or `open serve.command`).
The browser opens the worklist at `http://localhost:8770`. Reload after each
refresh to see fresh numbers.

## Scheduled refresh (daily 6 AM Manila)

Already installed as a LaunchAgent. Manage it with:

```bash
# status
launchctl list | grep japan-overlay

# run it now (don't wait for 6 AM)
launchctl kickstart -k gui/$(id -u)/com.zendesk.japan-overlay.refresh

# reload after editing the plist
cp com.zendesk.japan-overlay.refresh.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.zendesk.japan-overlay.refresh 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zendesk.japan-overlay.refresh.plist
```

Logs: `logs/auto_refresh.log` (script) and `logs/launchd.{out,err}.log` (launchd).

> The Mac's clock shows `PST +0800`, but that **+0800 offset is Manila time** — the
> "PST" label is a misnomer on this machine, so `Hour 6` fires at 6 AM MNL.

## Refresh → publish

`refresh.py` copies the CSV to the GTM Ops Drive folder each run
(`…/Shared drives/GTM Ops/Japan_Overlay_AE_Automation.csv`, overwritten each run).
To update the **App Foundry** dashboard, download that CSV and drop it on the app's
`/admin` page. See `japan-overlay-appfoundry/README.md`.
