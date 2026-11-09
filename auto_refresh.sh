#!/bin/bash
# ============================================================================
# Optional unattended refresh for the PSE dashboard.
# This script is not scheduled by default. If launched manually or by a user
# configured schedule, it writes the local ignored docs/pse-data.json snapshot.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

mkdir -p "$HERE/logs"
LOG="$HERE/logs/auto_refresh.log"

# launchd runs with a minimal PATH — add Homebrew (sf) and python locations.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') refresh start =====" >>"$LOG"

# Pull fresh data from Salesforce (uses the `prod` sf CLI alias / Okta SSO).
if python3 "$HERE/refresh.py" --org prod >>"$LOG" 2>&1; then
  echo "refresh OK — local PSE snapshot written" >>"$LOG"
else
  echo "refresh.py FAILED" >>"$LOG"
  exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') refresh done =====" >>"$LOG"
