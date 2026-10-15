import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/food-photo-card/scripts'))
import daily_log
import estimate_meal
import render_card

PROFILE = {'age': 30, 'height_cm': 175, 'weight_kg': 70, 'sex_coefficient': 'male',
           'activity': 'light', 'goal': 'maintain', 'special_care': False}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.meal_path = ROOT / 'examples/demo-meal.json'
        self.meal = json.loads(self.meal_path.read_text(encoding='utf-8'))

    def test_example_totals_and_html_escaping(self):
        self.assertEqual(sum(i['kcal'] for i in self.meal['items']), 510)
        result = render_card.render(self.meal)
        self.assertIn('380–670 kcal', result)
        self.assertNotIn('@@', result)
        self.meal['title'] = '<script>alert(1)</script>'
        self.assertNotIn('<script>', render_card.render(self.meal))

    def test_invalid_bounds_rejected(self):
        self.meal['items'][0]['low'] = 9999
        with self.assertRaises(ValueError):
            render_card.render(self.meal)

    def test_daily_reference_is_not_resting_energy(self):
        target = daily_log.estimate_target(PROFILE)
        self.assertAlmostEqual(target['low'], 1648.75 * 1.6)
        self.assertAlmostEqual(target['high'], 1648.75 * 1.8)
        self.assertIsNone(daily_log.estimate_target({**PROFILE, 'special_care': True}))
        self.assertIsNone(daily_log.estimate_target({**PROFILE, 'age': 16}))

    def test_log_amend_remove_and_next_day(self):
        state, _, _ = daily_log.execute(None, {
            'operation': 'init', 'date': '2026-10-15', 'is_demo': True,
            'timezone_offset': '+08:00', 'timezone_name': 'Asia/Shanghai', 'profile': PROFILE})
        add = {'operation': 'add', 'date': '2026-10-15', 'id': 'meal-1', 'meal_path': str(self.meal_path)}
        state, result, _ = daily_log.execute(state, add)
        self.assertEqual(result['consumed']['kcal'], 510)
        with self.assertRaises(ValueError):
            daily_log.execute(copy.deepcopy(state), add)
        state, result, _ = daily_log.execute(state, {'operation': 'amend', 'date': '2026-10-15', 'id': 'meal-1', 'fraction': .5})
        self.assertEqual(result['consumed']['kcal'], 255)
        self.assertEqual(daily_log.summary(state, '2026-10-16')['consumed']['kcal'], 0)
        state, result, _ = daily_log.execute(state, {'operation': 'remove', 'date': '2026-10-15', 'id': 'meal-1'})
        self.assertEqual(result['consumed']['kcal'], 0)

    def test_reference_density_and_image_portion_stay_distinct(self):
        observation = {'title': '肠粉测试', 'portion_basis': '按可见份量暂估', 'confidence': '份量为推测',
                       'comment': '示例', 'tip': '搭配蔬菜。', 'assumptions': ['无实测重量'], 'components': [{
                           'name': '粉皮', 'state': 'cooked',
                           'portion': {'g': 250, 'low_g': 200, 'high_g': 300, 'basis': '按图目测'},
                           'density': {'reference_id': 'cfs-10400200012', 'match': 'proxy', 'reason': '近似粉皮参照'}}]}
        meal = estimate_meal.build(observation)
        self.assertEqual(meal['items'][0]['kcal'], 275)
        self.assertEqual(meal['items'][0]['low'], 220)
        self.assertEqual(meal['items'][0]['high'], 330)
        self.assertEqual(meal['items'][0]['evidence']['portion'], 'image_estimate')
        self.assertEqual(len(meal['sources']), 1)
        observation['components'][0]['density'] = {'kcal_per_100g': 110, 'match': 'exact', 'reason': 'missing source'}
        with self.assertRaises(ValueError):
            estimate_meal.build(observation)


if __name__ == '__main__':
    unittest.main()
