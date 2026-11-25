"""Current local review plans and confirmed Salesforce updates."""
import datetime as dt
from email.utils import format_datetime
from local_scope import LOCAL_REP_FIELDS, in_local_scope, local_rep_state
from workflow import API_VERSION, TEAM_ROLE

COVERAGE_ROLE = TEAM_ROLE + ' - Coverage'
VALID_ROLES = (TEAM_ROLE, COVERAGE_ROLE)


def plan(row, approval=None):
    rep = row.get('partnerRepId')
    owner = row.get('partnerOwnerId')
    blank = row.get('partnerRepNeedsPopulate')
    different = bool(not blank and rep and owner and rep[:15] != owner[:15])
    review = approval or row.get('assignmentReview') or {}
    accepted = bool(review.get('confirmed') and review.get('baseOwnerId') == owner
                    and rep in (review.get('baseRepId'), review.get('userId'))
                    and review.get('role') in VALID_ROLES and review.get('active') is True)
    target = review.get('userId') if accepted else rep
    name = review.get('name') if accepted else row.get('partnerRepName')
    role = review.get('role') if accepted else TEAM_ROLE
    team = [item for item in row.get('teamEntries', []) if item.get('UserId') == target]
    splits = [item for item in row.get('splitEntries', []) if item.get('SplitOwnerId') == target]
    existing_split_role = splits[0].get('Opportunity_Team_Role__c') if len(splits) == 1 else None
    # Both Split roles are valid. Only an explicit reviewed role choice overrides one.
    split_role = existing_split_role if existing_split_role in VALID_ROLES and not accepted else role
    changes = []
    if blank or target != rep:
        changes.append({'object': 'Opportunity', 'action': 'Populate PSE field' if blank else 'Set approved PSE',
                        'values': {'Partner_Sales_Rep__c': target}})
    if not team:
        changes.append({'object':'OpportunityTeamMember','action':'Add Team member','values':{'UserId':target,'TeamMemberRole':role,'OpportunityAccessLevel':'Read'}})
    elif len(team) == 1 and team[0].get('TeamMemberRole') != role:
        changes.append({'object':'OpportunityTeamMember','id':team[0].get('Id'),'action':'Correct Team role','values':{'TeamMemberRole':role}})
    if not splits:
        changes.append({'object':'OpportunitySplit','action':'Add Overlay Split','values':{'SplitOwnerId':target,'Opportunity_Team_Role__c':split_role,'SplitPercentage':100}})
    elif len(splits) == 1 and existing_split_role != split_role:
        changes.append({'object':'OpportunitySplit','id':splits[0].get('Id'),'action':'Apply reviewed Split role' if existing_split_role in VALID_ROLES else 'Correct Split role','values':{'Opportunity_Team_Role__c':split_role}})
    reason = ''
    status = 'ready' if changes else 'complete'
    if row.get('inScope') is False:
        status, reason = 'blocked', 'Opportunity is outside the current scope.'
    elif not rep or not rep.startswith('005') or not owner:
        status, reason = 'blocked', 'Partner Owner or PSE could not be resolved.'
    elif not row.get('membershipDetailsLoaded'):
        status, reason = 'blocked', 'Click Refresh to load actual Team and Split roles.'
    elif len(team) > 1 or len(splits) > 1:
        status, reason = 'blocked', 'Multiple Team or Split entries need individual review in Salesforce.'
    elif any(item.get('id') is None and item['action'] in ('Correct Team role', 'Correct Split role', 'Apply reviewed Split role') for item in changes):
        status, reason = 'blocked', 'Refresh to load the existing entry IDs before correcting roles.'
    elif (len(splits) == 1 and existing_split_role == COVERAGE_ROLE
          and splits[0].get('Coverage__c') is not True):
        status, reason = 'blocked', ('Flag: Coverage role has Coverage unchecked. This read-only Salesforce formula needs verification in Salesforce.' if splits[0].get('Coverage__c') is False else 'Coverage status is not loaded. Click Refresh to read the Salesforce value.')
    elif status == 'complete' and not different:
        pass
    elif row.get('partnerRepActive') is False and not accepted:
        status, reason = 'rvp', 'Inactive PSE: confirm an active covering PSE with the RVP.'
    elif row.get('partnerRepActive') is not True and not accepted:
        status, reason = 'blocked', 'The PSE activity status is unavailable.'
    elif different and not accepted:
        status, reason = 'review', 'Tagged PSE differs from Partner Owner. Confirm the assignment first.'
    if accepted and review.get('requiresRvp') and (not review.get('rvpConfirmed') or role != COVERAGE_ROLE):
        status, reason = 'rvp', 'RVP confirmation and the Coverage role are required.'
    return {'status':status, 'reason':reason, 'changes':changes, 'targetRepId':target, 'targetName':name,
            'targetRole':role, 'targetSplitRole':split_role, 'coverageRequired':split_role == COVERAGE_ROLE, 'teamCurrent':team, 'splitCurrent':splits,
            'fieldCurrent':None if blank else row.get('partnerRepName'), 'different':different}


def decorate(row, approval=None):
    if approval:
        row['assignmentReview'] = approval
    row['updatePlan'] = plan(row, approval)
    return row


