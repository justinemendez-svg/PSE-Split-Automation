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

    def test_appfoundry_injected_database_variables_and_special_password(self):
        from sqlalchemy.engine import make_url
        environment={'DB_HOST':'db.example.invalid','DB_PORT':'5432','DB_NAME':'example_db','DB_USER':'example_user','DB_PASSWORD':'test:@/password%'}
        with patch.dict(hosted.os.environ,environment,clear=True):
            parsed=make_url(hosted._database_url())
        self.assertEqual(parsed.host,environment['DB_HOST'])
        self.assertEqual(parsed.database,environment['DB_NAME'])
        self.assertEqual(parsed.username,environment['DB_USER'])
        self.assertEqual(parsed.password,environment['DB_PASSWORD'])

    def test_database_url_override_is_preserved(self):
        value='postgres://fake:fake@example.invalid/example'
        with patch.dict(hosted.os.environ,{'DATABASE_URL':value},clear=True):
            self.assertEqual(hosted._database_url(),value.replace('postgres://','postgresql://',1))

    def test_unconfigured_database_reports_requirement(self):
        with patch.dict(hosted.os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'Postgres is required'):
                hosted._database_url()

    def test_standalone_bundle_loads_without_repository_and_matches_ui(self):
        import tempfile,shutil,sys
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='pse-standalone-') as temp,patch.dict(sys.modules):
            target=Path(temp)/'main.py'
            shutil.copy2(root/'pse-split-appfoundry/standalone_app.py',target)
            spec=importlib.util.spec_from_file_location('pse_isolated_bundle',target)
            bundle=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bundle)
            self.assertEqual(bundle.INDEX_HTML,(root/'docs/index.html').read_text())
            self.assertEqual(bundle.APP_JS,(root/'docs/app.js').read_text())
            with bundle.app.test_client() as client:
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(client.get('/app.js').data.decode(),bundle.APP_JS)

    def test_manual_refresh_job_saves_snapshot_and_status(self):
        import json,threading
        from contextlib import contextmanager
        from test_workflow import SalesforceStub
        records=SalesforceStub(blank=True).record
        store={'jobs':{},'snapshot':None}
        class Result:
            def __init__(self,row=None): self.row=row
            def mappings(self): return self
            def first(self): return self.row
        class Database:
            @contextmanager
            def begin(self): yield self
            def execute(self,statement,params=None):
                sql=str(statement); params=params or {}
                if 'pg_advisory_xact_lock' in sql: return Result()
                if 'INSERT INTO pse_split_jobs' in sql:
                    store['jobs'][params['id']]={'status':'queued','message':'queued','result':None,'sid':params['sid']}
                elif 'UPDATE pse_split_jobs' in sql:
                    store['jobs'][params['id']].update({key:params[key] for key in ('status','message','result')})
                elif 'SELECT status, message, result FROM pse_split_jobs' in sql:
                    row=store['jobs'].get(params['id'])
                    return Result(row if row and row['sid']==params['sid'] else None)
                elif 'INSERT INTO pse_split_snapshot' in sql:
                    store['snapshot']=params['payload']
                elif 'SELECT payload FROM pse_split_snapshot' in sql:
                    return Result((store['snapshot'],) if store['snapshot'] else None)
                else: raise AssertionError(sql)
                return Result()
        def query(session_id,sql,label='records'):
            if 'SELECT SplitTypeId FROM OpportunitySplit' in sql: return [{'SplitTypeId':'OverlayId'}]
            if 'FROM Opportunity WHERE' in sql: return [records]
            if 'FROM OpportunityTeamMember' in sql or 'FROM OpportunitySplit' in sql: return []
            raise AssertionError(sql)
        finished=threading.Event()
        original=hosted._run_refresh
        def run_job(*args):
            try: original(*args)
            finally: finished.set()
        with self.client.session_transaction() as session: session['sf_session_id']='offline-session'
        with patch.object(hosted,'db',return_value=Database()),patch.object(hosted,'_session_record',return_value={'instance_url':'https://example.invalid'}),patch.object(hosted,'sf_query',side_effect=query) as queries,patch.object(hosted,'sf_request') as writes,patch.object(hosted,'_run_refresh',side_effect=run_job):
            response=self.client.post('/api/refresh',json={'manual':True},base_url='https://localhost')
            self.assertEqual(response.status_code,202)
            self.assertTrue(finished.wait(timeout=5))
            job_id=response.get_json()['jobId']
            response=self.client.get('/api/refresh-status?jobId='+job_id,base_url='https://localhost')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.get_json()['status'],'complete')
            before=queries.call_count
            snapshot=self.client.get('/pse-data.json',base_url='https://localhost').get_json()
            self.assertEqual(snapshot['summary']['total'],1)
            self.assertTrue(snapshot['rows'][0]['partnerRepNeedsPopulate'])
            self.assertEqual(snapshot['config']['stage'],'08 - Closed')
            self.assertEqual(queries.call_count,before)
            writes.assert_not_called()
