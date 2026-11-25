"""Local-only scope and field checks; no Salesforce calls."""
import copy
import datetime as dt
import unittest
from local_scope import in_local_scope, LOCAL_REP_FIELDS, local_rep_state, unique_opportunities, local_summary, fiscal_window, in_fiscal_window, scope_snapshot
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

    def test_rolling_two_quarters_across_fiscal_year(self):
        cases = [
            ('2026-11-25', '2026-08-01', '2027-02-01'),
            ('2027-01-31', '2026-08-01', '2027-02-01'),
            ('2027-02-01', '2026-11-01', '2027-05-01'),
            ('2027-05-01', '2027-02-01', '2027-08-01'),
            ('2027-08-01', '2027-05-01', '2027-11-01'),
            ('2028-02-29', '2027-11-01', '2028-05-01'),
        ]
        for today, start, end in cases:
            as_of = dt.date.fromisoformat(today)
            with self.subTest(today=today):
                self.assertEqual(tuple(item.isoformat() for item in fiscal_window(as_of)), (start, end))
                self.assertTrue(in_fiscal_window(start, as_of))
                self.assertTrue(in_fiscal_window((dt.date.fromisoformat(end)-dt.timedelta(days=1)).isoformat(), as_of))
                self.assertFalse(in_fiscal_window(end, as_of))
                self.assertFalse(in_fiscal_window((dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat(), as_of))
                self.assertFalse(in_fiscal_window(None, as_of))
                self.assertFalse(in_fiscal_window('2027-02-30', as_of))
                sql = build_soql(as_of)
                self.assertIn('CloseDate >= '+start, sql)
                self.assertIn('CloseDate < '+end, sql)

    def test_saved_snapshot_is_narrowed_without_changing_extraction_time(self):
        rows = [{'id': '00600000000000'+str(n), 'closeDate': date, 'updatePlan': {'status': 'ready'}}
                for n, date in enumerate(['2026-07-31', '2026-08-01', '2027-01-31', '2027-02-01'])]
        original = {'rows': rows, 'generatedAt': 'saved extraction', 'config': {'scopeVersion': 6}}
        scoped = scope_snapshot(original, dt.date(2026, 11, 25))
        self.assertEqual([row['closeDate'] for row in scoped['rows']], ['2026-08-01', '2027-01-31'])
        self.assertEqual(scoped['summary']['total'], 2)
        self.assertEqual(scoped['generatedAt'], original['generatedAt'])
        self.assertEqual(len(original['rows']), 4)

    def test_stale_opportunity_cannot_be_updated(self):
        for date in [fiscal_window()[0]-dt.timedelta(days=1), fiscal_window()[1]]:
            sf = DetailedSalesforce(team_role=None, split_role=None)
            sf.record['CloseDate'] = date.isoformat()
            result = sf.local_run(write=True)
            self.assertEqual(sf.writes, [])
            self.assertEqual(result['idsToAdd'], [])
            self.assertFalse(result['states'][sf.opp_id]['inScope'])
