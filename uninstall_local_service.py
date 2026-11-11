#!/usr/bin/env python3
"""Remove the per-user macOS LaunchAgent for PSE Split Automation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.zendesk.pse-split-automation"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SERVICE = f"gui/{os.getuid()}/{LABEL}"


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The LaunchAgent only applies to macOS.")
    result = subprocess.run(["/bin/launchctl", "bootout", SERVICE], capture_output=True, text=True)
    if result.returncode and "Could not find service" not in result.stderr:
        print((result.stderr or result.stdout).strip(), file=sys.stderr)
    PLIST_PATH.unlink(missing_ok=True)
    print("Removed the PSE Split Automation background service. Its local app copy and Salesforce snapshot remain in Application Support.")


if __name__ == "__main__":
    main()
