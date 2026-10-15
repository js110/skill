#!/usr/bin/env python3
"""Read public CFS food listings and individual nutrient records; no food photos are sent."""
import argparse
import hashlib
import html
import json
import re
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://www.cfs.gov.hk/english/nutrient/'

def fetch(page, fields=None):
    request = Request(BASE + page, data=urlencode(fields).encode() if fields else None,
                      headers={'User-Agent': 'FoodPhotoCard/1.0', 'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Unexpectedly large CFS response')
    return raw

def plain(value):
    return ' '.join(html.unescape(re.sub('<[^>]*>', ' ', value)).split())

def search(query, limit=8):
    raw = fetch('fc-search3.php').decode('utf-8-sig')
    records = []
    pattern = r'''<a\b[^>]*href="javascript:tosubmit\s*\('([^']+)','([^']+)','([^']+)'\);\s*"[^>]*>(.*?)</a>'''
    for group, subgroup, food_id, label in re.findall(pattern, raw, re.I | re.S):
        label = re.sub(r'^\d+\s+', '', plain(label))
        if all(word.casefold() in label.casefold() for word in query.split()):
            records.append({'group': group, 'subgroup': subgroup, 'food_id': food_id, 'food_name': label})
    return records[:limit]

def get_record(group, subgroup, food_id):
    if not all(re.fullmatch('[A-Za-z0-9]+', v or '') for v in (group, subgroup, food_id)):
        raise ValueError('Invalid CFS record identifiers')
    raw = fetch('fc-myreport.php', {'fg_id': group, 'fsg_id': subgroup, 'food_id': food_id, 'forFC': '1'})
    content = raw.decode('utf-8-sig')
    name = re.search(r'Food\s*Item:\s*</[^>]+>\s*<[^>]+>(.*?)</', content, re.I | re.S)
    # This endpoint expects POST; GET responses can be blank even with HTTP 200.
    energy_rows = [r for r in re.findall(r'<tr\b[^>]*>.*?</tr>', content, re.I | re.S) if 'Energy*' in r]
    if not energy_rows:
        raise ValueError('No energy row found; no value was inferred')
    cells = [plain(v) for v in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', energy_rows[0], re.I | re.S)]
    if len(cells) != 3 or cells[1] != 'kcal' or not re.fullmatch(r'\d+(?:\.\d+)?', cells[2]):
        raise ValueError('Missing or ambiguous CFS energy value; no value was inferred')
    alltext = plain(content)
    food = re.search(r'Food Item:\s*(.*?)\s*Data Source:', alltext)
    source = re.search(r'Data Source:\s*(.*?)\s*Portion:', alltext)
    if not food or not source or '100 grams of edible portion' not in alltext:
        raise ValueError('Cannot verify food identity or per-100g unit')
    return {'id': 'cfs-' + food_id, 'food_name': food.group(1), 'kcal_per_100g': float(cells[2]),
            'source': {'label': '香港食物安全中心：' + food.group(1) + '；代码 ' + food_id,
                       'url': BASE + 'fc-search3.php', 'provider': source.group(1),
                       'food_id': food_id, 'group': group, 'subgroup': subgroup,
                       'retrieved_on': date.today().isoformat(), 'retrieval': 'public form POST; per 100g edible portion',
                       'response_sha256': hashlib.sha256(raw).hexdigest()}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit', type=int, default=8)
    g = sub.add_parser('get'); g.add_argument('--group', required=True); g.add_argument('--subgroup', required=True); g.add_argument('--food-id', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        result = search(args.query, max(1, min(20, args.limit))) if args.command == 'search' else get_record(args.group, args.subgroup, args.food_id)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            from pathlib import Path
            path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding='utf-8')
        print(text)
    except (OSError, ValueError) as error:
        parser.exit(2, f'CFS lookup unavailable: {error}\n')

if __name__ == '__main__':
    main()
