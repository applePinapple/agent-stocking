"""Synthetic selector histories; no historical market return is represented."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
import unittest

from agent_stocking.models import Bar
from agent_stocking.point_in_time import HistoricalCoverage, HistoricalFact, PointInTimeTable
from agent_stocking.portfolio_strategies import (
    CumulativeRsiPortfolio, PortfolioCloseContext, PortfolioRules, PublishedDailyBar,
    UnconfirmedPortfolioRules, WeeklyRsiRotation, subtract_calendar_months,
)

TZ=timezone(timedelta(hours=8))
SESSIONS=tuple(date(2020,1,1)+timedelta(days=i) for i in range(500) if (date(2020,1,1)+timedelta(days=i)).weekday()<5)[:230]
def stamp(day):return datetime.combine(day,time(15),TZ)

def history(symbol, values=None):
    prices=values or ([D(10)]*220+[D(100)]*8+[D(70),D(60)])
    return tuple(PublishedDailyBar(symbol,Bar(day,p,p,p,p,10000,D(1000000),True,True),stamp(day),stamp(day),p,"synthetic bar fixture") for day,p in zip(SESSIONS,prices))

def table(symbols="ABCDEFG",coverage=True):
    amounts={s:D(100+i) for i,s in enumerate(symbols)}
    amounts.update(A=D(500),B=D(100),C=D(100),D=D(300))
    facts=[HistoricalFact(s,"total_shares",amounts[s],stamp(SESSIONS[0]),stamp(SESSIONS[0]),"synthetic published fact","shares") for s in symbols]
    cover=[HistoricalCoverage(s,"total_shares",stamp(SESSIONS[0]),stamp(date(2021,12,31)),"synthetic event coverage") for s in symbols] if coverage else []
    return PointInTimeTable(facts,cover)

def rules(symbols="ABCDEFG",status="synthetic_test"):
    return PortfolioRules(tuple(symbols),status,"synthetic test approval","zero_first_bar")

def context(symbols="ABCDEFG",held=(),nav="900"):
    histories={s:history(s) for s in symbols}
    histories["INDEX"]=history("INDEX",[D(10)]*229+[D(11)])
    return PortfolioCloseContext(SESSIONS[-1],stamp(SESSIONS[-1]),histories,SESSIONS,SESSIONS[0],stamp(date(2019,1,1)),"synthetic preannounced calendar",True,D(nav),tuple(held),table(symbols),"synthetic adjusted signal prices","CNY")

class PortfolioSelectorTests(unittest.TestCase):
    def test_S14_confirmed_profile_requires_explicit_share_model(self):
        confirmed=replace(rules("A"),approval_status="confirmed")
        with self.assertRaises(ValueError):
            CumulativeRsiPortfolio(confirmed,minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive")
        selector=CumulativeRsiPortfolio(confirmed,minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive",share_fact_semantics="latest_published_known")
        c=context("A")
        self.assertEqual(selector.decide(c).decision.buys,())
        pub=HistoricalCoverage("A","total_shares_publications",stamp(SESSIONS[0]),stamp(date(2021,12,31)),"synthetic public-notice completeness")
        table_public=PointInTimeTable(c.facts._facts,[pub])
        self.assertEqual(len(selector.decide(replace(c,facts=table_public)).decision.buys),1)

    def test_S14_marketcap_order_code_tie_and_empty_slot_only(self):
        selector=CumulativeRsiPortfolio(rules("ABCD"),minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive")
        result=selector.decide(context("ABCD",held=("A",)))
        self.assertEqual(result.ranked_candidates,("B","C","D"))
        self.assertEqual([b.symbol for b in result.decision.buys],["B","C"])
        self.assertEqual([b.cash_budget for b in result.decision.buys],[D("300.00")]*2)
        self.assertEqual(result.decision.sell_symbols,())

    def test_S14_missing_shares_or_coverage_excludes_not_current_backfill(self):
        selector=CumulativeRsiPortfolio(rules("AB"),minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive")
        c=replace(context("AB"),facts=table("AB",coverage=False))
        result=selector.decide(c)
        self.assertEqual(result.decision.buys,())
        self.assertTrue(all("point_in_time" in v for v in result.exclusions.values()))

    def test_S14_exact_all_session_turnover_and_five_percent_budget_gate(self):
        selector=CumulativeRsiPortfolio(rules("AB"),minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive")
        c=context("AB")
        broken=dict(c.histories);broken["A"]=tuple(r for i,r in enumerate(broken["A"]) if i!=200)
        result=selector.decide(replace(c,histories=broken))
        self.assertIn("not_traded",result.exclusions["A"])
        rich=selector.decide(replace(c,nav=D("100000000")))
        self.assertEqual(rich.decision.buys,())
        self.assertTrue(all("five_percent" in v for v in rich.exclusions.values()))

    def test_S20_five_names_shared_five_equal_slots_and_full_turnover(self):
        selector=WeeklyRsiRotation(rules(),benchmark_symbol="INDEX")
        result=selector.decide(context(held=("A","G"),nav="1000"))
        self.assertEqual(result.ranked_candidates,tuple("ABCDEFG"))
        self.assertEqual(result.decision.sell_symbols,("A","G"))
        self.assertEqual([b.symbol for b in result.decision.buys],list("ABCDE"))
        self.assertEqual([b.cash_budget for b in result.decision.buys],[D("200.00")]*5)

    def test_S20_short_candidate_list_retains_unfilled_slots_as_cash(self):
        result=WeeklyRsiRotation(rules("ABC"),benchmark_symbol="INDEX").decide(context("ABC",nav="1000"))
        self.assertEqual(sum(b.cash_budget for b in result.decision.buys),D("600.00"))

    def test_S20_benchmark_missing_does_not_use_previous_or_future_date(self):
        c=context(); h=dict(c.histories);h["INDEX"]=h["INDEX"][:-1]
        with self.assertRaises(ValueError):WeeklyRsiRotation(rules(),benchmark_symbol="INDEX").decide(replace(c,histories=h))
        result=WeeklyRsiRotation(rules(),benchmark_symbol="INDEX").decide(replace(c,is_week_end=False,histories={}))
        self.assertEqual(result.decision.buys,())

    def test_future_price_perturbation_and_truncation_leave_past_selection_unchanged(self):
        selector=WeeklyRsiRotation(rules(),benchmark_symbol="INDEX")
        c=context()
        baseline=selector.decide(c)
        future_day=SESSIONS[-1]+timedelta(days=10)
        h={}
        for s,rows in c.histories.items():
            p=D("9999999")
            future=PublishedDailyBar(s,Bar(future_day,p,p,p,p,1,D(1),True,True),stamp(future_day),stamp(future_day),p,"synthetic future mutation")
            h[s]=rows+(future,)
        altered=selector.decide(replace(c,histories=h))
        self.assertEqual(altered,baseline)

    def test_future_fact_revision_does_not_change_marketcap_rank(self):
        selector=CumulativeRsiPortfolio(rules("ABCD"),minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive")
        c=context("ABCD")
        known=table("ABCD")
        future=HistoricalFact("D","total_shares",D(1),stamp(SESSIONS[0]),stamp(date(2021,1,1)),"synthetic later correction","shares")
        future_table=PointInTimeTable(list(known._facts)+[future],known._coverage)
        self.assertEqual(selector.decide(c),selector.decide(replace(c,facts=future_table)))

    def test_same_date_bar_published_after_decision_not_visible(self):
        c=context("A"); h=dict(c.histories)
        h["A"]=h["A"][:-1]+(replace(h["A"][-1],available_at=stamp(SESSIONS[-1])+timedelta(hours=1)),)
        result=WeeklyRsiRotation(rules("A"),benchmark_symbol="INDEX").decide(replace(c,histories=h))
        self.assertEqual(result.decision.buys,())

    def test_public_context_is_immutable_known_prefix_and_fact_snapshot(self):
        c=context("A")
        future_day=SESSIONS[-1]+timedelta(days=10)
        future=replace(history("A")[-1],bar=replace(history("A")[-1].bar,date=future_day),completed_at=stamp(future_day),available_at=stamp(future_day))
        external={"A":list(history("A"))+[future]}
        original=table("A")
        future_fact=HistoricalFact("A","total_shares",D(1),stamp(SESSIONS[0]),stamp(future_day),"synthetic future source","shares")
        snap=replace(c,histories=external,market_sessions=SESSIONS+(future_day,),facts=PointInTimeTable(original._facts+(future_fact,),original._coverage))
        self.assertEqual(snap.histories["A"],history("A"))
        self.assertNotIn(future_day,snap.market_sessions)
        self.assertEqual(snap.facts.as_of("A","total_shares",stamp(future_day),unit="shares").value,D(500))
        external["A"].clear()
        self.assertEqual(len(snap.histories["A"]),230)
        with self.assertRaises(TypeError):snap.histories["A"]=()

    def test_native_export_mode_uses_native_values_for_ranking_and_threshold(self):
        c=context("ABC"); h={}
        for symbol,rows in c.histories.items():
            native_rsi={"A":D(30),"B":D(20),"C":D(1),"INDEX":D(50)}[symbol]
            native_ma=D(10) if symbol=="INDEX" else D(50)
            h[symbol]=tuple(replace(row,native_features={"rsi2":native_rsi,"ma200":native_ma}) for row in rows)
        native_rules=replace(rules("ABC"),rsi_seed="native_export")
        result=WeeklyRsiRotation(native_rules,benchmark_symbol="INDEX").decide(replace(c,histories=h))
        self.assertEqual(result.ranked_candidates,("C","B","A"))
        s14=CumulativeRsiPortfolio(native_rules,minimum_market_cap_cny=D(1),lookback_boundary="start_exclusive").decide(replace(c,histories=h))
        self.assertEqual([buy.symbol for buy in s14.decision.buys],["C"])

    def test_native_mode_has_no_local_fallback_and_freezes_features(self):
        with self.assertRaises(ValueError):
            WeeklyRsiRotation(replace(rules("A"),rsi_seed="native_export"),benchmark_symbol="INDEX").decide(context("A"))
        supplied={"rsi2":D(12),"ma200":D(10)}
        row=replace(history("A")[-1],native_features=supplied)
        supplied["rsi2"]=D(99)
        self.assertEqual(row.native_features["rsi2"],D(12))
        with self.assertRaises(TypeError):row.native_features["rsi2"]=D(99)

    def test_benchmark_indicator_mode_is_explicit_and_native_has_no_fallback(self):
        confirmed=replace(rules("A"),approval_status="confirmed")
        with self.assertRaises(ValueError):WeeklyRsiRotation(confirmed,benchmark_symbol="INDEX")
        c=context("A");hist={}
        for symbol,rows in c.histories.items():
            ma=D(12) if symbol=="INDEX" else D(50)
            hist[symbol]=tuple(replace(row,native_features={"rsi2":D(1),"ma200":ma}) for row in rows)
        native_rules=replace(rules("A"),rsi_seed="native_export")
        native=WeeklyRsiRotation(native_rules,benchmark_symbol="INDEX",benchmark_indicator_mode="native_export")
        mathematical=WeeklyRsiRotation(native_rules,benchmark_symbol="INDEX",benchmark_indicator_mode="mathematical_mean")
        self.assertEqual(native.decide(replace(c,histories=hist)).decision.buys,())
        self.assertEqual([buy.symbol for buy in mathematical.decide(replace(c,histories=hist)).decision.buys],["A"])
        hist["INDEX"]=c.histories["INDEX"]
        with self.assertRaises(ValueError):native.decide(replace(c,histories=hist))

    def test_pending_rules_never_create_orders(self):
        with self.assertRaises(UnconfirmedPortfolioRules):
            WeeklyRsiRotation(rules(status="pending"),benchmark_symbol="INDEX").decide(context())

    def test_calendar_month_subtraction_handles_leap_day(self):
        self.assertEqual(subtract_calendar_months(date(2020,5,31),3),date(2020,2,29))

if __name__=="__main__":unittest.main()
