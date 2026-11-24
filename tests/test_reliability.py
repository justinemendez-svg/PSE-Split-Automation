"""Regression checks for partial failures, retries, batching and changing records."""
import copy
import io
import unittest
import urllib.error
from unittest.mock import patch
from test_workflow import SalesforceStub
from workflow import execute
import serve_local


class ManySalesforce:
    def __init__(self, count=2, blank=False, fail=None):
        self.items = {}
        for index in range(count):
            sf = SalesforceStub(blank=blank)
            sf.opp_id = f'006{index:015d}'
            sf.record['Id'] = sf.opp_id
            self.items[sf.opp_id] = sf
        self.fail = fail
        self.read_count = 0
        self.hook = lambda _: None
        self.split_resolutions = 0

    def query(self, sql):
        if ' FROM Opportunity WHERE ' in sql:
            self.read_count += 1
            self.hook(self)
        rows = []
        for key, sf in self.items.items():
            if "'" + key + "'" in sql:
                rows.extend(sf.query(sql))
        return rows

    def rest(self, method, path, body, headers=None):
        if method == 'PATCH':
            key = path.rsplit('/', 1)[-1]
            if self.fail == ('field_network', key): raise OSError('Example network failure')
            return self.items[key].rest(method, path, body, headers)
        result = []
        for row in body['records']:
            key = row['OpportunityId']
            if self.fail == ('malformed', row['attributes']['type']): return 200, []
            status, response = self.items[key].rest(method, path, {'records': [row]})
            result.extend(response)
            if self.fail == ('timeout_after_accept', row['attributes']['type']): raise TimeoutError('Example accepted response lost')
        return 200, result

    def split_type(self):
        self.split_resolutions += 1
        return 'OverlayId'

    def run(self, write=True):
        return execute(list(self.items), set(self.items), self.query, self.rest, self.split_type, write=write)


class ReliabilityTests(unittest.TestCase):
    def test_field_network_failure_does_not_abort_other_opportunities(self):
        sf = ManySalesforce(blank=True)
        first, second = sf.items
        sf.fail = ('field_network', first)
        result = sf.run()
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))
        self.assertTrue(sf.items[second].team)
        self.assertFalse(sf.items[first].team)

    def test_team_failure_does_not_stop_unrelated_split(self):
        sf = ManySalesforce()
        first, second = sf.items.values()
        first.fail = 'OpportunityTeamMember'
        second.team.add(second.rep)
        result = sf.run()
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))
        self.assertTrue(second.splits)
        self.assertFalse(first.splits)

    def test_malformed_collection_result_is_reported(self):
        sf = ManySalesforce()
        sf.fail = ('malformed', 'OpportunityTeamMember')
        result = sf.run()
        self.assertEqual(result['failed'], 2)
        self.assertEqual(result['succeeded'], 0)
        self.assertTrue(result['errors'])

    def test_timeout_after_success_is_reconciled_from_salesforce(self):
        sf = ManySalesforce(count=1)
        sf.fail = ('timeout_after_accept', 'OpportunitySplit')
        result = sf.run()
        self.assertEqual((result['succeeded'], result['failed']), (1, 0))
        writes = sum(len(item.writes) for item in sf.items.values())
        sf.run()
        self.assertEqual(sum(len(item.writes) for item in sf.items.values()), writes)

    def test_leaving_scope_mid_run_stops_writes_and_removes_stale_row(self):
        sf = ManySalesforce(count=1)
        def hook(fake):
            if fake.read_count == 2:
                next(iter(fake.items.values())).record['StageName'] = '02 - Confirm Need'
        sf.hook = hook
        result = sf.run()
        self.assertEqual(result['changed'], 1)
        self.assertFalse(next(iter(result['states'].values()))['inScope'])
        self.assertEqual(next(iter(sf.items.values())).writes, [])

    def test_missing_version_fails_only_affected_record(self):
        sf = ManySalesforce(blank=True)
        next(iter(sf.items.values())).record.pop('LastModifiedDate')
        result = sf.run()
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))

    def test_multiple_batches_resolve_overlay_type_once(self):
        sf = ManySalesforce(count=201)
        result = sf.run()
        self.assertEqual((result['succeeded'], result['failed']), (201, 0))
        self.assertEqual((result['teamAdded'], result['splitAdded']), (201, 201))
        self.assertEqual(sf.split_resolutions, 1)

    def test_preview_all_is_read_only(self):
        sf = ManySalesforce(count=201, blank=True)
        self.assertEqual(len(sf.run(write=False)['idsToAdd']), 201)
        self.assertFalse(any(item.writes for item in sf.items.values()))
        self.assertEqual(sf.split_resolutions, 0)

    def test_duplicate_requests_do_not_duplicate_writes(self):
        sf = ManySalesforce(count=1)
        key = next(iter(sf.items))
        result = execute([key,key], {key}, sf.query, sf.rest, sf.split_type, write=True)
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(len(sf.items[key].writes), 2)

    def test_concurrent_assignment_change_is_preserved(self):
        sf = SalesforceStub(blank=True)
        original = sf.rest
        def conditional(method, path, body, headers=None):
            if method == 'PATCH':
                sf.record['Partner_Sales_Rep__c'] = sf.rep
                sf.record['Partner_Sales_Rep__r'] = {'Name':'Changed user', 'IsActive':True}
                return 412, {'message':'Record changed'}
            return original(method,path,body,headers)
        result = execute([sf.opp_id],{sf.opp_id},sf.query,conditional,lambda:'Overlay',write=True)
        self.assertEqual(result['failed'],1)
        self.assertEqual(sf.record['Partner_Sales_Rep__c'],sf.rep)
        self.assertFalse(sf.team)


class LocalSessionTests(unittest.TestCase):
    def test_cli_login_failure_returns_runtime_error_instead_of_exit(self):
        with patch.object(serve_local,'sf_session',side_effect=SystemExit('expired')):
            with self.assertRaisesRegex(RuntimeError,'Salesforce login'):
                serve_local.refresh_session()

    def test_query_renews_expired_session_once(self):
        with patch.object(serve_local,'current_session',return_value=('old','https://example.invalid')), patch.object(serve_local,'refresh_session',return_value=('new','https://example.invalid')) as renew, patch.object(serve_local,'sf_query',side_effect=[RuntimeError('Salesforce query failed (401)'),[]]) as query:
            self.assertEqual(serve_local.query_current('SELECT Id FROM Opportunity LIMIT 1'),[])
            self.assertEqual(renew.call_count,1)
            self.assertEqual(query.call_args.args[1],'new')

    def test_rest_second_unauthorized_response_is_reported(self):
        def unauthorized(*args,**kwargs):
            raise urllib.error.HTTPError('https://example.invalid',401,'Unauthorized',None,io.BytesIO(b'[{"message":"Session expired"}]'))
        with patch.object(serve_local,'current_session',return_value=('fake','https://example.invalid')), patch.object(serve_local,'refresh_session'), patch.object(serve_local.urllib.request,'urlopen',side_effect=unauthorized) as request:
            status,result = serve_local.sf_rest('PATCH','/record',{},headers={'If-Unmodified-Since':'Tue, 24 Nov 2026 01:00:00 GMT'})
            self.assertEqual(status,401)
            self.assertIn('expired',result['message'])
            self.assertEqual(request.call_count,2)
            self.assertEqual(request.call_args.args[0].get_header('If-unmodified-since'),'Tue, 24 Nov 2026 01:00:00 GMT')
