"""Shared Partner → PSE → Team → Splits rules for local and hosted dashboards."""
from __future__ import annotations

import datetime as dt
from email.utils import format_datetime

PARTNER_FIELD = 'Referring_Partner__c'
PARTNER_REL = 'Referring_Partner__r'
TEAM_ROLE = 'Partner Account Executive'
OPPORTUNITY_TYPES = ('Renewal', 'New Business', 'Expansion')
SCOPE_VERSION = 4
CLOSED_STAGE = '08 - Closed'
API_VERSION = 'v60.0'
REP_FIELDS = (f'{PARTNER_FIELD}, {PARTNER_REL}.Name, {PARTNER_REL}.OwnerId, '
              f'{PARTNER_REL}.Owner.Name, {PARTNER_REL}.Owner.IsActive, '
              'Partner_Sales_Rep__c, Partner_Sales_Rep__r.Name, Partner_Sales_Rep__r.IsActive, LastModifiedDate')


def in_scope(record):
    return (bool(record.get(PARTNER_FIELD)) and record.get('Non_Commissionable__c') is False
            and record.get('Type') in OPPORTUNITY_TYPES and record.get('StageName') == CLOSED_STAGE)


def rep_state(record, team=(), splits=()):
    partner = record.get(PARTNER_REL) or {}
    populate = not bool(record.get('Partner_Sales_Rep__c'))
    rep_id = partner.get('OwnerId') if populate else record.get('Partner_Sales_Rep__c')
    rep = (partner.get('Owner') or {}) if populate else (record.get('Partner_Sales_Rep__r') or {})
    valid_user = bool(rep_id and rep_id.startswith('005'))
    on_team, on_split = bool(rep_id and rep_id in team), bool(rep_id and rep_id in splits)
    complete = not populate and on_team and on_split
    missing = []
    if populate: missing.append('Partner Sales Rep field')
    if not on_team: missing.append('Opportunity Team')
    if not on_split: missing.append('Opportunity Splits')
    return {'stage': record.get('StageName'), 'partnerId': record.get(PARTNER_FIELD), 'partnerName': partner.get('Name'),
            'partnerOwnerName': (partner.get('Owner') or {}).get('Name'),
            'partnerRepId': rep_id, 'partnerRepName': rep.get('Name') or '(user unavailable)',
            'partnerRepActive': rep.get('IsActive'), 'partnerRepNeedsPopulate': populate,
            'partnerRepSource': 'partner_owner' if populate else 'existing',
            'partnerRepOnTeam': on_team, 'partnerRepOnSplit': on_split,
            'blockedReason': '' if valid_user else 'The Partner Owner could not be resolved to a Salesforce user.',
            'bucket': 'complete' if complete else 'needs_call',
            'reason': 'PSE field, Team and Splits are complete' if complete else 'Missing: ' + ', '.join(missing)}


def is_complete(row):
    return not row.get('partnerRepNeedsPopulate') and row.get('partnerRepOnTeam') and row.get('partnerRepOnSplit')


def summary(rows):
    complete = sum(bool(is_complete(row)) for row in rows)
    return {'total': len(rows), 'tagged': complete, 'needsUpdate': len(rows) - complete,
            'needsPopulate': sum(bool(row.get('partnerRepNeedsPopulate')) for row in rows)}


def read_states(ids, query):
    quoted = ','.join("'" + value + "'" for value in ids)
    records = query('SELECT Id, Name, StageName, Type, Non_Commissionable__c, ' + REP_FIELDS +
                    f' FROM Opportunity WHERE Id IN ({quoted})')
    team, splits = {}, {}
    for row in query(f'SELECT OpportunityId, UserId FROM OpportunityTeamMember WHERE OpportunityId IN ({quoted})'):
        team.setdefault(row['OpportunityId'], set()).add(row['UserId'])
    for row in query(f'SELECT OpportunityId, SplitOwnerId FROM OpportunitySplit WHERE OpportunityId IN ({quoted})'):
        splits.setdefault(row['OpportunityId'], set()).add(row['SplitOwnerId'])
    by_id = {row['Id']: row for row in records}
    states = {key: rep_state(row, team.get(key, ()), splits.get(key, ()))
              for key, row in by_id.items() if in_scope(row)}
    return by_id, states


