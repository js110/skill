#!/usr/bin/env python3
"""Maintain a local, date-scoped food log and an adult maintenance reference."""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from render_card import validate as validate_meal
import profile_history as profiles

ACTIVITY = {'sedentary': (1.4, 1.6), 'light': (1.6, 1.8),
            'active': (1.8, 2.0), 'very_active': (2.0, 2.4)}

def number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f'{name} is outside the supported range')
    return value

def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value

def flag(value, name):
    if not isinstance(value, bool):
        raise ValueError(f'{name} must be boolean')
    return value

def offset(value):
    if not isinstance(value, str) or not re.fullmatch(r'[+-]\d{2}:\d{2}', value):
        raise ValueError('timezone_offset must be +/-HH:MM')
    hours, minutes = map(int, value[1:].split(':'))
    if minutes > 59 or hours > 14 or (hours == 14 and minutes):
        raise ValueError('Invalid UTC offset')
    return timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if value[0] == '+' else -1)))

def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date must be YYYY-MM-DD in the user timezone')
    date.fromisoformat(value)
    return value

def validate_profile(profile):
    if profile is None:
        return
    if not isinstance(profile, dict):
        raise ValueError('profile must be an object or null')
    for key, lo, hi in [('age', 1, 120), ('height_cm', 50, 250), ('weight_kg', 10, 400)]:
        number(profile.get(key), key, lo, hi)
    if profile.get('sex_coefficient') not in ('male', 'female', 'range'):
        raise ValueError('sex_coefficient must be male, female, or range')
    if profile.get('activity') not in ACTIVITY or profile.get('goal') not in ('maintain', 'lose', 'gain'):
        raise ValueError('Unsupported activity or goal')
    flag(profile.get('special_care'), 'special_care')
    if 'goal_weight_kg' in profile:
        number(profile['goal_weight_kg'], 'goal_weight_kg', 10, 400)
    for key in ('health_note', 'medication_note'):
        if key in profile and not isinstance(profile[key], str):
            raise ValueError(f'{key} must be text')
    for key in ('allergies', 'preferences'):
        if key in profile and (not isinstance(profile[key], list) or not all(isinstance(v, str) for v in profile[key])):
            raise ValueError(f'{key} must be a list of strings')
    override = profile.get('target_override')
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError('target_override must be an object')
        for key in ('kcal', 'low', 'high'):
            number(override.get(key), key, 0, 20000)
        if not override['low'] <= override['kcal'] <= override['high']:
            raise ValueError('Invalid target_override bounds')
        nonempty(override.get('basis'), 'target_override.basis')

def estimate_target(profile):
    validate_profile(profile)
    if profile is None:
        return None
    if (not 19 <= profile['age'] <= 78 or profile['special_care'] or profile.get('_stale')
            or profile.get('_weight_missing') or profile.get('plan_state') == 'needs_review'):
        return None
    if profile['goal'] == 'lose' and profile['weight_kg'] / (profile['height_cm'] / 100) ** 2 < 18.5:
        return None
    if profile.get('target_override'):
        override = copy.deepcopy(profile['target_override'])
        if override['low'] < 1200:
            return None
        override['method'] = '用户提供的个人计划'
        return override
    baseline = 10 * profile['weight_kg'] + 6.25 * profile['height_cm'] - 5 * profile['age']
    coeff = {'male': (5, 5), 'female': (-161, -161), 'range': (-161, 5)}[profile['sex_coefficient']]
    factors = ACTIVITY[profile['activity']]
    low, high = (baseline + coeff[0]) * factors[0], (baseline + coeff[1]) * factors[1]
    if low < 1200 or low <= 0:
        return None
    basis = 'Mifflin–St Jeor 静息估算 × 活动情景系数；不是统计置信区间'
    if profile['goal'] != 'maintain':
        basis += '；当前为维持体重参考，目标计划待确定'
    return {'kcal': (low + high) / 2, 'low': low, 'high': high,
            'method': '维持体重的起始估算', 'basis': basis}

def new_day(state, day):
    profile = profiles.effective_profile(state, day, smooth=True)
    version = profiles.version_at(state, day)
    return {'target': estimate_target(profile), 'profile_snapshot': copy.deepcopy(profile),
            'profile_version_id': version['id'] if version else None,
            'complete': False, 'records': [], 'target_history': []}

def refresh_day(state, day, reason):
    if day not in state['days']:
        return
    prior = state['days'][day]
    fresh = new_day(state, day)
    prior.setdefault('target_history', []).append({'target': copy.deepcopy(prior['target']),
        'profile_snapshot': copy.deepcopy(prior.get('profile_snapshot')), 'reason': reason,
        'changed_at': datetime.now(timezone.utc).isoformat()})
    for key in ('target', 'profile_snapshot', 'profile_version_id'):
        prior[key] = fresh[key]

