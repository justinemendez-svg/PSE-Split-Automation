"""Current closed Partner Opportunity scope and snapshot classification."""
import re
from workflow import CLOSED_STAGE, EXCLUDED_PARTNER_TYPE, PARTNER_FIELD, PARTNER_REL, REP_FIELDS, rep_state, is_complete

LOCAL_SCOPE_VERSION = 6
LOCAL_REP_FIELDS = (REP_FIELDS + ', Partner_Type__c, ' + PARTNER_REL + '.Region__c, '
                    + PARTNER_REL + '.Owner.Profile.Name, Partner_Sales_Rep__r.Profile.Name')


def opportunity_key(value):
    value=str(value or '').strip()
    return value[:15] if re.fullmatch(r'006[A-Za-z0-9]{15}',value) else value


def unique_opportunities(rows, field='id'):
    unique={}
    for row in rows:
        value=str(row.get(field) or '').strip()
        key=opportunity_key(value)
        if not key:continue
        previous=unique.get(key,{})
        unique[key]={**row,field:previous[field] if len(previous.get(field,''))==18 and len(value)==15 else value}
    return list(unique.values())


def local_summary(rows):
    rows=unique_opportunities(rows)
    complete=sum(row['updatePlan']['status']=='complete' if row.get('updatePlan') else bool(is_complete(row)) for row in rows)
    return {'total':len(rows),'tagged':complete,'needsUpdate':len(rows)-complete,
            'needsPopulate':sum(bool(row.get('partnerRepNeedsPopulate')) for row in rows)}


def in_local_scope(record):
    return (bool(record.get(PARTNER_FIELD))
            and record.get('Non_Commissionable__c') is False
            and record.get('StageName') == CLOSED_STAGE
            and record.get('Partner_Type__c') != EXCLUDED_PARTNER_TYPE
            and (record.get(PARTNER_REL) or {}).get('Name') != 'AWS Marketplace')


def local_rep_state(record, team=(), splits=()):
    partner = record.get(PARTNER_REL) or {}
    owner = partner.get('Owner') or {}
    tagged = record.get('Partner_Sales_Rep__r') or {}
    team = list(team)
    splits = list(splits)
    detailed = all(isinstance(row, dict) for row in team + splits)
    value = rep_state(record, {row.get('UserId') for row in team} if detailed else team,
                      {row.get('SplitOwnerId') for row in splits} if detailed else splits)
    value.update(
        partnerType=record.get('Partner_Type__c'),
        partnerAccountType=partner.get('Type'),
        partnerRegion=partner.get('Region__c'),
        partnerOwnerProfile=(owner.get('Profile') or {}).get('Name'),
        taggedPseProfile=(tagged.get('Profile') or {}).get('Name'),
    )
    if detailed:
        value['membershipDetailsLoaded'] = True
        value['teamEntries'] = [row for row in team if row.get('UserId') == value['partnerRepId']]
        value['splitEntries'] = [row for row in splits if row.get('SplitOwnerId') == value['partnerRepId']]
    return value
