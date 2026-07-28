#!/bin/bash
# Double-click to open the Japan Overlay AE Review locally.
# A tiny web server is needed because the viewer fetches data.json, which
# browsers block over file:// — so we serve the docs/ folder over http instead.
cd "$(dirname "$0")/docs" || exit 1
PORT=8770
echo "Japan Overlay AE Review → http://localhost:$PORT"
echo "(Leave this window open while you use the dashboard. Close it or press Ctrl-C when done.)"
# Open the browser once the server is up.
( sleep 1; open "http://localhost:$PORT" ) &
python3 -m http.server "$PORT"
