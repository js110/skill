"""Dated personal profiles, weight measurements and non-scheduled review prompts."""
import copy
import math
import statistics
import uuid
from datetime import date, datetime, timedelta, timezone

OPERATIONS = {'update-profile', 'weight', 'remove-weight', 'review-profile',
              'confirm-plan', 'review-noted', 'migrate'}
PLAN_FIELDS = {'age', 'height_cm', 'sex_coefficient', 'activity', 'goal',
               'goal_weight_kg', 'special_care', 'health_note', 'medication_note'}
PROFILE_FIELDS = PLAN_FIELDS | {'weight_kg', 'allergies', 'preferences'}

def stamp():
    return datetime.now(timezone.utc).isoformat()

def identifier():
    return str(uuid.uuid4())

def parse_day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Expected YYYY-MM-DD')
    return date.fromisoformat(value)

def upgrade(state, day):
    if state['version'] == 2:
        return state
    if state['version'] != 1:
        raise ValueError('Unsupported state version')
    # Preserve existing meal totals and target snapshots. Earlier profile values are unknown.
    state['version'] = 2
    state['profile_versions'] = []
    state['weights'] = []
    state['review_notices'] = {}
    legacy_dates = [x['date'] for x in state.get('history', [])
                    if x.get('operation') in ('init', 'set-profile') and x.get('date')]
    baseline = max(legacy_dates) if legacy_dates else day
    if state['profile'] is not None:
        p = copy.deepcopy(state['profile'])
        p['profile_reviewed_on'] = baseline
        p['plan_reviewed_on'] = baseline
        p['plan_state'] = 'active'
        p['plan_anchor_weight_kg'] = p['weight_kg']
        append_version(state, p, baseline, '旧版现有档案迁移；未重建更早资料', baseline)
        state['weights'].append({'id': identifier(), 'date': baseline, 'kg': p['weight_kg'],
                                'source': 'initial_profile', 'recorded_at': stamp(), 'removed': False})
    return state

def version_at(state, day):
    choices = [(index, v) for index, v in enumerate(state.get('profile_versions', [])) if v['effective_date'] <= day]
    return max(choices, key=lambda pair: (pair[1]['effective_date'], pair[0]))[1] if choices else None

def daily_weights(state, day):
    by_day = {}
    for record in state.get('weights', []):
        if not record.get('removed') and record['date'] <= day:
            by_day[record['date']] = record
    return [by_day[key] for key in sorted(by_day)]

def weight_trend(state, day):
    points = daily_weights(state, day)
    today = parse_day(day)
    current = [r for r in points if 0 <= (today - parse_day(r['date'])).days < 7]
    previous = [r for r in points if 7 <= (today - parse_day(r['date'])).days < 14]
    current_avg = statistics.fmean(r['kg'] for r in current) if len(current) >= 3 else None
    previous_avg = statistics.fmean(r['kg'] for r in previous) if len(previous) >= 3 else None
    return {'latest_kg': points[-1]['kg'] if points else None,
            'latest_date': points[-1]['date'] if points else None,
            'mean_7d_kg': current_avg, 'days_in_7d': len(current),
            'previous_mean_7d_kg': previous_avg,
            'change_between_7d_means_kg': current_avg - previous_avg if current_avg is not None and previous_avg is not None else None,
            'points': [{'date': r['date'], 'kg': r['kg']} for r in points]}

def effective_profile(state, day, smooth=False):
    if state.get('version') == 1:
        return copy.deepcopy(state['profile'])
    version = version_at(state, day)
    if not version or version['profile'] is None:
        return None
    profile = copy.deepcopy(version['profile'])
    profile.pop('_weight_missing', None)
    profile.pop('_stale', None)
    trend = weight_trend(state, day)
    if trend['latest_kg'] is not None:
        profile['weight_kg'] = trend['mean_7d_kg'] if smooth and trend['mean_7d_kg'] is not None else trend['latest_kg']
    else:
        profile['_weight_missing'] = True
    reviewed = profile.get('profile_reviewed_on', version['recorded_on'])
    profile['_stale'] = (parse_day(day) - parse_day(reviewed)).days >= 90
    return profile

def append_version(state, profile, effective_day, reason, recorded_day):
    parse_day(effective_day)
    profile = copy.deepcopy(profile)
    if profile:
        profile.pop('_stale', None)
        profile.pop('_weight_missing', None)
    event = {'id': identifier(), 'effective_date': effective_day, 'recorded_on': recorded_day,
             'recorded_at': stamp(), 'reason': reason, 'profile': copy.deepcopy(profile)}
    state['profile_versions'].append(event)
    state['profile'] = effective_profile(state, recorded_day)
    if state['profile']:
        state['profile'].pop('_stale', None)
    return event

def review_info(state, day):
    profile = effective_profile(state, day)
    version = version_at(state, day)
    trend = weight_trend(state, day)
    due, prompts = [], []
    if profile:
        last = trend['latest_date']
        if last is None or (parse_day(day) - parse_day(last)).days >= 7:
            due.append('weight')
        if (parse_day(day) - parse_day(profile.get('profile_reviewed_on', version['recorded_on']))).days >= 30:
            due.append('profile')
        if profile.get('plan_state') == 'needs_review':
            due.append('plan')
        for key in due:
            prior = state.get('review_notices', {}).get(key)
            if prior is None or (parse_day(day) - parse_day(prior)).days >= 7:
                prompts.append(key)
    return {'due': due, 'prompts': prompts, 'profile_effective_date': version['effective_date'] if version else None,
            'profile_reviewed_on': profile.get('profile_reviewed_on') if profile else None,
            'stale': bool(profile and profile['_stale']),
            'plan_needs_review': bool(profile and profile.get('plan_state') == 'needs_review'),
            'health_paused': bool(profile and profile.get('special_care'))}