def read_current(ids, query, approvals=None):
    quoted=','.join("'"+key+"'" for key in ids)
    records=query('SELECT Id, Name, StageName, CloseDate, Type, Non_Commissionable__c, '+LOCAL_REP_FIELDS+' FROM Opportunity WHERE Id IN ('+quoted+')')
    team=query('SELECT Id, OpportunityId, UserId, User.Name, TeamMemberRole, OpportunityAccessLevel FROM OpportunityTeamMember WHERE OpportunityId IN ('+quoted+')')
    splits=query('SELECT Id, OpportunityId, SplitOwnerId, SplitOwner.Name, SplitType.MasterLabel, Opportunity_Team_Role__c, Coverage__c, SplitPercentage FROM OpportunitySplit WHERE OpportunityId IN ('+quoted+')')
    states={}
    approvals=dict(approvals or {})
    selected={value['userId'] for value in approvals.values() if value.get('userId')}
    if selected:
        users=query('SELECT Id, Name, IsActive FROM User WHERE Id IN ('+','.join("'"+key+"'" for key in sorted(selected))+')')
        active={user['Id'] for user in users if user.get('IsActive') is True}
        approvals={key:{**value,'active':value.get('userId') in active} for key,value in approvals.items()}
    by_id={row['Id']:row for row in records}
    for key in ids:
        record=by_id.get(key)
        if not record or not in_local_scope(record):
            states[key]={'inScope':False,'updatePlan':{'status':'blocked','reason':'Opportunity is outside the current scope.','changes':[]}}
            continue
        members=[item for item in team if item['OpportunityId']==key]
        entries=[item for item in splits if item['OpportunityId']==key]
        row=local_rep_state(record,members,entries)
        # Reviews may select a different PSE; inspect all memberships before planning.
        row.update(teamEntries=members,splitEntries=entries)
        row.update(id=key,name=record.get('Name'),inScope=True)
        states[key]=decorate(row,approvals.get(key))
        row['teamEntries']=row['updatePlan']['teamCurrent']
        row['splitEntries']=row['updatePlan']['splitCurrent']
    return by_id,states


def execute_local(ids, allowed, query, rest, split_type, write=False, approvals=None):
    requested=[key for key in dict.fromkeys(ids) if key in allowed]
    result={'states':{},'idsToAdd':[],'errors':[],'succeeded':0,'failed':0,'fieldPopulated':0,
            'teamAdded':0,'splitAdded':0,'teamRolesUpdated':0,'splitRolesUpdated':0,
            'inactive':0,'changed':0,'excluded':len(set(ids))-len(requested)}
    for start in range(0,len(requested),200):
        batch=requested[start:start+200]
        records,current=read_current(batch,query,approvals)
        ready=[key for key in batch if current[key]['updatePlan']['status']=='ready']
        result['inactive']+=sum(current[key]['updatePlan']['status']=='rvp' for key in batch)
        result['changed']+=sum(current[key]['updatePlan']['status'] in ('review','blocked') for key in batch)
        result['idsToAdd'].extend(ready)
        failed=set()
        if write:
            for kind in ('Opportunity','OpportunityTeamMember','OpportunitySplit'):
                if kind!='Opportunity': records,current=read_current(batch,query,approvals)
                # Every stage rechecks scope, activity and the reviewed assignment.
                jobs=[]
                for key in ready:
                    row=current[key]; p=row['updatePlan']
                    if key in failed or p['status'] not in ('ready','complete'):continue
                    if kind=='OpportunitySplit' and any(change['object']=='OpportunityTeamMember' for change in p['changes']):
                        failed.add(key);result['errors'].append({'oppId':key,'message':'The Team step is not complete; Splits were not changed.'});continue
                    for change in p['changes']:
                        if change['object']==kind:jobs.append((key,change))
                if kind=='Opportunity':
                    for key,change in jobs:
                        try:
                            stamp=dt.datetime.strptime(records[key]['LastModifiedDate'],'%Y-%m-%dT%H:%M:%S.%f%z').astimezone(dt.timezone.utc)
                            code,response=rest('PATCH',f'/services/data/{API_VERSION}/sobjects/Opportunity/{key}',change['values'],headers={'If-Unmodified-Since':format_datetime(stamp,usegmt=True)})
                            if code not in (200,204):raise RuntimeError(str(response))
                            result['fieldPopulated']+=1
                        except (RuntimeError,OSError,ValueError,KeyError) as error:
                            failed.add(key);result['errors'].append({'oppId':key,'message':str(error)[:500]})
                else:
                    for method in ('POST','PATCH'):
                        group=[(key,change) for key,change in jobs if ('PATCH' if change.get('id') else 'POST')==method]
                        if not group:continue
                        payload=[]
                        for key,change in group:
                            values=dict(change['values'])
                            if method=='PATCH':values['Id']=change['id']
                            else:
                                values['OpportunityId']=key
                                if kind=='OpportunitySplit':values['SplitTypeId']=split_type()
                            payload.append({'attributes':{'type':kind},**values})
                        try:
                            code,response=rest(method,f'/services/data/{API_VERSION}/composite/sobjects',{'allOrNone':False,'records':payload})
                            if code not in (200,201) or not isinstance(response,list) or len(response)!=len(group):raise RuntimeError(str(response))
                            for (key,_),item in zip(group,response):
                                if item.get('success'):
                                    counter=('teamRolesUpdated' if kind=='OpportunityTeamMember' else 'splitRolesUpdated') if method=='PATCH' else ('teamAdded' if kind=='OpportunityTeamMember' else 'splitAdded')
                                    result[counter]+=1
                                else:
                                    failed.add(key);result['errors'].append({'oppId':key,'message':str(item.get('errors',item))[:500]})
                        except (RuntimeError,OSError,ValueError) as error:
                            for key,_ in group:failed.add(key);result['errors'].append({'oppId':key,'message':str(error)[:500]})
            _,current=read_current(batch,query,approvals)
            for key in ready:
                done=current[key]['updatePlan']['status']=='complete'
                result['succeeded' if done else 'failed']+=1
                if not done and not any(item.get('oppId')==key for item in result['errors']):
                    result['errors'].append({'oppId':key,'message':current[key]['updatePlan'].get('reason') or 'Some required changes remain.'})
        result['states'].update(current)
    return result
