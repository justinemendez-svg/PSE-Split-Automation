"""Offline hosted upload checks. No live Salesforce connection or writes."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

try:
    spec = importlib.util.spec_from_file_location('pse_hosted_checks', Path(__file__).resolve().parents[1]/'pse-split-appfoundry/app.py')
    hosted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hosted)
except ModuleNotFoundError:
    hosted = None

from snapshot import validate_snapshot
from refresh import export_snapshot


def sample():
    return {'generatedAt': '2026-11-24T00:00:00+00:00', 'config': {'scopeVersion': 4}, 'rows': [{
        'id':'006000000000001AAA', 'partnerId':'001000000000001AAA', 'name':'Example',
        'partnerRepId':'005000000000001AAA', 'stage':'08 - Closed', 'type':'Renewal',
        'closeDate':'2026-11-24', 'nonCommissionable':False, 'partnerRepActive':True,
        'partnerRepNeedsPopulate':False, 'partnerRepOnTeam':False, 'partnerRepOnSplit':True}]}


class SnapshotChecks(unittest.TestCase):
    def test_round_trip_export_omits_unknown_fields_and_recomputes_summary(self):
        value=sample(); value['rows'][0]['unrelated']='omit'; value['summary']={'total':999}
        with tempfile.TemporaryDirectory() as folder:
            path=export_snapshot(value,folder)
            result=json.loads(path.read_text())
            self.assertEqual(validate_snapshot(result),result)
            self.assertEqual(result['summary']['total'],1)
            self.assertNotIn('unrelated',result['rows'][0])
            self.assertEqual(len(list(Path(folder).iterdir())),1)

    def test_invalid_file_keeps_previous_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path=export_snapshot(sample(),folder)
            before=path.read_bytes()
            value=sample(); value['rows'][0]['partnerRepOnTeam']='false'
            with self.assertRaises(ValueError): export_snapshot(value,folder)
            self.assertEqual(path.read_bytes(),before)

    def test_export_has_exact_category_union_including_partial_memberships(self):
        value=sample()
        for n,team,split in ((2,True,False),(3,True,True)):
            row=copy.deepcopy(value['rows'][0]);row.update(id=f'00600000000000{n}AAA',partnerRepOnTeam=team,partnerRepOnSplit=split)
            value['rows'].append(row)
        with tempfile.TemporaryDirectory() as folder:
            saved=json.loads(export_snapshot(value,folder).read_text())
        self.assertEqual(len(saved['rows']),2)
        self.assertEqual(saved['summary']['total'],2)
        self.assertEqual(len(value['rows']),3)

    def test_rejects_wrong_scope_duplicate_ids_missing_checks_and_bad_dates(self):
        variants=[]
        value=sample(); value['config']['scopeVersion']=1; variants.append(value)
        value=sample(); value['rows']*=2; variants.append(value)
        for key,item in [('stage','07 - Won'),('nonCommissionable',True),('type','Other'),('partnerId',None),('id','bad'),('closeDate','invalid'),('partnerRepOnSplit',None),('amount',float('nan'))]:
            value=sample(); value['rows'][0][key]=item; variants.append(value)
        value=sample(); value['generatedAt']='invalid'; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError): validate_snapshot(value)

    def test_empty_export_and_unavailable_rep_are_supported(self):
        value=sample(); value['rows']=[]
        self.assertEqual(validate_snapshot(value)['summary']['total'],0)
        value=sample(); value['rows'][0].update(partnerRepId=None,partnerRepActive=None,partnerRepNeedsPopulate=True)
        self.assertIsNone(validate_snapshot(value)['rows'][0]['partnerRepId'])


@unittest.skipIf(hosted is None,'Optional hosted dependencies are not installed')
class HostedChecks(unittest.TestCase):
    def setUp(self):
        hosted.app.config['TESTING']=True
        self.client=hosted.app.test_client()

    def test_upload_persists_and_reloads_without_salesforce(self):
        saved={}
        with patch.object(hosted,'_save_snapshot',side_effect=lambda payload:saved.update(payload)), patch.object(hosted,'_load_snapshot',side_effect=lambda:saved):
            result=self.client.post('/api/upload',json={'manual':True,'snapshot':sample()})
            self.assertEqual(result.status_code,200)
            self.assertIn('uploadedAt',result.json['snapshot'])
            with hosted.app.test_client() as reopened:
                self.assertEqual(reopened.get('/pse-data.json').json,result.json['snapshot'])

    def test_rejected_uploads_never_replace_snapshot(self):
        with patch.object(hosted,'_save_snapshot') as save:
            for body in ([],{}, {'snapshot':sample()}, {'manual':True,'snapshot':{'rows':[]}}):
                self.assertEqual(self.client.post('/api/upload',json=body).status_code,400)
            self.assertEqual(self.client.post('/api/upload',data='broken',content_type='application/json').status_code,400)
            save.assert_not_called()

    def test_upload_size_limit(self):
        with patch.object(hosted,'_save_snapshot') as save:
            result=self.client.post('/api/upload',data=' '*(hosted.MAX_UPLOAD_BYTES+2048),content_type='application/json')
            self.assertEqual(result.status_code,413); save.assert_not_called()

    def test_health_exposes_only_uploads_and_open_never_loads_salesforce(self):
        with patch.object(hosted,'db') as database:
            self.assertEqual(self.client.get('/').status_code,200)
            result=self.client.get('/api/health').json
            self.assertTrue(result['uploadEnabled']); self.assertFalse(result['refreshEnabled']); self.assertFalse(result['writeEnabled'])
            database.assert_not_called()
        self.assertEqual(self.client.get('/').headers['Cache-Control'],'no-store')

    def test_old_oauth_refresh_and_update_routes_are_disabled(self):
        for path in ('/api/refresh','/api/write','/api/write-all','/api/preview','/api/preview-all'):
            self.assertEqual(self.client.post(path,json={'manual':True,'confirm':'CONFIRM'}).status_code,410)
        self.assertEqual(self.client.get('/oauth/login').status_code,410)

    def test_upload_save_uses_transaction_lock(self):
        engine=MagicMock(); connection=engine.begin.return_value.__enter__.return_value
        with patch.object(hosted,'db',return_value=engine): hosted._save_snapshot(validate_snapshot(sample()))
        self.assertIn('pg_advisory_xact_lock',str(connection.execute.call_args_list[0].args[0]))
        self.assertIn('ON CONFLICT',str(connection.execute.call_args_list[1].args[0]))

    def test_database_variables_support_special_password_and_url_override(self):
        from sqlalchemy.engine import make_url
        environment={'DB_HOST':'db.invalid','DB_PORT':'5432','DB_NAME':'example','DB_USER':'user','DB_PASSWORD':'test:@/password%'}
        with patch.dict(hosted.os.environ,environment,clear=True): self.assertEqual(make_url(hosted._database_url()).password,environment['DB_PASSWORD'])
        with patch.dict(hosted.os.environ,{'DATABASE_URL':'postgres://u:p@db.invalid/db'},clear=True): self.assertEqual(hosted._database_url(),'postgresql://u:p@db.invalid/db')
        with patch.dict(hosted.os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'Postgres is required'): hosted._database_url()

    def test_standalone_bundle_contains_exact_ui_and_validator(self):
        import shutil,sys
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules):
            target=Path(folder)/'app.py'; shutil.copy2(root/'pse-split-appfoundry/standalone_app.py',target)
            spec=importlib.util.spec_from_file_location('pse_isolated',target); bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
            self.assertEqual(bundle.INDEX_HTML,(root/'docs/index.html').read_text())
            self.assertEqual(bundle.APP_JS,(root/'docs/app.js').read_text())
            self.assertEqual(bundle.validate_snapshot(sample())['summary']['total'],1)
