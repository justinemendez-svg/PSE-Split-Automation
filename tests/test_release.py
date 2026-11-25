"""Release checks: one current build, safe exports and non-destructive install."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_local_service
from refresh import classify, export_snapshot
from snapshot import validate_snapshot
from test_local_updates import DetailedSalesforce


class ReleaseChecks(unittest.TestCase):
    def snapshot(self):
        sf = DetailedSalesforce()
        return {'config': {'scopeVersion': 6}, 'generatedAt': '2026-11-25T12:00:00+08:00',
                'rows': [classify(sf.record, sf.members, sf.entries)]}

    def test_current_export_preserves_added_fields_and_membership_details(self):
        data = self.snapshot()
        data['rows'][0]['partnerOwnerProfile'] = 'Example profile'
        with tempfile.TemporaryDirectory() as folder:
            target = export_snapshot(data, folder)
            saved = json.loads(target.read_text())
            self.assertEqual(saved['config']['scopeVersion'], 6)
            self.assertEqual(saved['rows'][0]['partnerOwnerProfile'], 'Example profile')
            self.assertEqual(saved['rows'][0]['splitEntries'][0]['SplitPercentage'], 37)
            self.assertEqual(saved['summary']['total'], 1)
            self.assertTrue(saved['rows'][0]['membershipDetailsLoaded'])

    def test_invalid_export_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'pse-data.json'
            target.write_text('previous snapshot')
            data = self.snapshot()
            data['config']['scopeVersion'] = 5
            with self.assertRaises(ValueError):
                export_snapshot(data, folder)
            self.assertEqual(target.read_text(), 'previous snapshot')

    def test_export_rejects_duplicate_ids_and_excluded_partners(self):
        data = self.snapshot()
        duplicate = copy.deepcopy(data['rows'][0])
        duplicate['id'] = duplicate['id'][:15]
        data['rows'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_snapshot(data)
        data['rows'] = data['rows'][:1]
        data['rows'][0]['partnerName'] = 'AWS Marketplace'
        with self.assertRaisesRegex(ValueError, 'outside'):
            validate_snapshot(data)

    def test_install_retires_old_build_and_preserves_snapshot_audit_and_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            app = Path(folder) / 'installed'
            (source / 'docs').mkdir(parents=True)
            (app / 'docs').mkdir(parents=True)
            (app / 'logs').mkdir()
            (app / 'pse-split-appfoundry').mkdir()
            (app / 'pse-split-appfoundry' / 'app.py').write_text('obsolete')
            (app / 'docs' / 'local-index.html').write_text('obsolete')
            (app / 'docs' / 'local-app.js').write_text('obsolete')
            (app / 'docs' / 'pse-data.json').write_text('saved data')
            (app / 'writes.log.jsonl').write_text('saved audit')
            (app / 'logs' / 'server.log').write_text('saved log')
            (source / 'docs' / 'index.html').write_text('current UI')
            (source / 'docs' / 'pse-data.json').write_text('must not copy')
            with patch.multiple(install_local_service, ROOT=source, APP_ROOT=app):
                install_local_service.sync_app_copy()
            self.assertFalse((app / 'pse-split-appfoundry').exists())
            self.assertFalse((app / 'docs' / 'local-index.html').exists())
            self.assertFalse((app / 'docs' / 'local-app.js').exists())
            self.assertEqual((app / 'docs' / 'index.html').read_text(), 'current UI')
            self.assertEqual((app / 'docs' / 'pse-data.json').read_text(), 'saved data')
            self.assertEqual((app / 'writes.log.jsonl').read_text(), 'saved audit')
            self.assertEqual((app / 'logs' / 'server.log').read_text(), 'saved log')
