"""Independent regression expectations for MarketLens 0444a21.

Usage: python marketlens_0444a21_repro.py --source EXTRACTED_SOURCE
Requires Python 3.11+, numpy, and timezone data. No pytest/server/account needed.
Nine tests are expected to FAIL on unmodified 0444a21. Synthetic prices are
only defect fixtures, never backtest performance. Source files are not edited.
"""
import argparse
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
args, rest = parser.parse_known_args()
sys.path.insert(0, str(Path(args.source).resolve() / 'backend'))
from marketlens.backtest import portfolio as P
from marketlens.application.momentum_book import MomentumBook
from marketlens.application.strategy_signals import StrategySignals, COST
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day

UTC = timezone.utc

def sec(key, days, prices, sector='Tech', dividends=()):
    return P.make_sec(key, key, sector, days,
                      [(p,p,p,p,1_000_000) for p in prices], [], dividends)

def last_sessions(n, end):
    out=[]
    while len(out)<n:
        if is_trading_day(end):
            out.append(end)
        end-=timedelta(days=1)
    return list(reversed(out))

def loader(data):
    return lambda start,end: {t:[b for b in bs if start<=b.day<=end] for t,bs in data.items()}

def momentum_world():
    days=[]
    d=date(2024,1,1)
    while d<=date(2026,11,6):
        if is_trading_day(d):
            days.append(d)
        d+=timedelta(days=1)
    data={}
    sectors={}
    for group in ('OLD','NEW'):
        for i in range(20):
            t=f'{group}{i:02d}'
            sectors[t]=f'Sector{i%4}'
            def price(day):
                if group=='OLD':
                    return 110. if day>=date(2026,1,1) else 100.
                return 120. if day>=date(2026,8,15) else 100.
            data[t]=[Bar(d,price(d),price(d),price(d),price(d),1e6) for d in days]
    data['SPY']=[Bar(d,400.,400.,400.,400.,1e8) for d in days]
    return data,sectors

def pullback_world():
    days=last_sessions(280,date(2026,9,24))
    closes=[40+.15*i for i in range(len(days))]
    closes[-2]=closes[-3]*.97
    closes[-1]=closes[-2]*.97
    data={'PULL':[Bar(d,p,p*1.002,p*.998,p,2e6) for d,p in zip(days,closes)],
          'SPY':[Bar(d,400+i,401+i,399+i,400+i,1e8) for i,d in enumerate(days)]}
    return data,closes[-1]

