#!/bin/bash
# Install/start the persistent local dashboard service, then open its page.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
URL="http://localhost:8773/"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The automatic background service is macOS-only."
  echo "Run: python3 \"$HERE/serve_local.py\" --org prod --port 8773"
  exit 1
fi

python3 "$HERE/install_local_service.py"
open -a "Google Chrome" "$URL"
echo "Opened PSE Split Automation at $URL"
