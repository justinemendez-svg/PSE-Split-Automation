"""Common Salesforce fields and PSE identity helpers for the current local dashboard."""
from __future__ import annotations


PARTNER_FIELD = 'Referring_Partner__c'
PARTNER_REL = 'Referring_Partner__r'
TEAM_ROLE = 'Partner Account Executive'
EXCLUDED_PARTNER_TYPE = 'Startup Program Partner'
CLOSED_STAGE = '08 - Closed'
API_VERSION = 'v60.0'
REP_FIELDS = (f'{PARTNER_FIELD}, {PARTNER_REL}.Name, {PARTNER_REL}.Type, {PARTNER_REL}.OwnerId, '
              f'{PARTNER_REL}.Owner.Name, {PARTNER_REL}.Owner.IsActive, '
              'Partner_Sales_Rep__c, Partner_Sales_Rep__r.Name, Partner_Sales_Rep__r.IsActive, LastModifiedDate')


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
            'partnerType': partner.get('Type'),
            'partnerOwnerId': partner.get('OwnerId'), 'partnerOwnerName': (partner.get('Owner') or {}).get('Name'),
            'partnerRepId': rep_id, 'partnerRepName': rep.get('Name') or '(user unavailable)',
            'partnerRepActive': rep.get('IsActive'), 'partnerRepNeedsPopulate': populate,
            'partnerRepSource': 'partner_owner' if populate else 'existing',
            'partnerRepOnTeam': on_team, 'partnerRepOnSplit': on_split,
            'blockedReason': '' if valid_user else 'The Partner Owner could not be resolved to a Salesforce user.',
            'bucket': 'complete' if complete else 'needs_call',
            'reason': 'PSE field, Team and Splits are complete' if complete else 'Missing: ' + ', '.join(missing)}


def is_complete(row):
    return not row.get('partnerRepNeedsPopulate') and row.get('partnerRepOnTeam') and row.get('partnerRepOnSplit')