def summary(state, day):
    data = state['days'].get(day) or new_day(state, day)
    records = [r for r in data['records'] if not r.get('removed', False)]
    consumed = {key: math.fsum(item[key] * r['fraction'] for r in records for item in r['meal']['items'])
                for key in ('kcal', 'low', 'high')}
    target, remaining, comparison = data['target'], None, 'unavailable'
    # A preserved snapshot is audit data, not permission to keep applying a plan
    # after an effective health/profile change or an expired profile.
    if estimate_target(profiles.effective_profile(state, day, smooth=True)) is None:
        target = None
    provisional = sum(r['provisional'] for r in records)
    if target:
        remaining = {'kcal': target['kcal'] - consumed['kcal'],
                     'low': target['low'] - consumed['high'],
                     'high': target['high'] - consumed['low']}
        if consumed['low'] > target['high']:
            comparison, status = 'above', '已记录摄入的估算范围高于每日参考范围'
        elif consumed['high'] < target['low']:
            comparison, status = 'below', '已记录摄入的估算范围低于每日参考范围'
        else:
            comparison, status = 'overlap', '摄入与参考范围重叠，是否超出尚不确定'
    else:
        status = '记录模式，尚未设置适用的个人热量参考'
    status += '；' + ('用户已确认全天记全' if data['complete'] else '当天记录尚未确认完整')
    if provisional:
        status += f'；含 {provisional} 条暂估份量'
    review = profiles.review_info(state, day)
    note = profiles.review_note(review)
    if note:
        status += '；' + note
    maintenance_profile = profiles.effective_profile(state, day, smooth=True)
    if maintenance_profile:
        maintenance_profile.pop('target_override', None)
        maintenance_profile.pop('plan_state', None)
    return {'date': day, 'is_demo': state['is_demo'], 'complete': data['complete'],
            'count': len(records), 'provisional_count': provisional, 'target': target,
            'consumed': consumed, 'remaining': remaining, 'comparison': comparison, 'status': status,
            'target_snapshot': copy.deepcopy(data['target']),
            'review': review, 'review_note': note, 'weight_trend': profiles.weight_trend(state, day),
            'maintenance_reference': estimate_target(maintenance_profile),
            'records': [{'id': r['id'], 'title': r['meal']['title'], 'fraction': r['fraction'],
                         'provisional': r['provisional']} for r in records]}

def load_meal(path, state):
    meal = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    validate_meal(meal)
    if meal.get('is_demo', False) and not state['is_demo']:
        raise ValueError('Demo meals cannot enter a real log')
    meal.pop('daily', None)
    return meal

def find_record(data, record_id):
    for r in data['records']:
        if r['id'] == record_id:
            return r
    raise ValueError('Unknown meal id')

