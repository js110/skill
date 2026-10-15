#!/usr/bin/env python3
"""Render an offline food estimate card. Image understanding happens upstream."""
import argparse
import base64
import html
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

COLORS = ['#397154', '#e6b94e', '#9bb77b', '#df925e', '#78a8a0', '#b3a0bb']

ILLUSTRATION = '''<svg viewBox="0 0 458 188" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="餐盘示意插画，非用户食物照片">
<path d="M0 149 Q130 119 458 158V188H0Z" fill="#caddc7"/>
<g transform="translate(226 88) rotate(-12)"><ellipse cy="13" rx="133" ry="73" fill="#abc3aa" opacity=".6"/><ellipse rx="132" ry="73" fill="#fffef8"/><ellipse rx="110" ry="58" fill="#e5eddf"/>
<path d="M-96 4Q-91-46-22-46Q2-23-5 39Q-76 56-96 4" fill="#faf5dd"/>
<g stroke="#e0d5ae" stroke-width="2" stroke-linecap="round"><path d="M-70-22l9 2m-5 12l8 1m-21 7l8 3m18-34l8 3m-5 13l8 1m-3 13l8 2m-37 18l8 2m16-8l8 1m-13 18l8 2m-22-46l8 3"/></g>
<g fill="#dba668" stroke="#bb8044" stroke-width="2"><path d="M3-39l37-8 29 25-41 20Z"/><path d="M9-16l36-10 29 26-40 16Z"/><path d="M18 10l37-11 25 24-37 18Z"/></g>
<g fill="#538d51"><circle cx="-2" cy="-43" r="15"/><circle cx="21" cy="-49" r="13"/><circle cx="77" cy="-17" r="15"/><circle cx="91" cy="0" r="13"/><circle cx="-5" cy="35" r="14"/></g>
<g fill="#e57a51"><ellipse cx="-32" cy="40" rx="13" ry="9"/><ellipse cx="76" cy="29" rx="12" ry="9"/></g><g fill="#7ba85f"><circle cx="2" cy="-48" r="7"/><circle cx="81" cy="-20" r="7"/><circle cx="-9" cy="32" r="7"/></g></g>
<g stroke="#6b8b6d" stroke-width="3" stroke-linecap="round"><path d="M369 33l-30 123m44-120l-30 123"/></g><path d="M64 40q-13-18-23-2q-7 12 11 24q19-5 12-22" fill="#ffdb72"/></svg>'''

def esc(value):
    return html.escape(str(value), quote=True)

def text_field(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key} must be a nonempty string')
    return value

def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Input must be a JSON object')
    for key in ['title', 'portion_basis', 'confidence', 'comment', 'tip']:
        text_field(data, key)
    if 'is_demo' in data and not isinstance(data['is_demo'], bool):
        raise ValueError('is_demo must be a boolean')
    if 'meal_label' in data:
        text_field(data, 'meal_label')
    if not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('items must be a nonempty list')
    for item in data['items']:
        if not isinstance(item, dict):
            raise ValueError('Each item must be an object')
        for key in ['name', 'portion', 'basis']:
            text_field(item, key)
        for key in ['low', 'kcal', 'high']:
            n = item.get(key)
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
                raise ValueError(f'{key} must be a finite number')
        if not 0 <= item['low'] <= item['kcal'] <= item['high']:
            raise ValueError('Each item must satisfy 0 <= low <= kcal <= high')
    if not isinstance(data.get('assumptions'), list) or not data['assumptions'] or not all(isinstance(x, str) and x.strip() for x in data['assumptions']):
        raise ValueError('assumptions must be a nonempty list of strings')
    if not isinstance(data.get('sources'), list):
        raise ValueError('sources must be a list')
    for source in data['sources']:
        if not isinstance(source, dict):
            raise ValueError('Each source must be an object')
        text_field(source, 'label')
        url = urlsplit(text_field(source, 'url'))
        if url.scheme not in ('https', 'http') or not url.netloc:
            raise ValueError('Source URLs must be HTTP(S)')
    if 'assessment' in data:
        assessment = data['assessment']
        if not isinstance(assessment, dict):
            raise ValueError('assessment must be an object')
        for key in ('method', 'note'):
            text_field(assessment, key)
        for key in ('observations', 'uncertain'):
            if not isinstance(assessment.get(key), list) or not all(isinstance(v, str) for v in assessment[key]):
                raise ValueError('assessment observations/uncertain must be lists of strings')

