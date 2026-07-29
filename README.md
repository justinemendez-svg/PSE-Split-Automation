# Japan Overlay AE — Review & Update

A governance worklist that checks whether **Mari Takiyama** — the sole permitted
AE Overlay — is tagged as **"AE Overlay"** in **both** the Opportunity Team **and**
an Opportunity Split (type "Overlay") on every in-scope Japan opportunity, and
flags the ones missing her (or that have *someone else* in the role).

It has two parts:

- **Review** — read-only. Lists every in-scope opp and its status (OK / Needs
  Mari / Someone else).
- **Update** — when you run the local server (`serve_local.py`, port **8771**),
  each "Needs Mari" opp gets an **"Update in Salesforce →"** button that writes
  the fix (adds her to the team + a 100% Overlay split), plus an **"Update all"**
  button and a **"Refresh data"** button. Every write is preview → confirm →
  audited, and writes as **you** via your own `sf` CLI login — no admin, no
  Connected App.

---

## Run it on your own laptop (teammates start here)

**The repo is public — you can clone it and have your own `http://localhost:8771/`
running in a couple of minutes, using your own Salesforce login. No admin needed.**

The easiest path: open **Claude Code** and hand it **[`TEAMMATE_SETUP.md`](TEAMMATE_SETUP.md)**
(or the PDF, `Japan_Overlay_Teammate_Setup.pdf`) — it contains a ready-to-paste
prompt that makes Claude install the CLI, log you in, clone this repo, and start
the dashboard for you.

Prefer to do it by hand? Four commands:

```bash
# 1. Salesforce CLI (skip if you already have `sf`)
brew install sf

# 2. Log in to prod with your own Zendesk Okta SSO
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com

# 3. Get the code
git clone https://github.com/justinemendez-svg/Japan-Overlay-AE.git
cd Japan-Overlay-AE

# 4. Start the dashboard → open http://localhost:8771/
python3 serve_local.py --org prod
```

Uses only the Python standard library — **no `pip install`**. Keep the terminal
open; the dashboard only works while the server is running. Nothing is written
until you click a button **and** confirm the preview.

See **[`TEAMMATE_SETUP.md`](TEAMMATE_SETUP.md)** for how to use it, safety notes,
and troubleshooting.

---

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

## What the "Update" actually writes

For each "Needs Mari" opp, on confirm, it creates whichever is missing (idempotent
— it skips what already exists):

1. **OpportunityTeamMember** — Mari, `TeamMemberRole` = `AE Overlay`, access `Read`.
2. **OpportunitySplit** — Mari, Overlay split type, `SplitPercentage` = 100.

It authenticates with your local `sf` CLI / Okta token, so every change is fully
attributed to you — the same as editing the opp by hand. Every write is appended
to `writes.log.jsonl` (local only — **not** committed; it holds prod opp IDs).

## What's here

| File | Purpose |
|---|---|
| **`refresh.py`** | ⭐ The engine. Pulls from Salesforce via the `sf` CLI, classifies each opp, writes `docs/data.json` **and** `Japan_Overlay_AE_Automation.csv`, and copies the CSV to the GTM Ops Drive folder. |
| **`serve_local.py`** | ⭐ The local **Review & Update** server (port 8771). Serves `docs/` and adds the write endpoints (`/api/preview`, `/api/write`, `/api/write-all`, `/api/refresh`). |
| `docs/index.html`, `docs/app.js` | The dashboard UI (Review tab + Update tab, filters, CSV export, Refresh-data + Update-all buttons). |
| `docs/data.json` | The latest analysis (written by `refresh.py`). |
| `TEAMMATE_SETUP.md` / `Japan_Overlay_Teammate_Setup.pdf` | The paste-into-Claude setup guide for teammates. |
| `serve.command` | Alternative read-only viewer on port 8770 (double-click). No write buttons — use `serve_local.py` for those. |
| `auto_refresh.sh` | launchd wrapper — sets PATH, runs `refresh.py --org prod`, logs to `logs/`. |
| `com.zendesk.japan-overlay.refresh.plist` | LaunchAgent — runs `auto_refresh.sh` **every 8 hours (8 AM / 4 PM / 12 AM Manila)**. |
| `japan-overlay-appfoundry/` | The App Foundry (Flask + Postgres) flavor — the shareable, no-login version. |

## Refresh the data

From inside the dashboard, click **"Refresh data"** (top-right). Or from the
terminal:

```bash
python3 refresh.py --org prod                   # default month range
python3 refresh.py --org prod --month 2026-06   # a specific month
```

Either way it rewrites `docs/data.json` + the CSV and copies the CSV to the GTM
Ops Drive folder. Reload the page (or let the in-page refresh do it) to see fresh
numbers.

## Scheduled refresh (every 8 hours, Manila)

Installed as a LaunchAgent on Justine's machine. Manage it with:

```bash
launchctl list | grep japan-overlay                                    # status
launchctl kickstart -k gui/$(id -u)/com.zendesk.japan-overlay.refresh  # run now

# reload after editing the plist
cp com.zendesk.japan-overlay.refresh.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.zendesk.japan-overlay.refresh 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zendesk.japan-overlay.refresh.plist
```

Logs: `logs/auto_refresh.log` (script) and `logs/launchd.{out,err}.log` (launchd).

> The Mac's clock shows `PST +0800`, but that **+0800 offset is Manila time** — the
> "PST" label is a misnomer on this machine.

## Two flavors (same rule, two hosts)

**One pull, two artifacts** — the classification lives in **both** Python and
Flask so each host reflects the rule independently; the JS only renders.

| Flavor | Where | Data source | For |
|---|---|---|---|
| **Local** (this folder) | `docs/` + `serve_local.py` | `docs/data.json` (flags baked in by `refresh.py`) | Reviewing **and** writing the fix on your own laptop |
| **App Foundry** | `japan-overlay-appfoundry/` | `Japan_Overlay_AE_Automation.csv` → Postgres (flags re-derived at render) | The shareable, no-login version |
