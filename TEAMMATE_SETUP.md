# Japan Overlay AE — Local Auto-Update Setup

**For: a teammate who wants the "Update in Salesforce" dashboard running on their
own laptop, writing with their OWN Salesforce login (no admin permission needed).**

This is the same tool as `http://localhost:8771/` — a dashboard that lists Japan
Closed-Won opps needing **Mari Takiyama** tagged as AE Overlay, with a button that
**writes the fix straight to Salesforce** (adds her to the Opportunity Team + a
100% Overlay split). It works without any admin because it borrows *your own*
`sf` CLI / Okta login that lives on your laptop.

> **Hand this whole file to Claude Code on your laptop and say:**
> *"Follow this guide and set up the Japan Overlay local server for me."*
> Claude can do every step below for you.

---

## What you need first (one-time)

1. **A Mac** (these steps assume macOS).
2. **Python 3** — already on every Mac (`python3 --version`).
3. **Salesforce CLI (`sf`)** + a login to prod with your own Zendesk SSO.
4. **Access to Salesforce prod** with **API Enabled** (everyone on the team has
   this — it's what lets the write work). You do **not** need "Customize
   Application" or any admin permission.

No `pip install` is needed — the server uses only the Python standard library.

---

## Step 1 — Install the Salesforce CLI (skip if you have `sf`)

```bash
# Check first:
sf --version
# If "command not found", install via Homebrew:
brew install sf
# (No Homebrew? Get it at https://brew.sh, or install sf from
#  https://developer.salesforce.com/tools/salesforcecli )
```

## Step 2 — Log in to Salesforce prod (your own SSO)

```bash
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
```

A browser opens → log in with your normal **Zendesk Okta SSO**. That's it —
this saves a token on YOUR laptop that the server will use to write as YOU.
Confirm it worked:

```bash
sf org display --target-org prod
```

## Step 3 — Get the code from GitHub (public repo)

```bash
cd ~/Desktop
git clone https://github.com/justinemendez-svg/Japan-Overlay-AE.git
cd Japan-Overlay-AE
```

You now have `serve_local.py`, `refresh.py`, and the `docs/` dashboard.

## Step 4 — Pull fresh data (optional but recommended)

The repo ships with a `docs/data.json`, but to get the latest opps, pull from
Salesforce yourself:

```bash
python3 refresh.py --org prod
```

This reads Salesforce (read-only) and rebuilds `docs/data.json` — the list of
opps and who's missing Mari. Re-run it anytime for fresh data.

## Step 5 — Start the local write server

```bash
python3 serve_local.py --org prod
```

You'll see it resolve your login, then:

```
Open  →  http://localhost:8771/
```

Open that in your browser. Nothing is written until you click **and** confirm.

---

## How to use it

1. Open **http://localhost:8771/**
2. Click the **"Overlay AE to update"** tab — the opps needing Mari.
3. For one opp: click **"Update in Salesforce →"** → a preview pops up showing
   exactly what it will add → click **OK** to write, **Cancel** to stop.
4. For the whole filtered list: **"Update all"** at the top (use the
   search/type/segment filters first to scope a smaller batch).
5. On success the opp shows *"Done — Mari is now in both"* and drops off the list.

Every write is logged to `writes.log.jsonl` in the folder (opp, what was created,
timestamp, and that it was you).

---

## Safety notes

- **You write as yourself.** It uses your own `sf`/Okta session — same as if you
  edited the opp by hand in Salesforce. Fully attributed to you.
- **Idempotent.** If an opp already has the team member or the split, it only
  adds what's missing. If both exist, it writes nothing.
- **Preview + confirm** before every write. "Update all" warns with the count.
- **"Update all" writes to production** for every opp in the current view —
  filter first and eyeball the list before running the whole thing.

---

## Quick command recap (copy-paste)

```bash
# one-time
brew install sf
sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
cd ~/Desktop && git clone https://github.com/justinemendez-svg/Japan-Overlay-AE.git
cd Japan-Overlay-AE

# each session
python3 refresh.py --org prod       # get fresh data (optional)
python3 serve_local.py --org prod   # start server → open http://localhost:8771/
```

To stop the server: press **Ctrl-C** in the terminal, or just close it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `sf: command not found` | Install it (Step 1). |
| `No active user named 'Mari Takiyama'` | Your `sf` login may point to the wrong org — re-run Step 2 with `--instance-url https://zendesk.my.salesforce.com`. |
| Button just opens Salesforce (doesn't auto-write) | You opened the dashboard as a plain file or on GitHub Pages. It must be opened via **http://localhost:8771/** (the running server). |
| `Port 8771 in use` | Run `python3 serve_local.py --org prod --port 8781` and open that port instead. |
| Write fails with a permissions error | You may lack API access on this opp — check with Justine; it's not an admin/Connected-App issue. |

---

*Built by Justine Mendez (Sales Ops / MSS / APAC). Questions → ping Justine.*
