"""Offline hosted contract checks; no database or Salesforce records accessed."""
import copy
import datetime as dt
import sys
import types
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import build

hosted = types.ModuleType('hosted_test')
exec(compile(build(), '<hosted-build>', 'exec'), hosted.__dict__)


def snapshot():
    from local_scope import fiscal_window
    return {'generatedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
            'config': {'scopeVersion': 6}, 'rows': [{
                'id': '006000000000001AAA', 'name': 'Synthetic Opportunity',
                'stage': '08 - Closed', 'nonCommissionable': False,
                'closeDate': fiscal_window()[0].isoformat(), 'type': 'Other',
                'partnerId': '001000000000001AAA', 'partnerType': None,
                'partnerName': 'Synthetic Partner', 'partnerRegion': 'APAC',
                'partnerOwnerId': '005000000000001AAA', 'partnerOwnerName': 'Synthetic PSE',
                'partnerRepId': '005000000000001AAA', 'partnerRepName': 'Synthetic PSE',
                'partnerRepActive': True, 'partnerRepNeedsPopulate': False,
                'membershipDetailsLoaded': True, 'teamEntries': [], 'splitEntries': []}]}


class HostedChecks(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.current = snapshot()
        hosted._load_snapshot = lambda: copy.deepcopy(self.current)
        hosted._save_snapshot = lambda value: self.saved.append(copy.deepcopy(value))
        self.client = hosted.app.test_client()

    def test_load_does_not_refresh_write_or_replace_data(self):
        health = self.client.get('/api/health').json
        self.assertTrue(health['uploadEnabled'])
        self.assertFalse(health['refreshEnabled'])
        self.assertFalse(health['writeEnabled'])
        value = self.client.get('/pse-data.json').json
        self.assertEqual(value['rows'][0]['updatePlan']['status'], 'ready')
        self.assertEqual(value['rows'][0]['type'], 'Other')
        self.assertEqual(self.saved, [])

    def test_manual_upload_revalidates_and_preserves_fields(self):
        result = self.client.post('/api/upload', json={'manual': True, 'snapshot': snapshot()})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(result.json['snapshot']['rows'][0]['partnerRegion'], 'APAC')

    def test_invalid_upload_keeps_previous_snapshot(self):
        for field, value in [('partnerType', 'Startup Program Partner'),
                             ('partnerName', 'AWS Marketplace'), ('stage', '07 - Lost'),
                             ('closeDate', '2020-01-01'), ('membershipDetailsLoaded', False)]:
            bad = snapshot()
            bad['rows'][0][field] = value
            self.assertEqual(self.client.post('/api/upload', json={'manual': True, 'snapshot': bad}).status_code, 400)
        duplicate = snapshot()
        duplicate['rows'].append({**duplicate['rows'][0], 'id': '006000000000001'})
        self.assertEqual(self.client.post('/api/upload', json={'manual': True, 'snapshot': duplicate}).status_code, 400)
        self.assertEqual(self.saved, [])

    def test_old_snapshot_requests_upload_without_overwriting_database(self):
        self.current['config']['scopeVersion'] = 4
        self.assertEqual(self.client.get('/pse-data.json').status_code, 409)
        self.assertEqual(self.saved, [])

    def test_no_write_or_review_endpoints(self):
        for path in ('refresh', 'write', 'write-all', 'preview', 'preview-all', 'review', 'review-inactive-pse', 'users'):
            self.assertEqual(self.client.post('/api/' + path, json={'confirm': 'CONFIRM'}).status_code, 410)
        self.assertEqual(self.saved, [])

    def test_laptop_review_not_used_as_shared_approval(self):
        self.current['rows'][0]['partnerRepActive'] = False
        self.current['rows'][0]['assignmentReview'] = {'confirmed': True, 'active': True}
        row = self.client.get('/pse-data.json').json['rows'][0]
        self.assertNotIn('assignmentReview', row)
        self.assertEqual(row['updatePlan']['status'], 'rvp')

    def test_ui_assets_and_upload_javascript_compile(self):
        import re
        import subprocess
        import tempfile
        html = self.client.get('/').text
        js = self.client.get('/app.js').text
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        for ident in re.findall(r'\$\("([A-Za-z][A-Za-z0-9]+)"\)', js):
            self.assertIn(ident, ids)
        self.assertIn('monthFilter', ids)
        self.assertIn('cDifferentPse', ids)
        self.assertIn('uploadPage', ids)
        self.assertIn('Connect to Salesforce', html)
        self.assertNotIn('Keep existing type and percentage', html + js)
        with tempfile.NamedTemporaryFile(suffix='.js', mode='w') as file:
            file.write(js)
            file.flush()
            subprocess.run(['node', '--check', file.name], check=True, capture_output=True)


class LiveChecks(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet
        self.environment = patch.dict(hosted.os.environ, {
            'SF_CLIENT_ID': 'synthetic-client', 'SF_CLIENT_SECRET': 'synthetic-secret',
            'SF_EXPECTED_ORG_ID': '00D000000000001AAA',
            'PSE_SESSION_SECRET': 'synthetic-session-secret-at-least-32-chars',
            'PSE_TOKEN_KEY': Fernet.generate_key().decode()})
        self.environment.start()
        self.old_secret = hosted.app.secret_key
        hosted.app.secret_key = 'synthetic-session-secret-at-least-32-chars'
        self.client = hosted.app.test_client()
        self.saved = []
        self.records = {}
        self.audit = []
        class Connection:
            def execute(inner, *args, **kwargs):
                return None
        class Database:
            def begin(inner):
                return nullcontext(Connection())
        self.patches = [patch.object(hosted, 'live_db', return_value=Database()),
                        patch.object(hosted, 'user_record', side_effect=lambda cx, lock=False: self.records.get(hosted.session_key())),
                        patch.object(hosted, 'save_user_snapshot', side_effect=lambda cx, data: self.saved.append(copy.deepcopy(data))),
                        patch.object(hosted, 'audit_live', side_effect=lambda actor, event: self.audit.append((actor, event)))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        hosted.app.secret_key = self.old_secret
        self.environment.stop()

    def connect(self, data=None):
        with self.client.session_transaction() as sess:
            sess['sid'] = 'synthetic-session'
            sess['csrf'] = 'synthetic-csrf'
        key = hosted.hashlib.sha256(b'synthetic-session').hexdigest()
        self.records[key] = ('encrypted-test-placeholder', hosted.json.dumps(data or snapshot()))

    def post(self, path, body, csrf=True):
        return self.client.post(path, json=body, base_url='https://localhost',
                                headers={'Origin': hosted.PUBLIC_ORIGIN, **({'X-PSE-CSRF': 'synthetic-csrf'} if csrf else {})})

    def test_connected_health_never_fetches_salesforce(self):
        self.connect()
        with patch.object(hosted, 'SalesforceClient', side_effect=AssertionError('No Salesforce call allowed')):
            health = self.client.get('/api/health', base_url='https://localhost').json
            self.assertTrue(health['writeEnabled'])
            self.assertTrue(health['refreshEnabled'])
            self.assertEqual(self.client.get('/pse-data.json', base_url='https://localhost').status_code, 200)
        self.assertEqual(self.saved, [])

    def test_guest_never_receives_another_users_snapshot(self):
        self.connect()
        other = hosted.app.test_client()
        self.assertFalse(other.get('/api/health', base_url='https://localhost').json['loggedIn'])
        self.assertEqual(other.post('/api/refresh', json={'manual': True}, base_url='https://localhost').status_code, 401)

    def test_csrf_and_cross_origin_requests_rejected(self):
        self.connect()
        self.assertEqual(self.post('/api/write', {'confirm': 'CONFIRM'}, csrf=False).status_code, 403)
        self.assertEqual(self.client.post('/api/upload', json={}, headers={'Origin': 'https://other.example'}).status_code, 403)

    def test_oauth_uses_pkce_and_rejects_wrong_state(self):
        response = self.client.get('/oauth/login', base_url='https://localhost')
        self.assertEqual(response.status_code, 302)
        fields = hosted.urllib.parse.parse_qs(hosted.urllib.parse.urlsplit(response.location).query)
        self.assertEqual(fields['code_challenge_method'], ['S256'])
        self.assertEqual(fields['redirect_uri'], [hosted.CALLBACK_URL])
        with patch.object(hosted, 'oauth_token', side_effect=AssertionError('Token exchange must not run')):
            self.assertEqual(self.client.get('/oauth/callback?state=wrong&code=synthetic', base_url='https://localhost').status_code, 400)

    def test_only_expected_org_and_salesforce_instance_allowed(self):
        with self.assertRaises(hosted.LiveError):
            hosted.oauth_identity({'id': 'https://login.salesforce.com/id/00D000000000002AAA/005000000000001AAA'})
        for value in ('http://example.com', 'https://salesforce.com.evil.example', 'https://evil.example', 'https://x.salesforce.com/path'):
            with self.assertRaises(hosted.LiveError):
                hosted.sf_instance(value)

    def test_update_requires_confirmation_and_reuses_rechecked_workflow(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
        from test_local_updates import DetailedSalesforce
        from local_scope import local_rep_state
        sf = DetailedSalesforce(team_role=None, split_role=None)
        data = snapshot()
        data['rows'][0].update(id=sf.opp_id, **local_rep_state(sf.record, sf.members, sf.entries))
        data['rows'][0]['membershipDetailsLoaded'] = True
        self.connect(data)
        sf.actor = 'synthetic-user'
        sf.split_type = lambda: 'OverlayId'
        with patch.object(hosted, 'SalesforceClient', return_value=sf):
            refused = self.post('/api/write', {'oppId': sf.opp_id})
            self.assertEqual(refused.status_code, 400)
            self.assertEqual(sf.writes, [])
            preview = self.post('/api/preview', {'oppId': sf.opp_id})
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview.json['eligible'])
            self.assertEqual(sf.writes, [])
            done = self.post('/api/write', {'oppId': sf.opp_id, 'confirm': 'CONFIRM'})
            self.assertEqual(done.status_code, 200)
            self.assertTrue(done.json['done'])
            self.assertEqual([kind for kind, _ in sf.writes], ['OpportunityTeamMember', 'OpportunitySplit'])
            self.assertEqual([event['type'] for _, event in self.audit], ['update_started', 'update_finished'])

    def test_no_refresh_without_explicit_click(self):
        self.connect()
        with patch.object(hosted, 'SalesforceClient'), patch.object(hosted, 'collect_snapshot') as collect:
            self.assertEqual(self.post('/api/refresh', {}).status_code, 400)
            collect.assert_not_called()
            collect.return_value = hosted.scope_snapshot(snapshot())
            self.assertEqual(self.post('/api/refresh', {'manual': True}).status_code, 200)
            collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
