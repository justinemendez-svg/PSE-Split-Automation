"""Local-only scope and field checks; no Salesforce calls."""
import copy
import unittest
from local_scope import in_local_scope, LOCAL_REP_FIELDS, local_rep_state, unique_opportunities, local_summary
from workflow import PARTNER_REL
from refresh import build_soql, classify
from salesforce_stub import SalesforceStub
from test_local_updates import DetailedSalesforce


class LocalScopeChecks(unittest.TestCase):
    def test_counts_unique_opportunities_including_15_and_18_character_ids(self):
        rows=[{'id':'006000000000001','updatePlan':{'status':'ready'}},
              {'id':'006000000000001AAA','updatePlan':{'status':'ready'}},
              {'id':'006000000000001','updatePlan':{'status':'ready'}},
              {'id':'006000000000002AAA','updatePlan':{'status':'complete'}},
              {'id':None}]
        self.assertEqual(len(unique_opportunities(rows)),2)
        self.assertEqual(unique_opportunities(rows)[0]['id'],'006000000000001AAA')
        self.assertEqual(local_summary(rows)['total'],2)
        self.assertEqual(local_summary(rows)['needsUpdate'],1)
        self.assertEqual(local_summary(rows)['tagged'],1)
        self.assertEqual(len(unique_opportunities([{'id':'00600000000000a'},{'id':'00600000000000A'}])),2)

    def test_exact_current_query(self):
        sql = build_soql()
        self.assertIn("Partner_Type__c <> 'Startup Program Partner'", sql)
        self.assertIn("Referring_Partner__r.Name NOT IN ('AWS Marketplace')", sql)
        self.assertNotIn('AND Type IN', sql)
        self.assertIn('ORDER BY CloseDate DESC', sql)

    def test_scope_uses_opportunity_partner_type_and_all_opportunity_types(self):
        record = SalesforceStub().record
        record['Type'] = 'Other'
        record[PARTNER_REL]['Type'] = 'Startup Program Partner'
        self.assertTrue(in_local_scope(record))
        record['Partner_Type__c'] = 'Startup Program Partner'
        self.assertFalse(in_local_scope(record))
        record['Partner_Type__c'] = None
        record[PARTNER_REL]['Name'] = 'AWS Marketplace'
        self.assertFalse(in_local_scope(record))

    def test_additional_fields_are_preserved_and_tagged_profile_stays_blank(self):
        record = SalesforceStub(blank=True).record
        record.update(Partner_Deal_Source__c='Referral', Deal_Type__c='Direct', Total_Booking_ARR_in_USD__c=1234.5)
        record[PARTNER_REL].update(Region__c='APAC')
        record[PARTNER_REL]['Owner']['Profile'] = {'Name':'Partner owner profile'}
        value = classify(record, [], [])
        self.assertEqual(value['partnerRegion'], 'APAC')
        self.assertEqual(value['partnerOwnerProfile'], 'Partner owner profile')
        self.assertIsNone(value['taggedPseProfile'])
        self.assertEqual(value['partnerDealSource'], 'Referral')
        self.assertEqual(value['dealType'], 'Direct')
        self.assertEqual(value['totalBookingArrUsd'], 1234.5)

    def test_current_write_recheck_uses_same_scope(self):
        sf = DetailedSalesforce(team_role=None, split_role=None)
        sf.record['Type'] = 'Other'
        self.assertEqual(sf.local_run(write=True)['succeeded'], 1)
        for field, value in [('Partner_Type__c', 'Startup Program Partner'), ('StageName', 'Open'), ('Non_Commissionable__c', True)]:
            sf = DetailedSalesforce(team_role=None, split_role=None)
            sf.record[field] = value
            sf.local_run(write=True)
            self.assertEqual(sf.writes, [])
        sf = DetailedSalesforce(team_role=None, split_role=None)
        sf.record[PARTNER_REL]['Name'] = 'AWS Marketplace'
        sf.local_run(write=True)
        self.assertEqual(sf.writes, [])