class Review0444a21(unittest.TestCase):
    def test_F01_future_bars_must_not_change_past_equity(self):
        days=[date(2024,1,2)+timedelta(days=i) for i in range(11)
              if (date(2024,1,2)+timedelta(days=i)).weekday()<5]
        spy=sec('SPY',days,[100.]*len(days))
        short=sec('X',days[:2],[100.,100.])
        extended=sec('X',days[:2]+[date(2024,1,16)],[100.,100.,100.])
        kwargs=dict(variant=P.Variant('test','test',(P.Sleeve('MN',1),)),
                    spy=spy,calendar=days,cands={'MN':{days[0]:[(1.,'X')]}},cost=0)
        a=P.simulate(secs={'X':short},**kwargs)
        b=P.simulate(secs={'X':extended},**kwargs)
        self.assertEqual(a['equity'],b['equity'],
                         'Identical observations through Jan12; future Jan16 bar changes $70k into $100k')

    def test_F02_journals_must_isolate_changed_data_modes(self):
        data,sectors=momentum_world()
        now=[datetime(2026,10,30,22,tzinfo=UTC)]
        with tempfile.TemporaryDirectory() as td:
            first=MomentumBook(loader(data),lambda:now[0],Path(td),lambda:sectors)
            first.book()  # timely signal from first source, before intended entry
            live={t:([Bar(x.day,100.,100.,100.,100.,1e6) for x in bs]
                     if t.startswith('NEW') else bs) for t,bs in data.items()}
            now[0]=datetime(2026,11,4,12,tzinfo=UTC)
            second=MomentumBook(loader(live),lambda:now[0],Path(td),lambda:sectors)
            result=second.book()
            current={r['ticker'] for r in result['holdings']}
            forward=set(result['forward']['open'])
            self.assertTrue(all(t.startswith('OLD') for t in current))
            self.assertFalse(any(t.startswith('NEW') for t in forward),
                             'Service shares data_dir across MOCK/LIVE; original-source NEW journal survives')

    def test_F03_same_month_end_must_preserve_holdings(self):
        data,sectors=momentum_world()
        now=[datetime(2026,10,6,12,tzinfo=UTC)]
        with tempfile.TemporaryDirectory() as td:
            book=MomentumBook(loader(data),lambda:now[0],Path(td),lambda:sectors)
            early=book.book()
            now[0]=datetime(2026,10,27,12,tzinfo=UTC)
            late=book.book()
            self.assertEqual(early['rebalance_day'],late['rebalance_day'])
            self.assertEqual({r['ticker'] for r in early['holdings']},
                             {r['ticker'] for r in late['holdings']},
                             'Same Sep30 rebalance becomes entirely different holdings as the 420-day window moves')

    def test_F04a_momentum_must_not_backfill_forward_trades(self):
        data,sectors=momentum_world()
        now=datetime(2026,11,7,12,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            book=MomentumBook(loader(data),lambda:now,Path(td),lambda:sectors)
            result=book.book()
            self.assertFalse(result['forward'].get('open'),
                             'First Nov7 observation must not fill the already-past Nov2 open')

    def test_F04b_daily_signal_must_not_enter_before_observation(self):
        data,p=pullback_world()
        now=[datetime(2026,9,25,15,tzinfo=UTC)]
        with tempfile.TemporaryDirectory() as td:
            signals=StrategySignals(loader(data),lambda:now[0],Path(td))
            scan=signals.scan()
            self.assertTrue(any(s['ticker']=='PULL' and s['strategy']=='A' for s in scan['confirmed']))
            data['PULL'].append(Bar(date(2026,9,25),p,p*1.002,p*.998,p,2e6))
            now[0]=datetime(2026,9,26,12,tzinfo=UTC)
            trade=signals.forward()['trades'][0]
            self.assertEqual(trade['state'],'MISSED',
                             'Observed 15:00Z, but implied next-open fill is 13:30Z on the same day')

    def test_F05_ex_date_sale_keeps_dividend_entitlement(self):
        days=[date(2024,1,d) for d in (2,3,4)]
        stock=sec('X',days,[100.,100.,99.],dividends=[(days[2],1.)])
        spy=sec('SPY',days,[100.]*3)
        variant=P.Variant('test','test',(P.Sleeve('MN',1),))
        result=P.simulate(variant,{'X':stock},spy,days,{'MN':{days[0]:[(1.,'X')]}},cost=0,
                          xexit=lambda strategy,key,d:'rule' if d==days[1] else None)
        self.assertAlmostEqual(result['equity'][-1][1],100000.,
                               msg='1000 shares x $1 ordinary dividend omitted from ex-date sale')

    def test_F06_allocation_continues_past_sector_rejections(self):
        days=[date(2024,1,2),date(2024,1,3)]
        secs={f'X{j:02d}':sec(f'X{j:02d}',days,[100.,100.],
                              'Tech' if j<15 else f'Sector{j}') for j in range(22)}
        ranked=[(float(100-j),k) for j,k in enumerate(secs)]
        result=P.simulate(P.VARIANTS[0],secs,sec('SPY',days,[100.,100.]),days,
                          {'A':{days[0]:ranked}},cost=0)
        self.assertEqual(len(result['open']),10,
                         '3 Tech plus 7 eligible other sectors fit; queue truncation buys only 3')

    def test_F07_old_premarket_quote_must_not_create_preliminary_signal(self):
        days=last_sessions(280,date(2026,9,24))
        closes=[40+.15*i for i in range(len(days))]
        closes[-1]=closes[-2]*.97
        data={'PULL':[Bar(d,p,p*1.002,p*.998,p,2e6) for d,p in zip(days,closes)]}
        now=datetime(2026,9,25,18,tzinfo=UTC)
        quote_time=datetime(2026,9,25,10,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            signals=StrategySignals(loader(data),lambda:now,Path(td))
            result=signals.scan(lambda ticker:(closes[-1]*.9,quote_time))
            self.assertEqual(result['preliminary'],[],
                             '8-hour-old premarket quote should be rejected')

    def test_F08_open_return_includes_entry_fee(self):
        data,p=pullback_world()
        now=[datetime(2026,9,24,22,tzinfo=UTC)]
        with tempfile.TemporaryDirectory() as td:
            signals=StrategySignals(loader(data),lambda:now[0],Path(td))
            signals.scan()  # timely, before next open; isolates fee bug
            data['PULL'].append(Bar(date(2026,9,25),p,p*1.002,p*.998,p,2e6))
            now[0]=datetime(2026,9,26,12,tzinfo=UTC)
            trade=signals.forward()['trades'][0]
            self.assertEqual(trade['state'],'OPEN')
            self.assertAlmostEqual(trade['ret_open'],1/(1+COST)-1,
                                   msg='Unchanged price has an entry cost, not 0% net return')

if __name__=='__main__':
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
