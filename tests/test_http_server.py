"""Local HTTP adapter contract checks with all Salesforce operations mocked."""
import json
import datetime as dt
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
import serve_local
from local_scope import fiscal_window


class QuietHandler(serve_local.Handler):
    def log_message(self,*args): pass


class HTTPChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='pse-http-checks-')
        cls.root=Path(cls.temp.name)
        cls.opp='006000000000001AAA'
        cls.snapshot={'config':{'scopeVersion':6},'summary':{'total':1},'rows':[{'id':cls.opp,'name':'Offline example','stage':'08 - Closed','closeDate':fiscal_window()[0].isoformat(),'type':'Renewal','nonCommissionable':False,'partnerType':None,'partnerId':'001000000000001AAA','partnerRepOnTeam':False,'partnerRepOnSplit':False}]}
        (cls.root/'index.html').write_text('<h1>Offline dashboard</h1>')
        (cls.root/'pse-data.json').write_text(json.dumps(cls.snapshot))
        cls.paths=patch.multiple(serve_local,DOCS=cls.root,DATA=cls.root/'pse-data.json')
        cls.paths.start()
        cls.server=serve_local.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.paths.stop(); cls.temp.cleanup()

    def request(self,path,payload=None,raw=None):
        body=raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        request=urllib.request.Request(self.url+path,data=body,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=5) as response: return response.status,response.read()
        except urllib.error.HTTPError as error: return error.code,error.read()

    def test_get_dashboard_and_snapshot_does_not_refresh(self):
        with patch.object(serve_local.subprocess,'run') as command:
            self.assertEqual(self.request('/')[0],200)
            self.assertEqual(self.request('/pse-data.json')[0],200)
            self.assertEqual(self.request('/api/health')[0],200)
            command.assert_not_called()

    def test_missing_manual_intent_rejected(self):
        with patch.object(serve_local.subprocess,'run') as command:
            self.assertEqual(self.request('/api/refresh',{'force':False})[0],400)
            command.assert_not_called()

    def test_review_requires_explicit_assignment_confirmation(self):
        for payload in ({},{'oppId':self.opp,'userId':'005000000000001AAA','role':'Partner Account Executive'}):
            status,_=self.request('/api/review',payload)
            self.assertEqual(status,400)
        self.assertEqual(self.request('/api/users',{'search':'a'})[0],400)

    def test_manual_refresh_runs_once(self):
        from types import SimpleNamespace
        with patch.object(serve_local.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as command:
            status,body=self.request('/api/refresh',{'manual':True})
            self.assertEqual(status,200)
            self.assertTrue(json.loads(body)['ok'])
            self.assertEqual(command.call_count,1)

    def test_update_routes_require_confirmation(self):
        with patch.object(serve_local,'recheck_and_write') as write:
            self.assertEqual(self.request('/api/write',{'oppId':self.opp})[0],400)
            self.assertEqual(self.request('/api/write-all',{'oppIds':[self.opp]})[0],400)
            write.assert_not_called()

    def test_single_update_passes_only_selected_id(self):
        value={'partnerRepNeedsPopulate':False,'partnerRepOnTeam':True,'partnerRepOnSplit':True}
        with patch.object(serve_local,'recheck_and_write',return_value={'states':{self.opp:value},'errors':[]}) as write:
            status,body=self.request('/api/write',{'oppId':self.opp,'confirm':'CONFIRM'})
            self.assertEqual(status,200)
            self.assertTrue(json.loads(body)['done'])
            write.assert_called_once_with([self.opp])

    def test_bulk_update_passes_only_requested_ids(self):
        with patch.object(serve_local,'recheck_and_write',return_value={'succeeded':1}) as write:
            status,_=self.request('/api/write-all',{'oppIds':[self.opp,self.opp],'confirm':'CONFIRM'})
            self.assertEqual(status,200)
            write.assert_called_once_with([self.opp])

    def test_malformed_requests_return_json(self):
        for raw in (b'{',b'[]',b'"text"',b'\xff'):
            status,body=self.request('/api/refresh',raw=raw)
            self.assertEqual(status,400)
            self.assertIn('error',json.loads(body))

    def test_preview_network_error_returns_json(self):
        with patch.object(serve_local,'preview_missing_ids',side_effect=OSError('Example network failure')):
            status,body=self.request('/api/preview',{'oppId':self.opp})
            self.assertEqual(status,502)
            self.assertIn('network',json.loads(body)['error'])

    def test_saved_data_and_update_eligibility_are_limited_without_refresh(self):
        old = dict(self.snapshot['rows'][0], id='006000000000002AAA',
                   closeDate=(fiscal_window()[0]-dt.timedelta(days=1)).isoformat())
        data = {**self.snapshot, 'generatedAt': 'original extraction',
                'rows': [self.snapshot['rows'][0], old]}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'pse-data.json'
            original = json.dumps(data)
            target.write_text(original)
            with patch.object(serve_local, 'DATA', target), patch.object(serve_local.subprocess, 'run') as command:
                status, body = self.request('/pse-data.json')
                visible = json.loads(body)
                self.assertEqual(status, 200)
                self.assertEqual([row['id'] for row in visible['rows']], [self.opp])
                self.assertEqual(visible['summary']['total'], 1)
                self.assertEqual(visible['generatedAt'], 'original extraction')
                self.assertEqual(serve_local.allowed_missing_ids(), {self.opp})
                command.assert_not_called()
            self.assertEqual(target.read_text(), original)

    def test_set_inactive_requires_confirmation_and_never_writes_salesforce(self):
        with patch.object(serve_local, 'sf_rest') as write:
            status, _ = self.request('/api/review-inactive-pse', {'oppId': self.opp})
            self.assertEqual(status, 400)
            write.assert_not_called()

    def test_set_inactive_uses_identified_user_and_saves_local_choice_only(self):
        from test_local_updates import DetailedSalesforce
        sf = DetailedSalesforce(active=False, team_role=None, split_role=None)
        with patch.object(serve_local, 'refresh_session'), \
             patch.object(serve_local, 'allowed_missing_ids', return_value={sf.opp_id}), \
             patch.object(serve_local, 'query_current', side_effect=sf.query), \
             patch.object(serve_local, 'update_local_snapshot') as save, \
             patch.object(serve_local, 'audit'), \
             patch.object(serve_local, 'sf_rest') as write:
            status, body = self.request('/api/review-inactive-pse', {
                'oppId': sf.opp_id, 'confirmed': True, 'userId': sf.other})
            self.assertEqual(status, 200)
            value = json.loads(body)
            self.assertEqual(value['assignmentReview']['userId'], sf.rep)
            self.assertTrue(value['updatePlan']['retainedInactive'])
            self.assertEqual(value['updatePlan']['status'], 'ready')
            save.assert_called_once()
            write.assert_not_called()
            self.assertEqual(sf.writes, [])

    def test_coverage_role_review_requires_coverage_box_confirmation(self):
        with patch.object(serve_local, 'refresh_session') as login, patch.object(serve_local, 'sf_rest') as write:
            status, body = self.request('/api/review', {
                'oppId': self.opp, 'userId': '005000000000003AAA', 'confirmed': True,
                'rvpConfirmed': True, 'role': 'Partner Account Executive - Coverage'})
            self.assertEqual(status, 400)
            self.assertIn('Tick coverage box', json.loads(body)['error'])
            login.assert_not_called()
            write.assert_not_called()