def execute(state, request):
    if not isinstance(request, dict):
        raise ValueError('request must be a JSON object')
    operation = request.get('operation')
    modified = False
    if operation == 'init':
        if state is not None:
            raise ValueError('State already exists; init does not overwrite it')
        validate_profile(request.get('profile'))
        zone = request.get('timezone_offset')
        offset(zone)
        state = {'version': 1, 'timezone_offset': zone,
                 'timezone_name': nonempty(request.get('timezone_name'), 'timezone_name'),
                 'is_demo': flag(request.get('is_demo', False), 'is_demo'),
                 'profile': copy.deepcopy(request.get('profile')), 'days': {}, 'history': []}
        modified = True
    if state is None or state.get('version') not in (1, 2):
        raise ValueError('Initialize a supported state first')
    day = request.get('date')
    if day is None and operation in ('init', 'set-profile'):
        day = datetime.now(offset(state['timezone_offset'])).date().isoformat()
    day = valid_date(day)
    migrating = state['version'] == 1
    state = profiles.upgrade(state, day)
    if migrating and operation not in ('summary', 'export-meal'):
        modified = True
    if operation in profiles.OPERATIONS:
        profiles.handle(state, request, day, validate_profile)
        if operation not in ('migrate', 'review-noted') and request.get('effective_date', day) <= day:
            refresh_day(state, day, operation)
        modified = True
    elif operation == 'set-profile':
        validate_profile(request.get('profile'))
        profile = copy.deepcopy(request.get('profile'))
        if profile is not None:
            profile.update({'profile_reviewed_on': day, 'plan_reviewed_on': day, 'plan_state': 'active',
                            'plan_anchor_weight_kg': profile['weight_kg']})
        profiles.append_version(state, profile, day, '兼容完整档案更新；旧日快照保留', day)
        if profile is not None:
            latest = profiles.weight_trend(state, day)['latest_kg']
            if latest != profile['weight_kg']:
                state['weights'].append({'id': profiles.identifier(), 'date': day, 'kg': profile['weight_kg'],
                                        'source': 'user_report', 'recorded_at': profiles.stamp(), 'removed': False})
            state['profile'] = profiles.effective_profile(state, day)
            state['profile'].pop('_stale', None)
        if 'timezone_offset' in request:
            offset(request['timezone_offset'])
            state['timezone_offset'] = request['timezone_offset']
            state['timezone_name'] = nonempty(request.get('timezone_name'), 'timezone_name')
        modified = True
    elif operation == 'summary':
        pass
    elif operation == 'init':
        pass
    elif operation in ('retarget', 'add', 'amend', 'remove', 'complete', 'export-meal'):
        if operation in ('amend', 'remove', 'export-meal') and day not in state['days']:
            raise ValueError('No records for that date')
        data = state['days'].setdefault(day, new_day(state, day))
        if operation == 'retarget':
            refresh_day(state, day, '用户明确重算该日参考')
        elif operation == 'complete':
            data['complete'] = flag(request.get('complete'), 'complete')
        elif operation == 'add':
            record_id = nonempty(request.get('id'), 'id')
            if any(r['id'] == record_id for r in data['records']):
                raise ValueError('Meal id already exists; use amend instead of adding again')
            paths = request.get('photo_paths', [])
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                raise ValueError('photo_paths must be an array of local paths')
            hashes = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]
            if not flag(request.get('new_occurrence', False), 'new_occurrence'):
                known = {h for r in data['records'] if not r.get('removed') for h in r['photo_hashes']}
                if known.intersection(hashes):
                    raise ValueError('Photo already logged today; amend it or confirm a new occurrence')
            data['records'].append({'id': record_id, 'meal': load_meal(request.get('meal_path'), state),
                                    'fraction': number(request.get('fraction', 1), 'fraction', 0, 1),
                                    'provisional': flag(request.get('provisional', True), 'provisional'),
                                    'photo_hashes': hashes, 'removed': False})
            data['complete'] = False
        else:
            record = find_record(data, request.get('id'))
            if record['removed']:
                raise ValueError('Meal has been removed')
            if operation == 'remove':
                record['removed'] = True
                data['complete'] = False
            elif operation == 'amend':
                if 'meal_path' in request:
                    record['meal'] = load_meal(request['meal_path'], state)
                if 'fraction' in request:
                    record['fraction'] = number(request['fraction'], 'fraction', 0, 1)
                if 'provisional' in request:
                    record['provisional'] = flag(request['provisional'], 'provisional')
                data['complete'] = False
            elif operation == 'export-meal':
                meal = copy.deepcopy(record['meal'])
                for i in meal['items']:
                    for key in ('kcal', 'low', 'high'):
                        i[key] *= record['fraction']
                if record['fraction'] != 1:
                    for i in meal['items']:
                        i['portion'] += f'；计入原份量的 {record["fraction"]:.0%}'
                        i['basis'] += f'；按本次食用比例 {record["fraction"]:.0%} 缩放热量'
                meal['portion_basis'] += f'；日志计入比例 {record["fraction"]:.0%}'
                destination = Path(nonempty(request.get('output_path'), 'output_path'))
                if destination.resolve() == Path(request.get('_state_path', '')).resolve():
                    raise ValueError('Cannot export a meal over the state file')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(meal, ensure_ascii=False, indent=2), encoding='utf-8')
        modified = operation != 'export-meal'
    else:
        raise ValueError('Unknown operation')
    if modified:
        state['history'].append({'operation': operation, 'date': day, 'id': request.get('id'),
                                 'at': datetime.now(timezone.utc).isoformat()})
    return state, summary(state, day), modified

def atomic_save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.output and args.output.resolve() == args.state.resolve():
            raise ValueError('Summary output cannot overwrite the state file')
        state = json.loads(args.state.read_text(encoding='utf-8-sig')) if args.state.exists() else None
        request = json.loads(args.request.read_text(encoding='utf-8-sig'))
        request['_state_path'] = str(args.state.resolve())
        state, result, modified = execute(state, request)
        if modified:
            atomic_save(args.state, state)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding='utf-8')
        print(text)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f'Cannot update log: {error}\n')

if __name__ == '__main__':
    main()
