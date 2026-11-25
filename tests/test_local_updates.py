import copy
import unittest
from local_updates import execute_local, read_current, TEAM_ROLE, COVERAGE_ROLE
from salesforce_stub import SalesforceStub
from workflow import PARTNER_REL


class DetailedSalesforce(SalesforceStub):
    def __init__(self, blank=False, active=True, team_role=TEAM_ROLE, split_role=TEAM_ROLE, fail_team=False):
        super().__init__(blank=blank, active=active)
        self.owner=self.rep
        self.record[PARTNER_REL]['OwnerId']=self.rep
        self.record[PARTNER_REL]['Owner']['Name']='Existing'
        self.other='005000000000003AAA'
        self.users={self.rep:active,self.other:True}
        self.members=[] if team_role is None else [{'Id':'00q000000000001AAA','OpportunityId':self.opp_id,'UserId':self.rep,'User':{'Name':'Existing'},'TeamMemberRole':team_role,'OpportunityAccessLevel':'Edit'}]
        self.entries=[] if split_role is None else [{'Id':'049000000000001AAA','OpportunityId':self.opp_id,'SplitOwnerId':self.rep,'SplitOwner':{'Name':'Existing'},'SplitType':{'MasterLabel':'Overlay'},'Opportunity_Team_Role__c':split_role,'SplitPercentage':37}]
        self.fail_team=fail_team

    def query(self,sql):
        if ' FROM User ' in sql:return [{'Id':key,'Name':'Existing' if key==self.rep else 'Covering','IsActive':active} for key,active in self.users.items()]
        if ' FROM OpportunityTeamMember ' in sql:return copy.deepcopy(self.members)
        if ' FROM OpportunitySplit ' in sql:return copy.deepcopy(self.entries)
        return super().query(sql)

    def rest(self,method,path,body,headers=None):
        if '/sobjects/Opportunity/' in path:
            self.writes.append(('Opportunity',body))
            chosen=body['Partner_Sales_Rep__c']
            self.record['Partner_Sales_Rep__c']=chosen
            self.record['Partner_Sales_Rep__r']={'Name':'Existing' if chosen==self.rep else 'Covering','IsActive':self.users[chosen]}
            return 204,{}
        result=[]
        for item in body['records']:
            kind=item['attributes']['type'];self.writes.append((kind,copy.deepcopy(item)))
            if kind=='OpportunityTeamMember' and self.fail_team:
                result.append({'success':False,'errors':['Team validation failed']});continue
            store=self.members if kind=='OpportunityTeamMember' else self.entries
            if method=='PATCH':
                entry=next(row for row in store if row['Id']==item['Id'])
                entry.update({key:value for key,value in item.items() if key!='attributes'})
            else:store.append({'Id':'NEW',**{key:value for key,value in item.items() if key!='attributes'}})
            result.append({'success':True})
        return 200,result

    def local_run(self,write=False,approval=None):
        return execute_local([self.opp_id],{self.opp_id},self.query,self.rest,lambda:'OverlayId',write=write,
                             approvals={self.opp_id:approval} if approval else {})


class LocalUpdateChecks(unittest.TestCase):
    def test_corrects_existing_roles_and_preserves_percentage_and_access(self):
        sf=DetailedSalesforce(team_role='',split_role='')
        self.assertEqual(sf.local_run()['idsToAdd'],[sf.opp_id])
        self.assertEqual(sf.writes,[])
        result=sf.local_run(write=True)
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(result['teamRolesUpdated'],1)
        self.assertEqual(result['splitRolesUpdated'],1)
        self.assertEqual(sf.members[0]['OpportunityAccessLevel'],'Edit')
        self.assertEqual(sf.entries[0]['SplitPercentage'],37)
        self.assertNotIn('SplitPercentage',sf.writes[-1][1])

    def test_populates_blank_field_even_when_team_and_split_already_exist(self):
        sf=DetailedSalesforce(blank=True)
        result=sf.local_run(write=True)
        self.assertEqual(result['succeeded'],1)
        self.assertEqual([kind for kind,_ in sf.writes],['Opportunity'])

    def test_different_and_inactive_users_are_never_written_without_review(self):
        sf=DetailedSalesforce();sf.record[PARTNER_REL]['OwnerId']=sf.other
        self.assertEqual(sf.local_run(write=True)['states'][sf.opp_id]['updatePlan']['status'],'review')
        self.assertEqual(sf.writes,[])
        sf=DetailedSalesforce(active=False,team_role=None)
        self.assertEqual(sf.local_run(write=True)['states'][sf.opp_id]['updatePlan']['status'],'rvp')
        self.assertEqual(sf.writes,[])

    def test_correct_matching_assignment_needs_no_write_even_if_now_inactive(self):
        sf=DetailedSalesforce(active=False)
        self.assertEqual(sf.local_run()['states'][sf.opp_id]['updatePlan']['status'],'complete')
        self.assertEqual(sf.writes,[])

    def test_rvp_confirmed_coverage_uses_selected_active_user(self):
        sf=DetailedSalesforce(active=False)
        approval={'confirmed':True,'baseOwnerId':sf.rep,'baseRepId':sf.rep,'userId':sf.other,'name':'Covering',
                  'active':True,'role':COVERAGE_ROLE,'requiresRvp':True,'rvpConfirmed':True}
        result=sf.local_run(write=True,approval=approval)
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(sf.record['Partner_Sales_Rep__c'],sf.other)
        self.assertEqual(sf.members[-1]['TeamMemberRole'],COVERAGE_ROLE)
        self.assertEqual(sf.entries[-1]['Opportunity_Team_Role__c'],COVERAGE_ROLE)
        self.assertEqual(sf.entries[-1]['SplitPercentage'],100)
        self.assertEqual(sf.entries[0]['SplitPercentage'],37)

    def test_deactivated_covering_user_and_stale_review_are_skipped(self):
        sf=DetailedSalesforce(active=False)
        approval={'confirmed':True,'baseOwnerId':sf.rep,'baseRepId':sf.rep,'userId':sf.other,'name':'Covering',
                  'active':True,'role':COVERAGE_ROLE,'requiresRvp':True,'rvpConfirmed':True}
        sf.users[sf.other]=False
        self.assertEqual(sf.local_run(write=True,approval=approval)['idsToAdd'],[])
        self.assertEqual(sf.writes,[])
        sf.users[sf.other]=True;approval['baseOwnerId']='changed'
        self.assertEqual(sf.local_run(write=True,approval=approval)['idsToAdd'],[])

    def test_team_failure_stops_split_stage(self):
        sf=DetailedSalesforce(team_role=None,split_role=None,fail_team=True)
        result=sf.local_run(write=True)
        self.assertEqual(result['failed'],1)
        self.assertEqual([kind for kind,_ in sf.writes],['OpportunityTeamMember'])

    def test_duplicate_splits_require_manual_review(self):
        sf=DetailedSalesforce();sf.entries*=2
        self.assertEqual(sf.local_run(write=True)['states'][sf.opp_id]['updatePlan']['status'],'blocked')
        self.assertEqual(sf.writes,[])
