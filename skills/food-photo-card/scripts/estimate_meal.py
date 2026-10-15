#!/usr/bin/env python3
"""Compute a traceable meal estimate from an assistant's image observations."""
import argparse
import json
import math
from pathlib import Path

from render_card import validate

CATALOG = Path(__file__).resolve().parent.parent / 'references/nutrient-catalog.json'

def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(label + ' must be a nonnegative finite number')
    return value

def required_text(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key + ' must be a nonempty string')
    return value

def build(observation, catalog=None):
    catalog = catalog or json.loads(CATALOG.read_text(encoding='utf-8'))
    records = {r['id']: r for r in catalog['records']}
    if not isinstance(observation.get('components'), list) or not observation['components']:
        raise ValueError('components must contain the image-based food breakdown')
    sources, items, evidence = {}, [], []
    for component in observation['components']:
        name = required_text(component, 'name')
        grams = component['portion']
        unit = grams.get('unit', 'g')
        if unit not in ('g', 'ml'):
            raise ValueError('Portion unit must be g or ml')
        center_key, lower_key, upper_key = unit, 'low_' + unit, 'high_' + unit
        for key in (center_key, lower_key, upper_key):
            finite(grams.get(key), key)
        amount, low_amount, high_amount = grams[center_key], grams[lower_key], grams[upper_key]
        if not low_amount <= amount <= high_amount:
            raise ValueError('Portion bounds must contain the central estimate')
        scale_basis = required_text(grams, 'basis')
        densities = [component['density']] + component.get('alternatives', [])
        values, details, kinds = [], [], []
        for density in densities:
            match = density.get('match')
            if match not in ('exact', 'proxy', 'experience'):
                raise ValueError('density.match must be exact, proxy, or experience')
            why = required_text(density, 'reason')
            if 'reference_id' in density:
                if match == 'experience':
                    raise ValueError('Reference values must state exact or proxy matching')
                record = records[density['reference_id']]
                if record.get('basis_unit', 'g') != unit:
                    raise ValueError('Cannot apply per-100g data directly to ml; use a matching unit or explicit density conversion')
                density_key = 'kcal_per_100' + unit
                value = finite(record[density_key], density_key)
                # Raw-to-cooked substitution requires an explicit separate yield calculation.
                if component.get('state') == 'cooked' and 'raw' in record['food_name'].casefold():
                    raise ValueError('Cannot apply raw density directly to cooked grams')
                source = record['source']
                if source.get('kind') == 'image_label':
                    required_text(source, 'image_reference')
                    origin = '图中营养标签：' + source['image_reference']
                else:
                    sources[record['id']] = {'label': source['label'], 'url': source['url']}
                    origin = record['food_name']
                details.append(f'[{record["id"]}] {origin}，{value:g} kcal/100{unit}；' + ('对应条目' if match == 'exact' else '近似食材/做法参照') + '；' + why)
            else:
                if match != 'experience':
                    raise ValueError('Unverified manual densities must be labeled experience')
                value = finite(density.get('kcal_per_100' + unit), 'experience density')
                details.append(f'经验情景 {value:g} kcal/100{unit}（未查证此配方）；{why}')
            values.append(value)
            if value > 1000:
                raise ValueError('Density exceeds 1000 kcal/100g; check kJ versus kcal and serving units')
            kinds.append(match)
        kcal = amount * values[0] / 100
        low, high = low_amount * min(values) / 100, high_amount * max(values) / 100
        basis = f'照片份量推测：{scale_basis}；中心计算 {amount:g}{unit} × {values[0]:g}/100 = {kcal:g} kcal。' + '；'.join(details)
        item = {'name': name, 'portion': f'约 {amount:g}{unit}（照片推测）', 'kcal': kcal, 'low': low, 'high': high, 'basis': basis,
                'evidence': {'density_matches': kinds, 'portion': 'image_estimate', 'reference_ids': [d['reference_id'] for d in densities if 'reference_id' in d]}}
        items.append(item)
        evidence.extend(kinds)
    if all(e == 'experience' for e in evidence):
        label = '经验情景估算'
    elif any(e in ('experience', 'proxy') for e in evidence):
        label = '食物数据参照＋配方推测'
    else:
        label = '食物数据参照＋照片份量推测'
    result = {key: observation[key] for key in ('title', 'portion_basis', 'confidence', 'comment', 'tip', 'assumptions')}
    result.update({'items': items, 'sources': list(sources.values()), 'is_demo': observation.get('is_demo', False),
                   'meal_label': observation.get('meal_label', '拍照估算'),
                   'assessment': {'method': label, 'note': '食物热量密度与照片份量分别处理；范围是情景包络，不是实测误差或置信区间。',
                                  'observations': observation.get('visual_evidence', []),
                                  'uncertain': observation.get('uncertain', [])}})
    validate(result)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        observation = json.loads(args.input.read_text(encoding='utf-8-sig'))
        catalog = json.loads(args.catalog.read_text(encoding='utf-8-sig')) if args.catalog else None
        result = build(observation, catalog)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        print(args.output.resolve())
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f'Cannot estimate meal: {error}\n')

if __name__ == '__main__':
    main()
