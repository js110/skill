import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/food-photo-card/scripts'))
import daily_log as log
import profile_history as history
import render_card

PROFILE = {'age': 35, 'height_cm': 172, 'weight_kg': 80, 'sex_coefficient': 'male',
           'activity': 'sedentary', 'goal': 'lose', 'special_care': False,
           'target_override': {'kcal': 2000, 'low': 2000, 'high': 2000, 'basis': '虚构测试计划'}}

class ProfileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.state, _, _ = log.execute(None, {'operation': 'init', 'date': '2026-10-01',
            'timezone_offset': '+08:00', 'timezone_name': 'Asia/Shanghai', 'is_demo': True, 'profile': PROFILE})

    def op(self, operation, day='2026-10-08', **fields):
        self.state, result, _ = log.execute(copy.deepcopy(self.state), {'operation': operation, 'date': day, **fields})
        return result

    def test_legacy_migration_preserves_numbers_and_does_not_invent_old_profiles(self):
        legacy = {'version': 1, 'profile': PROFILE, 'days': {'2026-10-01': {'target': PROFILE['target_override'], 'complete': False, 'records': []}},
                  'history': [{'operation': 'init', 'date': '2026-10-01'}], 'is_demo': True,
                  'timezone_offset': '+08:00', 'timezone_name': 'Asia/Shanghai'}
        old = copy.deepcopy(legacy['days'])
        migrated, summary, _ = log.execute(legacy, {'operation': 'migrate', 'date': '2026-10-08'})
        self.assertEqual(migrated['version'], 2)
        self.assertEqual(migrated['days'], old)
        self.assertEqual(summary['weight_trend']['latest_kg'], 80)
        self.assertIsNone(history.effective_profile(migrated, '2026-09-30'))

    def test_new_weight_recalculates_maintenance_and_reviews_fixed_budget(self):
        old = log.summary(self.state, '2026-10-01')['maintenance_reference']['kcal']
        result = self.op('weight', id='w1', value=78, unit='kg')
        self.assertEqual(result['weight_trend']['latest_kg'], 78)
        self.assertLess(result['maintenance_reference']['kcal'], old)
        self.assertTrue(result['review']['plan_needs_review'])
        self.assertIsNone(result['target'])
        self.assertEqual(history.effective_profile(self.state, '2026-10-01')['weight_kg'], 80)

    def test_new_measurement_does_not_rewrite_existing_old_day(self):
        self.op('complete', day='2026-10-01', complete=False)
        snapshot = copy.deepcopy(self.state['days']['2026-10-01'])
        self.op('weight', id='w1', value=78)
        self.assertEqual(self.state['days']['2026-10-01'], snapshot)

    def test_same_day_profile_changes_revise_reference_without_changing_food(self):
        self.op('add', id='meal', meal_path=str(ROOT/'examples/demo-meal.json'))
        before = copy.deepcopy(self.state['days']['2026-10-08'])
        result = self.op('update-profile', changes={'activity': 'active'})
        self.assertIsNone(result['target'])
        self.assertEqual(result['consumed']['kcal'], 510)
        self.assertEqual(self.state['days']['2026-10-08']['records'], before['records'])
        self.assertEqual(self.state['days']['2026-10-08']['target_history'][-1]['target']['kcal'], 2000)
        self.assertEqual(history.effective_profile(self.state, '2026-10-01')['activity'], 'sedentary')

    def test_health_pause_and_explicit_plan_reconfirmation(self):
        self.op('update-profile', changes={'special_care': True, 'health_note': '虚构测试健康变化'})
        paused = self.op('summary')
        self.assertIsNone(paused['target'])
        self.assertNotIn('<b>2000</b>', render_card.daily_html(paused))
        with self.assertRaises(ValueError):
            self.op('confirm-plan', confirmed=True, target_override=PROFILE['target_override'])
        self.op('weight', id='while-paused', value=79.9)
        self.assertIsNone(self.op('summary')['target'])
        self.op('update-profile', changes={'special_care': False, 'health_note': '虚构测试：已复核'})
        self.assertIsNone(self.op('summary')['target'])
        result = self.op('confirm-plan', confirmed=True, target_override=PROFILE['target_override'])
        self.assertEqual(result['target']['kcal'], 2000)

    def test_smoothing_and_duplicate_weight(self):
        self.op('weight', day='2026-10-02', id='a', value=79.8)
        self.op('weight', day='2026-10-03', id='b', value=80.1)
        trend = self.op('summary', day='2026-10-03')['weight_trend']
        self.assertAlmostEqual(trend['mean_7d_kg'], (80+79.8+80.1)/3)
        self.assertIsNone(trend['change_between_7d_means_kg'])
        self.assertEqual(history.effective_profile(self.state, '2026-10-03')['weight_kg'], 80.1)
        with self.assertRaises(ValueError):self.op('weight', id='a', value=79.8)

    def test_jin_conversion_backfill_and_future_profile(self):
        self.op('weight', id='old', effective_date='2026-09-28', value=162, unit='jin')
        self.assertEqual(self.op('summary', day='2026-09-28')['weight_trend']['latest_kg'], 81)
        self.op('update-profile', effective_date='2026-10-20', changes={'activity': 'active'})
        self.assertEqual(history.effective_profile(self.state, '2026-10-08')['activity'], 'sedentary')
        self.assertEqual(history.effective_profile(self.state, '2026-10-20')['activity'], 'active')

    def test_correct_weight_keeps_original_record(self):
        self.op('weight', id='mistake', value=77)
        self.op('weight', id='correct', replace_id='mistake', value=79.5)
        self.assertTrue(next(r for r in self.state['weights'] if r['id']=='mistake')['removed'])
        self.assertEqual(self.op('summary')['weight_trend']['latest_kg'], 79.5)

    def test_review_frequency_and_no_fabricated_measurements(self):
        result = self.op('summary')
        self.assertIn('weight', result['review']['prompts'])
        size = len(self.state['weights'])
        self.op('review-noted', keys=['weight'])
        self.assertNotIn('weight', self.op('summary', day='2026-10-09')['review']['prompts'])
        self.assertIn('weight', self.op('summary', day='2026-10-15')['review']['prompts'])
        self.assertEqual(len(self.state['weights']), size)

    def test_stale_profiles_need_review_and_weight_does_not_clear_health_freshness(self):
        result = self.op('summary', day='2026-12-31')
        self.assertTrue(result['review']['stale'])
        self.assertIsNone(result['target'])
        self.op('weight', day='2026-12-31', id='late', value=79.9)
        self.assertTrue(self.op('summary', day='2026-12-31')['review']['stale'])
        self.op('review-profile', day='2026-12-31', confirmed=True)
        self.assertFalse(self.op('summary', day='2026-12-31')['review']['stale'])

    def test_weight_only_changes_automatic_maintenance_reference(self):
        self.op('confirm-plan', day='2026-10-01', confirmed=True, target_override=None)
        before = self.op('summary', day='2026-10-01')['target']['kcal']
        result = self.op('weight', id='new-auto', value=79.5)
        self.assertLess(result['target']['kcal'], before)
        self.assertFalse(result['review']['plan_needs_review'])

    def test_old_saved_budget_does_not_override_stale_profile(self):
        self.op('complete', day='2026-10-01', complete=False)
        self.state['days']['2026-12-31'] = copy.deepcopy(self.state['days']['2026-10-01'])
        result = self.op('summary', day='2026-12-31')
        self.assertIsNone(result['target'])
        self.assertEqual(result['target_snapshot']['kcal'], 2000)
        self.assertEqual(result['comparison'], 'unavailable')

    def test_invalid_units_and_goal_weight_rejected(self):
        with self.assertRaises(ValueError):self.op('weight', id='bad', value=80, unit='lb')
        with self.assertRaises(ValueError):self.op('update-profile', changes={'goal_weight_kg': -1})
        with self.assertRaises(ValueError):self.op('weight', id='nan', value=float('nan'))

    def test_unchanged_profile_patch_does_not_invalidate_budget(self):
        result = self.op('update-profile', changes={'activity': 'sedentary'})
        self.assertEqual(result['target']['kcal'], 2000)
        self.assertFalse(result['review']['plan_needs_review'])

if __name__ == '__main__':
    unittest.main()
