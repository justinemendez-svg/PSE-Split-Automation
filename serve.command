#!/bin/bash
# Double-click to open the PSE Split Automation dashboard in Google Chrome.
HERE="$(cd "$(dirname "$0")" && pwd)"
# 8772 may be used by a static preview; use a dedicated port for Salesforce actions.
PORT=8773
echo "PSE Split Automation → http://localhost:$PORT"
echo "Leave this window open while using the dashboard. Press Ctrl-C to stop."
( sleep 1; open -a "Google Chrome" "http://localhost:$PORT" ) &
cd "$HERE" || exit 1
python3 serve_local.py --org prod --port "$PORT"