def review_note(info):
    if info['health_paused']:
        return '健康情况已变化，热量限额与超额判断暂停，继续记录食物'
    if info['stale']:
        return '个人资料已较久未复核，暂用饮食记录模式'
    if info['plan_needs_review']:
        return '资料已更新，原热量计划待复核，暂不沿用旧限额'
    messages = {'weight': '近期没有新体重，方便时记一次即可',
                'profile': '有空时确认近期活动量、健康情况和目标是否有变化'}
    return '；'.join(messages[k] for k in info['prompts'] if k in messages)

def handle(state, request, day, validate_profile):
    operation = request['operation']
    effective_day = request.get('effective_date', day)
    parse_day(effective_day)
    if operation == 'migrate':
        return
    if operation == 'review-noted':
        keys = request.get('keys', [])
        if not isinstance(keys, list) or not all(k in ('weight', 'profile', 'plan') for k in keys):
            raise ValueError('Invalid review reminder keys')
        for key in keys:
            state['review_notices'][key] = day
        return
    if operation == 'remove-weight':
        matching = [r for r in state['weights'] if r['id'] == request.get('id') and not r.get('removed')]
        if not matching:
            raise ValueError('Unknown active weight id')
        matching[0]['removed'] = True
        return
    profile = effective_profile(state, effective_day)
    if profile is None and operation == 'weight':
        profile = effective_profile(state, day)
    if profile is None:
        raise ValueError('Complete the basic profile before updating it')
    profile.pop('_stale', None)
    if operation == 'weight':
        value = request.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Weight must be a finite number')
        unit = request.get('unit', 'kg')
        if unit not in ('kg', 'jin'):
            raise ValueError('Weight unit must be kg or jin (市斤)')
        kg = value / 2 if unit == 'jin' else value
        if not 10 <= kg <= 400:
            raise ValueError('Weight outside supported range; verify units')
        record_id = request.get('id')
        if not isinstance(record_id, str) or not record_id:
            raise ValueError('Weight requires a stable id')
        if any(r['id'] == record_id for r in state['weights']):
            raise ValueError('Duplicate weight id')
        replacement = request.get('replace_id')
        if replacement:
            old = next((r for r in state['weights'] if r['id'] == replacement and not r.get('removed')), None)
            if old is None:
                raise ValueError('Weight to correct not found')
            old['removed'] = True
        source = request.get('source', 'user_report')
        if source not in ('user_report', 'scale_image'):
            raise ValueError('Weight source must be user_report or scale_image')
        state['weights'].append({'id': record_id, 'date': effective_day, 'kg': kg, 'source': source,
                                'recorded_at': stamp(), 'removed': False, 'replaces': replacement})
        # Calorie assumptions are recomputed from dated measurements. A user-set budget
        # is reviewed after a material change, not adjusted for every scale fluctuation.
        anchor = profile.get('plan_anchor_weight_kg', profile['weight_kg'])
        comparison_day = max(day, effective_day)
        reference = effective_profile(state, comparison_day, smooth=True)['weight_kg']
        if profile.get('target_override') and abs(reference - anchor) / anchor >= .02:
            profile['plan_state'] = 'needs_review'
            applicable = effective_day if version_at(state, effective_day) else day
            append_version(state, profile, applicable, '体重相对计划基准变化，需复核原计划', day)
        state['profile'] = effective_profile(state, day)
        if state['profile']:
            state['profile'].pop('_stale', None)
        return
    if operation == 'update-profile':
        changes = request.get('changes')
        if not isinstance(changes, dict) or not changes or set(changes) - PROFILE_FIELDS:
            raise ValueError('Provide supported profile fields only; weight uses the weight operation')
        if 'weight_kg' in changes:
            raise ValueError('Use weight to retain the measurement timeline')
        changed = {k for k, v in changes.items() if profile.get(k) != v}
        profile.update(copy.deepcopy(changes))
        if changed & PLAN_FIELDS:
            profile['plan_state'] = 'needs_review'
        validate_profile(profile)
        append_version(state, profile, effective_day, request.get('reason', '用户更新个人情况'), day)
    elif operation == 'review-profile':
        if request.get('confirmed') is not True:
            raise ValueError('Full profile review needs the user to confirm unchanged fields')
        profile['profile_reviewed_on'] = day
        append_version(state, profile, effective_day, '用户确认基础资料仍适用', day)
    elif operation == 'confirm-plan':
        if request.get('confirmed') is not True or profile['special_care']:
            raise ValueError('Only confirm a user-chosen plan when special care is not active')
        if 'target_override' not in request:
            raise ValueError('Provide an explicit target_override, or null for maintenance reference')
        if profile.get('_stale') or (parse_day(day) - parse_day(profile.get('profile_reviewed_on', day))).days >= 90:
            raise ValueError('Review the current profile before confirming a plan')
        profile['target_override'] = copy.deepcopy(request['target_override'])
        profile['plan_state'] = 'active'
        profile['plan_reviewed_on'] = day
        profile['plan_anchor_weight_kg'] = effective_profile(state, day, smooth=True)['weight_kg']
        validate_profile(profile)
        append_version(state, profile, effective_day, '用户确认当前适用热量参考', day)

