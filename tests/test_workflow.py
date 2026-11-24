"""Offline workflow checks. No Salesforce connection or records are used."""
import copy
import re
import unittest
from workflow import execute, PARTNER_FIELD, PARTNER_REL, is_complete


class SalesforceStub:
    def __init__(self, blank=False, team=False, split=False, active=True, fail=None):
        self.rep = '005000000000001AAA'
        self.owner = '005000000000002AAA'
        self.opp_id = '006000000000001AAA'
        self.record = {'Id': self.opp_id, 'Name': 'Example', 'StageName': '08 - Closed', 'Type': 'Renewal',
                       'Non_Commissionable__c': False, PARTNER_FIELD: '001000000000001AAA',
                       PARTNER_REL: {'Name': 'Partner', 'OwnerId': self.owner,
                                     'Owner': {'Name': 'Owner', 'IsActive': active}},
                       'Partner_Sales_Rep__c': None if blank else self.rep,
                       'Partner_Sales_Rep__r': None if blank else {'Name': 'Existing', 'IsActive': active},
                       'LastModifiedDate': '2026-11-24T01:00:00.000+0000'}
        chosen = self.owner if blank else self.rep
        self.team = {chosen} if team else set()
        self.splits = {chosen} if split else set()
        self.writes = []
        self.fail = fail

    def query(self, sql):
        if ' FROM Opportunity WHERE ' in sql: return [copy.deepcopy(self.record)]
        if ' FROM OpportunityTeamMember ' in sql:
            return [{'OpportunityId': self.opp_id, 'UserId': value} for value in self.team]
        if ' FROM OpportunitySplit ' in sql:
            # An existing non-Overlay split is sufficient; no type filter is permitted.
            assert 'SplitTypeId =' not in sql
            return [{'OpportunityId': self.opp_id, 'SplitOwnerId': value} for value in self.splits]
        raise AssertionError(sql)

    def rest(self, method, path, body, headers=None):
        if method == 'PATCH':
            assert self.record['Partner_Sales_Rep__c'] is None
            assert headers['If-Unmodified-Since']
            self.writes.append(('Opportunity', body))
            if self.fail == 'Opportunity': return 412, {'message': 'Record changed'}
            self.record['Partner_Sales_Rep__c'] = body['Partner_Sales_Rep__c']
            self.record['Partner_Sales_Rep__r'] = {'Name': 'Owner', 'IsActive': True}
            return 204, {}
        result = []
        for row in body['records']:
            kind = row['attributes']['type']
            self.writes.append((kind, row))
            if self.fail == kind:
                result.append({'success': False, 'errors': ['Example validation error']})
                continue
            if kind == 'OpportunityTeamMember':
                assert row['TeamMemberRole'] == 'Partner Account Executive'
                assert row['OpportunityAccessLevel'] == 'Read'
                self.team.add(row['UserId'])
            else:
                assert row['SplitOwnerId'] in self.team
                assert row['Opportunity_Team_Role__c'] == 'Partner Account Executive'
                assert row['SplitPercentage'] == 100
                self.splits.add(row['SplitOwnerId'])
            result.append({'success': True})
        return 200, result

    def run(self, write=True):
        return execute([self.opp_id], {self.opp_id}, self.query, self.rest, lambda: 'OverlayId', write=write)


class WorkflowTests(unittest.TestCase):
    def test_blank_populates_then_team_then_split(self):
        sf = SalesforceStub(blank=True)
        result = sf.run()
        self.assertEqual([kind for kind, _ in sf.writes], ['Opportunity', 'OpportunityTeamMember', 'OpportunitySplit'])
        self.assertEqual(sf.record['Partner_Sales_Rep__c'], sf.owner)
        self.assertTrue(is_complete(result['states'][sf.opp_id]))

    def test_preserves_existing_rep_even_when_partner_owner_differs(self):
        sf = SalesforceStub()
        sf.run()
        self.assertEqual(sf.record['Partner_Sales_Rep__c'], sf.rep)
        self.assertEqual(sf.team, {sf.rep})
        self.assertEqual(sf.splits, {sf.rep})

    def test_team_present_still_adds_split(self):
        sf = SalesforceStub(team=True)
        sf.run()
        self.assertEqual([kind for kind, _ in sf.writes], ['OpportunitySplit'])

    def test_any_existing_split_preserved(self):
        sf = SalesforceStub(split=True)
        sf.run()
        self.assertEqual([kind for kind, _ in sf.writes], ['OpportunityTeamMember'])

    def test_field_blank_requires_update_even_when_memberships_exist(self):
        sf = SalesforceStub(blank=True, team=True, split=True)
        sf.run()
        self.assertEqual([kind for kind, _ in sf.writes], ['Opportunity'])

    def test_preview_is_read_only_and_retry_is_idempotent(self):
        sf = SalesforceStub(blank=True)
        self.assertEqual(sf.run(write=False)['idsToAdd'], [sf.opp_id])
        self.assertEqual(sf.writes, [])
        sf.run()
        writes = len(sf.writes)
        sf.run()
        self.assertEqual(len(sf.writes), writes)

    def test_conditional_field_failure_stops_later_stages(self):
        sf = SalesforceStub(blank=True, fail='Opportunity')
        self.assertEqual(sf.run()['failed'], 1)
        self.assertEqual([kind for kind, _ in sf.writes], ['Opportunity'])

    def test_team_failure_stops_split_and_retry_continues(self):
        sf = SalesforceStub(blank=True, fail='OpportunityTeamMember')
        self.assertEqual(sf.run()['failed'], 1)
        self.assertEqual([kind for kind, _ in sf.writes], ['Opportunity', 'OpportunityTeamMember'])
        sf.fail = None
        self.assertEqual(sf.run()['succeeded'], 1)
        self.assertEqual(sum(kind == 'Opportunity' for kind, _ in sf.writes), 1)

    def test_scope_requires_partner_commissionable_and_allowed_type(self):
        for key, value in ((PARTNER_FIELD, None), ('Non_Commissionable__c', True), ('Type', 'Other'), ('StageName', '07 - Closing'), ('StageName', 'Closed Lost'), ('StageName', '08 - Closed ')):
            sf = SalesforceStub()
            sf.record[key] = value
            self.assertEqual(sf.run()['changed'], 1)
            self.assertEqual(sf.writes, [])

    def test_inactive_existing_rep_is_preserved_and_not_filtered(self):
        sf = SalesforceStub(active=False)
        preview = sf.run(write=False)
        self.assertEqual(preview['idsToAdd'], [sf.opp_id])
        self.assertFalse(preview['states'][sf.opp_id]['partnerRepActive'])

    def test_unavailable_owner_is_reported_without_writes(self):
        sf = SalesforceStub(blank=True)
        sf.record[PARTNER_REL]['OwnerId'] = None
        result = sf.run()
        self.assertEqual(result['failed'], 1)
        self.assertTrue(result['errors'])
        self.assertEqual(sf.writes, [])


if __name__ == '__main__': unittest.main()
