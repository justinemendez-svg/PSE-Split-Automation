# Get your own "Japan Overlay AE Review & Update" running

**This is the same dashboard as `http://localhost:8771/`** — it lists Japan
Closed-Won opps that need **Mari Takiyama** tagged as AE Overlay, with a button
that **writes the fix straight to Salesforce** (adds her to the Opportunity Team
+ a 100% Overlay split). It runs on *your own laptop* using *your own* Salesforce
login, so **you don't need any admin permission.**

You don't have to understand any of the code. **Claude Code will build the whole
thing for you** — you just paste one instruction and answer the login prompt.

---

## What you need (one-time)

- **A Mac.**
- **Claude Code** installed and open in a Terminal.
- **A Salesforce prod login** (your normal Zendesk Okta SSO). Everyone on the
  team already has the access this needs (API Enabled) — you do **not** need
  "Customize Application" or any admin rights.

That's it. No coding, no `pip install`.

---

## Step 1 — Open Claude Code, then paste this

Open **Claude Code** in a Terminal window and paste the block below **exactly as
is**, then hit Enter. Claude will do every step itself — install the Salesforce
CLI if needed, log you in, download the code, and start the dashboard.

> 📋 **COPY EVERYTHING BETWEEN THE LINES AND PASTE IT INTO CLAUDE:**

```text
Please set up the "Japan Overlay AE Review & Update" local dashboard on my Mac.
It's a tool that writes an AE Overlay tag to Salesforce using my own sf CLI login.
Do all of these steps for me, and stop to tell me if any step needs my input:

1. Check if the Salesforce CLI is installed by running: sf --version
   If it is not installed, install it with Homebrew: brew install sf
   (If Homebrew isn't installed either, install it first from https://brew.sh)

2. Log me in to Salesforce production with my own SSO by running:
   sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
   A browser will open — tell me to log in with my Zendesk Okta SSO, and wait
   for me to finish. Then confirm it worked with: sf org display --target-org prod

3. Download the code from this public GitHub repo into my Desktop:
   cd ~/Desktop && git clone https://github.com/justinemendez-svg/Japan-Overlay-AE.git
   Then cd into the Japan-Overlay-AE folder.

4. Start the local dashboard server by running:
   python3 serve_local.py --org prod
   This uses only Python's standard library, so no pip install is needed.
   Wait ~20 seconds for it to print "Open → http://localhost:8771/".

5. Tell me to open http://localhost:8771/ in my browser, and remind me to KEEP
   THE TERMINAL WINDOW OPEN — the dashboard only works while this server is
   running. If I close it, just run "python3 serve_local.py --org prod" again.

Important safety notes to tell me:
- Nothing is written to Salesforce until I click a button AND confirm a preview.
- It writes as ME (my own login), exactly as if I edited the opp by hand.
- If an opp already has the tag, it skips it (it never duplicates).
```

> ⬆️ **THAT'S THE WHOLE SETUP.** Claude runs it start to finish. The only thing
> it needs from you is to log in when the browser pops open in step 2.

---

## Step 2 — Use the dashboard

Once Claude tells you it's running, open **http://localhost:8771/** in your
browser.

1. Click the **"Overlay AE to update"** tab — these are the opps needing Mari.
2. **One opp:** click **"Update in Salesforce →"** → a preview shows exactly what
   it will add → click **OK** to write, **Cancel** to stop.
3. **The whole filtered list:** click **"Update all"** at the top. (Use the
   search / type / segment filters first to scope a smaller batch.)
4. On success the opp shows *"Done — Mari is now in both"* and drops off the list.

### The "Refresh data" button — get the latest opps

Top-right of the dashboard (next to **Export CSV**) there's a **Refresh data**
button. Click it → confirm → it re-pulls the latest Japan Closed-Won opps
straight from Salesforce and rebuilds the list **right in the page** — no
terminal needed. It only reads Salesforce (no writes) and takes up to a minute.

---

## If something's not working, tell Claude

You don't need to fix anything yourself — just paste the problem to Claude. Common ones:

| What you see | Just say to Claude |
|---|---|
| **The page is blank / won't load** at localhost:8771 | *"The dashboard isn't loading — is the server still running? Please start it again."* (This is almost always it: the server stopped because the terminal was closed.) |
| **The button just opens Salesforce** instead of auto-writing | *"The Update button opens Salesforce instead of writing — did I open the page through the local server?"* (You must use `http://localhost:8771/`, not a file or GitHub Pages.) |
| **`Port 8771 in use`** | *"Port 8771 is busy — please start the server on a different port and tell me the new address."* |
| **A write fails with a permissions error** | Screenshot it and ping **Justine** — it's an access question, not an admin/Connected-App issue. |
| **`No active user named 'Mari Takiyama'`** | *"It can't find Mari — please re-run the Salesforce login pointing at https://zendesk.my.salesforce.com."* |

---

## Good to know

- **You write as yourself.** It borrows your own `sf`/Okta session on your laptop
  — fully attributed to you, same as editing the opp by hand.
- **It never duplicates.** Idempotent: if the team member or split already
  exists, it only adds what's missing; if both exist, it writes nothing.
- **Preview + confirm before every write.** "Update all" warns you with the count
  first — filter and eyeball the list before running the whole thing.
- **Every write is logged** to `writes.log.jsonl` in the folder (which opp, what
  was created, when, and that it was you).
- **Keep the terminal open** while you work — closing it stops the dashboard.

---

*Built by Justine Mendez (Sales Ops / MSS / APAC). Questions → ping Justine.*
