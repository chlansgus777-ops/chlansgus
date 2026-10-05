"""Independent regression cases for source 059b465; four expected failures.

Run with Python 3.11+ (tzdata on Windows):
  python marketlens_review_repro.py --source PATH_TO_EXTRACTED_SOURCE
No app dependencies, network, credentials or source edits are required.
The _live_plan case executes that method extracted verbatim by AST; it is
an isolated method test, not a running server or desktop integration test.
"""
import argparse
import ast
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
args, test_args = parser.parse_known_args()
root = Path(args.source).resolve()
sys.path.insert(0, str(root / 'backend'))
from marketlens.application.live_judge import LivePlan, LiveJudge, judge
from marketlens.domain.entry import build_entry_plan
from marketlens.domain.enums import BULLISH_ACTIONS
from marketlens.domain.indicators import TechnicalSnapshot

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)

def plan(**overrides):
    return LivePlan(**(dict(ticker='TEST', rec_id=1,
        as_of=NOW-timedelta(minutes=30), action='BUY', bullish=True,
        data_ok=True, rec_price=100.0, max_buy=103.0, stop=80.0,
        target1=150.0, ideal_entry=99.0, min_rr=2.0) | overrides))

class IndependentReview(unittest.TestCase):
    def test_F01_rejudged_plan_uses_matching_price_basis(self):
        tree = ast.parse((root/'backend/marketlens/application/services.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='MarketLensService')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='_live_plan')
        namespace = {'Any': object, 'BULLISH_ACTIONS': BULLISH_ACTIONS}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(root/'backend/marketlens/application/services.py'), 'exec'), namespace)
        live = dict(action='BUY', current_status='CURRENT', price=96.,
                    max_buy=100., stop=90., target1=120., ideal_entry=95.)
        svc = SimpleNamespace(rejudged=lambda rid: live)
        actual = namespace['_live_plan'](svc, plan(max_buy=103., stop=80., target1=150.))
        result = judge(actual, 96., NOW, NOW)
        print('\nF01:', result['valid_now'], result['problems'])
        self.assertTrue(result['valid_now'], 'CURRENT rejudgement contradicted by live chip using old rec_price')

    def test_F02_exact_boundary_survives_stored_plan_conversion(self):
        values = {f.name: None for f in fields(TechnicalSnapshot)}
        values.update(atr14=2., supports=(98.006,), resistances=(106.006,))
        entry = build_entry_plan(100.005, TechnicalSnapshot(**values))
        self.assertIsNotNone(entry)
        self.assertTrue(entry.in_buy_zone)
        self.assertGreater(100.008, entry.max_buy_exact)
        # plans_from_rows/levels_now pass these rounded display fields to LivePlan.
        actual = plan(rec_price=100.005, max_buy=entry.max_buy,
                      stop=entry.stop, target1=entry.target1)
        result = judge(actual, 100.008, NOW, NOW)
        print('\nF02:', 'exact max=', entry.max_buy_exact,
              'display max=', entry.max_buy, 'valid_now=', result['valid_now'])
        self.assertFalse(result['valid_now'], 'rounded max buy admits a price above the exact maximum')

    def test_F03_large_move_requests_reanalysis_without_zone_change(self):
        monitor = LiveJudge(now=lambda: NOW)
        requested = []
        monitor.on_reanalyze = lambda ticker, why: requested.append((ticker, why))
        monitor.set_plans([plan()])
        monitor.observe('TEST', 100., NOW)
        self.assertTrue(judge(plan(), 94., NOW, NOW)['needs_reanalysis'])
        monitor.observe('TEST', 94., NOW)
        print('\nF03:', 'requested=', requested)
        self.assertTrue(requested, '6% move stays BUY_ZONE, so observe returns before the callback')

    def test_F04_boundary_noise_does_not_repeat_buy_notification(self):
        monitor = LiveJudge(now=lambda: NOW)
        monitor.set_plans([plan(max_buy=102., stop=92., target1=125.)])
        for price in (102.01, 101.99, 102.01, 101.99, 102.01, 101.99):
            monitor.observe('TEST', price, NOW)
        buys = [a for a in monitor.alerts() if a['kind']=='BUY_ZONE']
        print('\nF04:', 'BUY_ZONE notifications=', len(buys))
        self.assertLessEqual(len(buys), 1, 'same timestamp boundary noise produces three buy notifications')

    def test_control_fresh_price_valid_plan_is_actionable(self):
        self.assertTrue(judge(plan(), 100., NOW, NOW)['valid_now'])

    def test_control_stale_price_blocks_action(self):
        self.assertFalse(judge(plan(), 100., NOW-timedelta(minutes=25), NOW)['valid_now'])

if __name__=='__main__':
    unittest.main(argv=[sys.argv[0], *test_args], verbosity=2)
