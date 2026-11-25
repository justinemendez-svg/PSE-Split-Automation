#!/usr/bin/env python3
"""Install and start the per-user macOS LaunchAgent for PSE Split Automation."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
import json
import urllib.error
import urllib.request
from pathlib import Path

LABEL = "com.zendesk.pse-split-automation"
PORT = 8773
URL = f"http://localhost:{PORT}/"
ROOT = Path(__file__).resolve().parent
APP_ROOT = Path.home() / "Library" / "Application Support" / LABEL
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = AGENT_DIR / f"{LABEL}.plist"
LOG_DIR = APP_ROOT / "logs"
DOMAIN = f"gui/{os.getuid()}"
SERVICE = f"{DOMAIN}/{LABEL}"


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, check=check)


def dashboard_ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as response:
            if response.status != 200:
                return False
            health = json.loads(response.read().decode("utf-8"))
            return (
                health.get("refreshEnabled") is True
                and health.get("writeEnabled") is True
                and health.get("teamRole") == "Partner Account Executive"
                and health.get("splitType") == "Overlay"
            )
    except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_dashboard(seconds: int = 40) -> bool:
    for _ in range(seconds * 2):
        if dashboard_ready():
            return True
        time.sleep(0.5)
    return False


def sync_app_copy() -> None:
    """Copy runnable app files out of macOS-protected Desktop folders."""
    def ignored(directory: str, names: list[str]) -> set[str]:
        skipped = {
            ".git", ".DS_Store", ".venv", "venv", "node_modules", "__pycache__",
            "logs", "writes.log.jsonl", "pse-data.json",
        }
        return {
            name for name in names
            if name in skipped or name.endswith((".pyc", ".log")) or name.startswith(".env")
        }

    # Retire only known obsolete managed code, never snapshots, audit or logs.
    for relative in ('docs/local-index.html', 'docs/local-app.js', 'LOCAL_PREVIEW.md',
                     'pse-split-appfoundry', 'tests/test_workflow.py', 'tests/test_hosted.py',
                     'tests/test_reliability.py', 'tests/test_categories.js'):
        target = APP_ROOT / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    shutil.copytree(ROOT, APP_ROOT, dirs_exist_ok=True, ignore=ignored)


def write_agent() -> None:
    python = shutil.which("python3") or sys.executable
    sf = shutil.which("sf")
    if not sf:
        raise SystemExit("Salesforce CLI (`sf`) is not on PATH. Install it and log in before starting the dashboard.")
    auth = subprocess.run([sf, "org", "display", "--json", "--target-org", "prod"], capture_output=True, text=True)
    try:
        result = json.loads(auth.stdout).get("result", {})
    except (json.JSONDecodeError, AttributeError):
        result = {}
    if auth.returncode or not result.get("accessToken") or not result.get("instanceUrl"):
        raise SystemExit("Salesforce CLI has no usable `prod` session. Run `sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com` first.")
    paths = [str(Path(sf).resolve().parent), *os.environ.get("PATH", "").split(os.pathsep)]
    environment = {
        "PATH": os.pathsep.join(dict.fromkeys(path for path in paths if path)),
        "PYTHONUNBUFFERED": "1",
    }
    definition = {
        "Label": LABEL,
        "ProgramArguments": [python, "-u", str(APP_ROOT / "serve_local.py"), "--org", "prod", "--port", str(PORT)],
        "WorkingDirectory": str(APP_ROOT),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(LOG_DIR / "dashboard-server.log"),
        "StandardErrorPath": str(LOG_DIR / "dashboard-server-error.log"),
    }
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(definition, fmt=plistlib.FMT_XML, sort_keys=False))


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The automatic background service requires macOS. On other systems, run `python3 serve_local.py --org prod` in a terminal.")

    sync_app_copy()
    # Validate Salesforce access and write the replacement definition before
    # stopping a working copy of the current service.
    write_agent()
    launchctl("bootout", SERVICE, check=False)

    if dashboard_ready():
        raise SystemExit("A PSE dashboard server is already using port 8773 outside LaunchAgent. Stop that process, then run serve.command again.")

    result = launchctl("bootstrap", DOMAIN, str(PLIST_PATH), check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"Could not install the background service: {detail}")
    if not wait_for_dashboard():
        raise SystemExit(f"The service was installed but did not start. Check {LOG_DIR / 'dashboard-server-error.log'}.")
    print(f"Installed the PSE dashboard background service from {APP_ROOT}. It starts at login and restarts if it stops.\nOpen {URL}")


if __name__ == "__main__":
    main()
