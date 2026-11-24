"""Optional hosted adapter checks, offline with Flask and DB operations mocked."""
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

try:
    spec = importlib.util.spec_from_file_location('pse_hosted_checks',Path(__file__).resolve().parents[1]/'pse-split-appfoundry/app.py')
    hosted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hosted)
except ModuleNotFoundError:
    hosted = None


@unittest.skipIf(hosted is None,'Optional hosted dependencies are not installed')
class HostedChecks(unittest.TestCase):
    def setUp(self):
        hosted.app.config.update(TESTING=True,SECRET_KEY='offline-test-only')
        self.client = hosted.app.test_client()

    def test_login_stores_and_sends_matching_state(self):
        with patch.object(hosted,'WRITE_CONFIGURED',True):
            response = self.client.get('/oauth/login',base_url='https://localhost')
        self.assertEqual(response.status_code,302)
        sent = parse_qs(urlsplit(response.location).query)['state'][0]
        with self.client.session_transaction() as session:
            self.assertEqual(session['oauth_state'],sent)

    def test_callback_requires_valid_stored_state(self):
        with patch.object(hosted.urllib.request,'urlopen') as outbound:
            response = self.client.get('/oauth/callback?code=fake',base_url='https://localhost')
            self.assertIn(b'Login state mismatch',response.data)
            outbound.assert_not_called()

    def test_matching_callback_consumes_state_without_network_when_code_missing(self):
        with self.client.session_transaction() as session: session['oauth_state']='expected'
        with patch.object(hosted.urllib.request,'urlopen') as outbound:
            response = self.client.get('/oauth/callback?state=expected',base_url='https://localhost')
            self.assertIn(b'No authorization code returned',response.data)
            outbound.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertNotIn('oauth_state',session)

    def test_callback_rejects_mismatch(self):
        with self.client.session_transaction() as session: session['oauth_state']='expected'
        with patch.object(hosted.urllib.request,'urlopen') as outbound:
            response = self.client.get('/oauth/callback?code=fake&state=wrong',base_url='https://localhost')
            self.assertIn(b'Login state mismatch',response.data)
            outbound.assert_not_called()

    def test_refresh_rejects_automatic_requests(self):
        with patch.object(hosted,'_require_login') as login:
            response = self.client.post('/api/refresh',json={'force':False})
            self.assertEqual(response.status_code,400)
            login.assert_not_called()

    def test_snapshot_operations_take_database_lock(self):
        from unittest.mock import MagicMock
        connection=MagicMock()
        engine=MagicMock()
        engine.begin.return_value.__enter__.return_value=connection
        with patch.object(hosted,'db',return_value=engine):
            with hosted.snapshot_operation(): pass
        self.assertIn('pg_advisory_xact_lock',str(connection.execute.call_args.args[0]))
