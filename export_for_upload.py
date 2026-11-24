#!/usr/bin/env python3
"""Read Salesforce locally and save the portable snapshot in Google Drive."""
import argparse
from pathlib import Path
import subprocess
import sys

DEFAULT_EXPORT_DIR = Path.home() / 'Library/CloudStorage/GoogleDrive-justine.mendez@zendesk.com/Shared drives/GTM Ops/MSS/PSE_Split_Automation'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', default='prod', help='Salesforce CLI org alias')
    parser.add_argument('--export-dir', type=Path, default=DEFAULT_EXPORT_DIR, help='Override the Google Drive export folder')
    args = parser.parse_args()
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('refresh.py')), '--org', args.org,
                             '--export-dir', str(args.export_dir)])
    sys.exit(result.returncode)