def execute(ids, allowed, query, rest, split_type, write=False):
    """Preview or apply the same ordered rules; re-read each stage and report partial failures."""
    requested = [value for value in dict.fromkeys(ids) if value in allowed]
    states, errors, ids_to_add = {}, [], []
    counts = {'fieldPopulated': 0, 'teamAdded': 0, 'splitAdded': 0, 'alreadyComplete': 0,
              'changed': 0, 'excluded': len(ids) - len(requested), 'inactive': 0}
    failed, succeeded = set(), set()

    def insert_many(object_name, records, counter):
        if not records: return
        status, result = rest('POST', f'/services/data/{API_VERSION}/composite/sobjects',
                              {'allOrNone': False, 'records': [{'attributes': {'type': object_name}, **row} for _, row in records]})
        responses = result if isinstance(result, list) and len(result) == len(records) else []
        if status not in (200, 201) or not responses:
            raise RuntimeError(str(result)[:500])
        for (opp_id, _), response in zip(records, responses):
            if response.get('success'): counts[counter] += 1
            else:
                failed.add(opp_id)
                errors.append({'oppId': opp_id, 'object': object_name, 'message': str(response.get('errors', response))[:500]})

    for start in range(0, len(requested), 200):
        batch = requested[start:start + 200]
        try:
            records, current = read_states(batch, query)
            counts['changed'] += len(batch) - len(current)
            for opp_id, value in current.items():
                if is_complete(value): counts['alreadyComplete'] += 1
                elif value['blockedReason']: errors.append({'oppId': opp_id, 'message': value['blockedReason']})
                else: ids_to_add.append(opp_id)
            if not write:
                states.update(current)
                continue
            # Populate blank fields only, conditional on the Opportunity remaining unchanged.
            for opp_id, value in current.items():
                if value['blockedReason']:
                    failed.add(opp_id)
                    continue
                if not value['partnerRepNeedsPopulate']: continue
                modified = records[opp_id].get('LastModifiedDate')
                if not modified:
                    failed.add(opp_id)
                    errors.append({'oppId': opp_id, 'message': 'Missing record version; refresh before populating the PSE.'})
                    continue
                timestamp = dt.datetime.strptime(modified, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(dt.timezone.utc)
                status, response = rest('PATCH', f'/services/data/{API_VERSION}/sobjects/Opportunity/{opp_id}',
                                        {'Partner_Sales_Rep__c': value['partnerRepId']},
                                        headers={'If-Unmodified-Since': format_datetime(timestamp, usegmt=True)})
                if status in (200, 204): counts['fieldPopulated'] += 1
                else:
                    failed.add(opp_id)
                    errors.append({'oppId': opp_id, 'object': 'Opportunity', 'message': str(response)[:500]})
            # Re-read to see assignment changes and any Salesforce automation triggered by the field update.
            _, current = read_states(batch, query)
            members = [(key, {'OpportunityId': key, 'UserId': value['partnerRepId'],
                        'TeamMemberRole': TEAM_ROLE, 'OpportunityAccessLevel': 'Read'})
                       for key, value in current.items() if key not in failed and not value['blockedReason']
                       and not value['partnerRepNeedsPopulate'] and not value['partnerRepOnTeam']]
            insert_many('OpportunityTeamMember', members, 'teamAdded')
            _, current = read_states(batch, query)
            splits = [(key, {'OpportunityId': key, 'SplitOwnerId': value['partnerRepId'],
                       'SplitTypeId': split_type(), 'Opportunity_Team_Role__c': TEAM_ROLE, 'SplitPercentage': 100})
                      for key, value in current.items() if key not in failed and not value['blockedReason']
                      and not value['partnerRepNeedsPopulate'] and value['partnerRepOnTeam'] and not value['partnerRepOnSplit']]
            insert_many('OpportunitySplit', splits, 'splitAdded')
            _, current = read_states(batch, query)
            states.update(current)
            for key, value in current.items():
                if is_complete(value): succeeded.add(key); failed.discard(key)
                else: failed.add(key)
        except (RuntimeError, OSError, ValueError) as error:
            if not write: raise
            failed.update(batch)
            errors.append({'message': str(error)[:500]})
            try:
                _, latest = read_states(batch, query)
                states.update(latest)
            except (RuntimeError, OSError, ValueError): pass
    return {**counts, 'states': states, 'idsToAdd': ids_to_add, 'succeeded': len(succeeded),
            'succeededIds': sorted(succeeded), 'failed': len(failed), 'errors': errors[:50]}
