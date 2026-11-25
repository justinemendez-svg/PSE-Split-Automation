"""Validate current-profile local snapshots without dropping review fields."""
import copy
import datetime as dt
import json
import re
from local_scope import LOCAL_SCOPE_VERSION, opportunity_key, local_summary, in_fiscal_window
from workflow import CLOSED_STAGE, EXCLUDED_PARTNER_TYPE


def validate_snapshot(value):
    if not isinstance(value, dict) or not isinstance(value.get('config'), dict):
        raise ValueError('Expected a current PSE Split Automation snapshot.')
    if value['config'].get('scopeVersion') != LOCAL_SCOPE_VERSION:
        raise ValueError('This snapshot uses an older scope. Click Refresh to fetch current data.')
    try:
        timestamp = dt.datetime.fromisoformat(value['generatedAt'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ValueError('The extraction date must include a timezone.') from None
    if not isinstance(value.get('rows'), list):
        raise ValueError('The snapshot must contain an Opportunity rows list.')
    seen = set()
    for number, row in enumerate(value['rows'], 1):
        if not isinstance(row, dict):
            raise ValueError(f'Row {number} is not an Opportunity record.')
        opp_id = row.get('id')
        if not isinstance(opp_id, str) or not re.fullmatch(r'006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?', opp_id):
            raise ValueError(f'Row {number} has an invalid Opportunity ID.')
        key = opportunity_key(opp_id)
        if key in seen:
            raise ValueError(f'Duplicate Opportunity ID at row {number}.')
        seen.add(key)
        if (row.get('stage') != CLOSED_STAGE or row.get('nonCommissionable') is not False
                or not row.get('partnerId') or 'partnerType' not in row
                or not in_fiscal_window(row.get('closeDate'))
                or row['partnerType'] == EXCLUDED_PARTNER_TYPE or row.get('partnerName') == 'AWS Marketplace'):
            raise ValueError(f'Row {number} is outside the current closed Partner scope.')
        if row.get('membershipDetailsLoaded') is not True:
            raise ValueError(f'Row {number} needs current Team and Split details.')
        if not isinstance(row.get('teamEntries'), list) or not isinstance(row.get('splitEntries'), list):
            raise ValueError(f'Row {number} has invalid Team or Split details.')
    # Reject non-JSON or non-finite values before replacing an export file.
    json.dumps(value, allow_nan=False)
    checked = copy.deepcopy(value)
    checked['summary'] = local_summary(checked['rows'])
    return checked
