#!/usr/bin/env python3
"""Read Salesforce and export the current local snapshot to an explicitly selected folder."""
import argparse
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', default='prod', help='Salesforce CLI org alias')
    parser.add_argument('--export-dir', type=Path, required=True, help='Your actual synced Google Drive folder or another export folder')
    args = parser.parse_args()
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('refresh.py')), '--org', args.org,
                             '--export-dir', str(args.export_dir)])
    sys.exit(result.returncode)
