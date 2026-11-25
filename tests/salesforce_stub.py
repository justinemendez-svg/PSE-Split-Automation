"""Offline workflow checks. No Salesforce connection or records are used."""
import copy
import re
import unittest
from workflow import PARTNER_FIELD, PARTNER_REL


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
