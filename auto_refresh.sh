#!/bin/bash
# ============================================================================
# Japan Overlay AE Review — unattended refresh.
# Pulls from Salesforce and writes docs/data.json + Japan_Overlay_AE_Automation.csv
# (the CSV is also copied into the GTM Ops Google Drive folder by refresh.py).
# Invoked every 8 hours (8 AM, 4 PM, 12 AM Manila time) by launchd
# (see com.zendesk.japan-overlay.refresh.plist). Logs to logs/auto_refresh.log.
#
# To update the App Foundry dashboard: download the CSV from Drive, then drop it
# on the app's /admin page. The local viewer just needs `serve.command` reopened.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

mkdir -p "$HERE/logs"
LOG="$HERE/logs/auto_refresh.log"

# launchd runs with a minimal PATH — add Homebrew (sf) and python locations.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') refresh start =====" >>"$LOG"

# Pull fresh data from Salesforce (uses the `prod` sf CLI alias / Okta SSO);
# refresh.py writes docs/data.json and the CSV, and copies the CSV to Drive.
if python3 "$HERE/refresh.py" --org prod >>"$LOG" 2>&1; then
  echo "refresh OK — data.json + CSV written, CSV copied to Drive" >>"$LOG"
else
  echo "refresh.py FAILED" >>"$LOG"
  exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') refresh done =====" >>"$LOG"