def rounded(n):
    return str(int(math.floor(n / 10 + 0.5) * 10))

def photo_html(path):
    path = Path(path)
    formats = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}
    mime = formats.get(path.suffix.lower())
    if not mime:
        raise ValueError('Photo must be JPEG, PNG, WebP, or GIF')
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'<img src="data:{mime};base64,{encoded}" alt="用户提供的本餐食物照片">'

def daily_html(daily):
    if not isinstance(daily, dict):
        raise ValueError('Daily summary must be an object')
    for key in ('date', 'status'):
        text_field(daily, key)
    consumed, target = daily.get('consumed'), daily.get('target')
    def check_values(values, label, allow_negative=False):
        if not isinstance(values, dict):
            raise ValueError(f'{label} must be an object')
        for key in ('kcal', 'low', 'high'):
            n = values.get(key)
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
                raise ValueError(f'{label}.{key} must be finite')
        if not values['low'] <= values['kcal'] <= values['high'] or (not allow_negative and values['low'] < 0):
            raise ValueError(f'{label} has invalid bounds')
    check_values(consumed, 'consumed')
    review = daily.get('review', {})
    context = ''
    if review.get('profile_reviewed_on'):
        context = f'<p class="daily-note">个人资料复核于 {esc(review["profile_reviewed_on"])}；参考随有效记录更新。</p>'
    if target is None:
        return f'<section class="daily"><h2>{esc(daily["date"])} · 今日记录</h2><p>已记录约 <b>{rounded(consumed["kcal"])}</b> kcal</p><p class="daily-note">{esc(daily["status"])}</p>{context}</section>'
    check_values(target, 'target')
    remaining = daily.get('remaining')
    check_values(remaining, 'remaining', allow_negative=True)
    expected = {'kcal': target['kcal'] - consumed['kcal'], 'low': target['low'] - consumed['high'], 'high': target['high'] - consumed['low']}
    if not all(math.isclose(remaining[k], expected[k], abs_tol=1e-6) for k in expected):
        raise ValueError('Daily remaining does not match target minus consumed')
    ratio = consumed['kcal'] / target['kcal'] * 100 if target['kcal'] else 0
    remain_label = '剩余参照' if remaining['kcal'] >= 0 else '超出参考中心'
    remain_value = rounded(abs(remaining['kcal']))
    rows = f'''<div class="daily-grid"><div><span>每日参考</span><b>{rounded(target['kcal'])}</b></div><div><span>今日已记录</span><b>{rounded(consumed['kcal'])}</b></div><div><span>{remain_label}</span><b>{remain_value}</b></div></div>'''
    return f'''<section class="daily"><h2>{esc(daily['date'])} · 每日进度 <small>kcal</small></h2>{rows}
<div class="daily-track" role="img" aria-label="已记录摄入约为参考中心值的 {ratio:.0f}%"><i style="width:{min(100, max(0, ratio)):.4f}%"></i></div>
<p class="daily-note">参考范围 {rounded(target['low'])}–{rounded(target['high'])}；已记录范围 {rounded(consumed['low'])}–{rounded(consumed['high'])}。<br>剩余参照范围 {rounded(remaining['low'])}–{rounded(remaining['high'])} kcal。</p>
<p class="daily-status">{esc(daily['status'])}</p><p class="daily-note">参照值不是停食或必须吃满的指令，按饥饿感正常进餐。</p>
{context}<details><summary>每日参考怎么算</summary><p>{esc(target.get('method', '个人参考'))}：{esc(target.get('basis', ''))}</p></details></section>'''

def render(data, photo=None, daily=None):
    validate(data)
    totals = {key: sum(item[key] for item in data['items']) for key in ['kcal', 'low', 'high']}
    if not all(math.isfinite(value) for value in totals.values()):
        raise ValueError('Summed calories must be finite')
    rows, bars = [], []
    for index, item in enumerate(data['items']):
        color = COLORS[index % len(COLORS)]
        share = item['kcal'] / totals['kcal'] * 100 if totals['kcal'] else 0
        bars.append(f'<i style="width:{share:.6f}%;background:{color}"></i>')
        rows.append(f'<li><span class="dot" style="background:{color}"></span><span class="food-name">{esc(item["name"])}<span class="portion-small">{esc(item["portion"])}</span></span><span class="food-kcal">约 {rounded(item["kcal"])} <small>kcal</small></span></li>')
    basis = '<ul>' + ''.join(f'<li>{esc(i["name"])}：{esc(i["basis"])}；原始估算 {i["kcal"]:g} kcal，范围 {i["low"]:g}–{i["high"]:g} kcal。</li>' for i in data['items']) + '</ul>'
    if data['sources']:
        basis += '<ul>' + ''.join(f'<li><a href="{esc(s["url"])}" target="_blank" rel="noopener noreferrer">{esc(s["label"])}</a></li>' for s in data['sources']) + '</ul>'
    else:
        label_refs = any('图中营养标签：' in i['basis'] for i in data['items'])
        basis += '<p>参照图中营养标签；份量按图片情景估算。</p>' if label_refs else '<p>经验估算，未核对数据库。</p>'
    assessment = data.get('assessment')
    evidence_html = ''
    if assessment:
        evidence_html = f'<p class="evidence-label">{esc(assessment["method"])}</p>'
        basis += f'<p>{esc(assessment["note"])}</p>'
        if assessment['observations']:
            basis += '<p>可见证据：' + '；'.join(esc(v) for v in assessment['observations']) + '。</p>'
        if assessment['uncertain']:
            basis += '<p>未确定项：' + '；'.join(esc(v) for v in assessment['uncertain']) + '。</p>'
    daily = daily if daily is not None else data.get('daily')
    demo = data.get('is_demo', False)
    daily_demo = bool(daily and daily.get('is_demo'))
    replacements = {
        'TITLE': esc(data['title']), 'BADGE': '示例 · 非实测' if demo else ('日记录演示' if daily_demo else esc(data.get('meal_label', '这一餐'))),
        'HERO': photo_html(photo) if photo else ILLUSTRATION,
        'PHOTO_CLASS': 'has-photo' if photo else '',
        'HERO_LABEL': '原始食物照片' if photo else '餐盘示意插画 · 非食物实拍',
        'PORTION': esc(data['portion_basis']), 'TOTAL': rounded(totals['kcal']),
        'LOW': rounded(totals['low']), 'HIGH': rounded(totals['high']), 'CONFIDENCE': esc(data['confidence']),
        'BARS': ''.join(bars), 'ITEMS': ''.join(rows), 'COMMENT': esc(data['comment']), 'TIP': esc(data['tip']),
        'ASSUMPTIONS': '<br>'.join(esc(x) for x in data['assumptions']), 'BASIS': basis,
        'DAILY': daily_html(daily) if daily is not None else '',
        'EVIDENCE': evidence_html,
        'DEMO_NOTE': ('<span class="demo-note">演示数据，不代表对真实照片的分析。</span><br>' if demo else ('<span class="demo-note">个人档案与当日累计为功能演示，不是用户真实日记录。</span><br>' if daily_demo else '')),
    }
    template = (Path(__file__).resolve().parent.parent / 'assets' / 'card.html').read_text(encoding='utf-8')
    return re.sub(r'@@([A-Z_]+)@@', lambda match: replacements[match.group(1)], template)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--photo', type=Path)
    parser.add_argument('--daily-json', type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        daily = json.loads(args.daily_json.read_text(encoding='utf-8-sig')) if args.daily_json else None
        result = render(data, args.photo, daily)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding='utf-8')
    except (ValueError, OSError, TypeError) as error:
        parser.exit(2, f'Cannot render card: {error}\n')
    print(args.output.resolve())

if __name__ == '__main__':
    main()
