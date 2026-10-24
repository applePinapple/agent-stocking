"""Causal S14/S20 selectors for one shared account, never seven cash accounts.

Published rules and user-proposed local adaptations are separate explicit
parameters. Pending profiles cannot generate orders. RSI seed is explicit and
is not a claim of verified native Tongdaxin numerical identity.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN
from statistics import median
from types import MappingProxyType
from typing import Mapping, Sequence

from .models import Bar
from .point_in_time import PointInTimeTable, aware
from .portfolio import BuyIntent, PortfolioDecision


class UnconfirmedPortfolioRules(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedDailyBar:
    symbol: str
    bar: Bar
    completed_at: datetime
    available_at: datetime
    unadjusted_close: Decimal
    source: str
    native_features: Mapping[str, Decimal | None] | None = None

    def __post_init__(self):
        aware(self.completed_at, "completed_at")
        aware(self.available_at, "available_at")
        if self.available_at < self.completed_at or self.completed_at.date() != self.bar.date:
            raise ValueError("A daily bar cannot be available before completion")
        if not isinstance(self.unadjusted_close, Decimal) or not self.unadjusted_close.is_finite() or self.unadjusted_close <= 0:
            raise ValueError("Explicit unadjusted close required for market-cap calculation")
        if not self.symbol or not self.source:
            raise ValueError("Symbol and bar evidence source required")
        if self.native_features is not None:
            if set(self.native_features)!={"rsi2","ma200"}:
                raise ValueError("Native feature snapshot requires explicit rsi2 and ma200")
            for name,value in self.native_features.items():
                if value is not None and (not isinstance(value,Decimal) or not value.is_finite()
                                           or value<0 or (name=="ma200" and value==0)
                                           or (name=="rsi2" and value>100)):
                    raise ValueError("Invalid native indicator value; convert invalid sentinel to None")
            object.__setattr__(self,"native_features",MappingProxyType(dict(self.native_features)))


@dataclass(frozen=True, slots=True)
class PortfolioCloseContext:
    session: date
    decision_at: datetime
    histories: Mapping[str, tuple[PublishedDailyBar, ...]]
    market_sessions: tuple[date, ...]
    calendar_coverage_start: date
    calendar_published_at: datetime
    calendar_source: str
    is_week_end: bool
    nav: Decimal
    held_symbols: tuple[str, ...]
    facts: PointInTimeTable
    price_basis: str
    amount_unit: str

    def __post_init__(self):
        aware(self.decision_at, "decision_at")
        aware(self.calendar_published_at, "calendar_published_at")
        if self.calendar_published_at > self.decision_at or not self.calendar_source:
            raise ValueError("Market calendar must be publicly known before the decision")
        if self.market_sessions != tuple(sorted(set(self.market_sessions))) or self.session not in self.market_sessions:
            raise ValueError("Explicit ascending market sessions including decision date required")
        if self.session != self.decision_at.date() or type(self.is_week_end) is not bool:
            raise ValueError("Decision timestamp/date and explicit week-end marker required")
        if not isinstance(self.nav, Decimal) or not self.nav.is_finite() or self.nav < 0:
            raise ValueError("Shared account NAV must be a nonnegative Decimal")
        if len(set(self.held_symbols)) != len(self.held_symbols) or not self.price_basis or self.amount_unit != "CNY":
            raise ValueError("Unique holdings, explicit price basis and CNY turnover required")
        for symbol, rows in self.histories.items():
            dates = tuple(row.bar.date for row in rows)
            if dates != tuple(sorted(set(dates))) or any(row.symbol != symbol for row in rows):
                raise ValueError("Price histories must be uniquely ordered and symbol-consistent")
        known={symbol:tuple(row for row in rows if row.bar.date<=self.session
                           and row.completed_at<=self.decision_at and row.available_at<=self.decision_at)
               for symbol,rows in self.histories.items()}
        object.__setattr__(self,"histories",MappingProxyType(known))
        object.__setattr__(self,"market_sessions",tuple(d for d in self.market_sessions if d<=self.session))
        object.__setattr__(self,"held_symbols",tuple(self.held_symbols))
        if not isinstance(self.facts,PointInTimeTable):
            raise ValueError("Publication-aware historical fact table required")
        object.__setattr__(self,"facts",self.facts.at_decision(self.decision_at))

    def known_history(self, symbol: str) -> tuple[PublishedDailyBar, ...]:
        return tuple(row for row in self.histories.get(symbol, ()) if row.bar.date <= self.session
                     and row.completed_at <= self.decision_at and row.available_at <= self.decision_at)


@dataclass(frozen=True, slots=True)
class PortfolioRules:
    universe: tuple[str, ...]
    approval_status: str
    approval_source: str
    rsi_seed: str

    def __post_init__(self):
        if not self.universe or len(set(self.universe)) != len(self.universe):
            raise ValueError("Explicit unique universe required")
        object.__setattr__(self,"universe",tuple(self.universe))
        if self.approval_status not in ("pending", "confirmed", "synthetic_test"):
            raise ValueError("Explicit approval state required")
        if self.approval_status != "pending" and not self.approval_source:
            raise ValueError("Confirmed/test rules require their evidence source")
        if self.rsi_seed not in ("zero_first_bar", "first_delta", "native_export"):
            raise ValueError("Explicit native-export or local RSI initialization model required")

    def require_confirmed(self):
        if self.approval_status == "pending":
            raise UnconfirmedPortfolioRules("Portfolio allocation/data adaptations remain pending; no orders or returns")


@dataclass(frozen=True, slots=True)
class PortfolioSelection:
    decision: PortfolioDecision
    ranked_candidates: tuple[str, ...]
    exclusions: Mapping[str, str]
    features: Mapping[str, Mapping[str, Decimal]]


def rsi2_values(closes: Sequence[Decimal], seed: str) -> list[Decimal | None]:
    if seed not in ("zero_first_bar", "first_delta"):
        raise ValueError("Unknown RSI seed")
    result: list[Decimal | None] = [None] * len(closes)
    gain, loss = Decimal(0), Decimal(0)
    for i, value in enumerate(closes):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ValueError("RSI closes must be positive finite Decimals")
        if i == 0:
            continue
        change = value - closes[i-1]
        up, down = max(change, Decimal(0)), max(-change, Decimal(0))
        if i == 1 and seed == "first_delta":
            gain, loss = up, down
        else:
            gain, loss = (gain+up)/2, (loss+down)/2
        denominator = gain+loss
        result[i] = None if denominator == 0 else gain/denominator*100
    return result


def features(rows: Sequence[PublishedDailyBar], seed: str) -> tuple[Decimal, Decimal, Decimal | None] | None:
    if len(rows) < 200:
        return None
    if seed=="native_export":
        if rows[-1].native_features is None or rows[-2].native_features is None:
            raise ValueError("Native-export mode requires bound native indicator values; no recomputation fallback")
        current=rows[-1].native_features
        previous=rows[-2].native_features
        if current["ma200"] is None or current["rsi2"] is None:
            return None
        return current["ma200"],current["rsi2"],previous["rsi2"]
    closes = [row.bar.close for row in rows]
    rsi = rsi2_values(closes, seed)
    if rsi[-1] is None:
        return None
    return sum(closes[-200:])/200, rsi[-1], rsi[-2]


def subtract_calendar_months(day: date, months: int) -> date:
    serial = day.year*12 + day.month - 1 - months
    year, month0 = divmod(serial, 12)
    month = month0+1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def slot_budget(nav: Decimal, slots: int) -> Decimal:
    return (nav/slots).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


class CumulativeRsiPortfolio:
    max_positions = 3

    def __init__(self, rules: PortfolioRules, *, minimum_market_cap_cny: Decimal, lookback_boundary: str,
                 share_fact_semantics: str | None = None):
        self.rules = rules
        if not isinstance(minimum_market_cap_cny, Decimal) or not minimum_market_cap_cny.is_finite() or minimum_market_cap_cny <= 0:
            raise ValueError("Explicit large-cap threshold in CNY required")
        if lookback_boundary not in ("start_exclusive", "start_inclusive"):
            raise ValueError("Explicit three-calendar-month lower boundary required")
        self.minimum_market_cap_cny = minimum_market_cap_cny
        self.lookback_boundary = lookback_boundary
        if share_fact_semantics is None and rules.approval_status in ("synthetic_test","pending"):
            share_fact_semantics="actual_effective_with_audited_coverage"
        if share_fact_semantics not in ("actual_effective_with_audited_coverage","latest_published_known"):
            raise ValueError("Formal S14 requires explicit historical share fact semantics")
        self.share_fact_semantics=share_fact_semantics

    def decide(self, context: PortfolioCloseContext) -> PortfolioSelection:
        self.rules.require_confirmed()
        exits, candidates, excluded, values = [], [], {}, {}
        budget = slot_budget(context.nav, 3)
        beginning = subtract_calendar_months(context.session, 3)
        if context.calendar_coverage_start > beginning:
            raise ValueError("Market calendar does not cover the full three-month liquidity window")
        sessions = [d for d in context.market_sessions if d <= context.session and
                    (d > beginning if self.lookback_boundary == "start_exclusive" else d >= beginning)]
        for symbol in self.rules.universe:
            rows = context.known_history(symbol)
            feat = features(rows, self.rules.rsi_seed)
            if not rows or rows[-1].bar.date != context.session or feat is None or feat[2] is None:
                excluded[symbol] = "current_completed_indicator_history_unavailable"
                continue
            ma200, current_rsi, prior_rsi = feat
            cumulative = current_rsi+prior_rsi
            close = rows[-1].bar.close
            values[symbol] = {"ma200":ma200, "rsi2":current_rsi, "cumulative_rsi2":cumulative}
            if symbol in context.held_symbols:
                if cumulative > 65 or close < ma200:
                    exits.append(symbol)
                continue
            if not (close > ma200 and cumulative < 10):
                excluded[symbol] = "entry_signal_false"
                continue
            cap = context.facts.market_cap(symbol, context.decision_at, rows[-1].unadjusted_close,
                                          share_fact_semantics=self.share_fact_semantics)
            if cap is None:
                excluded[symbol] = "no_verified_point_in_time_total_shares"
                continue
            market_cap, _fact = cap
            if market_cap < self.minimum_market_cap_cny:
                excluded[symbol] = "below_confirmed_market_cap_threshold"
                continue
            by_date = {r.bar.date:r.bar for r in rows}
            if not sessions or any(d not in by_date or by_date[d].volume <= 0 or by_date[d].amount is None or by_date[d].amount <= 0 for d in sessions):
                excluded[symbol] = "not_traded_every_market_session_in_three_months"
                continue
            median_turnover = median([by_date[d].amount for d in sessions])
            if budget > median_turnover*Decimal("0.05"):
                excluded[symbol] = "planned_slot_budget_exceeds_five_percent_median_turnover"
                continue
            values[symbol].update(market_cap_cny=market_cap, median_turnover_cny=median_turnover)
            candidates.append((market_cap, symbol))
        ranked = tuple(s for _,s in sorted(candidates))
        slots = max(0, 3-(len(context.held_symbols)-len(exits)))
        buys = tuple(BuyIntent(s, budget, "S14 cumulative_RSI2 market_cap_rank") for s in ranked[:slots] if budget > 0)
        return PortfolioSelection(PortfolioDecision(context.session,tuple(sorted(exits)),buys,"S14 exit_before_ranked_entries"),ranked,excluded,values)


class WeeklyRsiRotation:
    max_positions = 5

    def __init__(self, rules: PortfolioRules, *, benchmark_symbol: str, benchmark_indicator_mode: str | None = None):
        self.rules = rules
        if not benchmark_symbol:
            raise ValueError("Explicit approved market-regime benchmark required")
        self.benchmark_symbol = benchmark_symbol
        if benchmark_indicator_mode is None and rules.approval_status in ("synthetic_test","pending"):
            benchmark_indicator_mode="mathematical_mean"
        if benchmark_indicator_mode not in ("mathematical_mean","native_export"):
            raise ValueError("Formal S20 requires explicit mathematical_mean/native_export benchmark mode")
        self.benchmark_indicator_mode=benchmark_indicator_mode

    def decide(self, context: PortfolioCloseContext) -> PortfolioSelection:
        self.rules.require_confirmed()
        if not context.is_week_end:
            return PortfolioSelection(PortfolioDecision(context.session,(),(),"not_preannounced_week_end"),(),{}, {})
        benchmark = context.known_history(self.benchmark_symbol)
        if not benchmark or benchmark[-1].bar.date != context.session:
            raise ValueError("Missing same-date completed benchmark history")
        exits = tuple(sorted(context.held_symbols))
        benchmark_ma=None
        if len(benchmark)>=200:
            if self.benchmark_indicator_mode=="native_export":
                if benchmark[-1].native_features is None:
                    raise ValueError("Native benchmark mode requires bound native MA200; no mathematical fallback")
                benchmark_ma=benchmark[-1].native_features["ma200"]
            else:
                benchmark_ma=sum(r.bar.close for r in benchmark[-200:])/200
        if benchmark_ma is None or benchmark[-1].bar.close <= benchmark_ma:
            return PortfolioSelection(PortfolioDecision(context.session,exits,(),"S20 benchmark_filter_false_or_warmup"),(),{}, {})
        candidates, excluded, values = [], {}, {}
        for symbol in self.rules.universe:
            rows = context.known_history(symbol)
            feat = features(rows,self.rules.rsi_seed)
            if not rows or rows[-1].bar.date != context.session or feat is None:
                excluded[symbol] = "current_completed_indicator_history_unavailable"
                continue
            ma200, rsi, _prior = feat
            if rows[-1].bar.close <= ma200:
                excluded[symbol] = "stock_above_MA200_false"
                continue
            values[symbol] = {"ma200":ma200,"rsi2":rsi}
            candidates.append((rsi,symbol))
        ranked = tuple(s for _,s in sorted(candidates))
        budget = slot_budget(context.nav,5)
        buys = tuple(BuyIntent(s,budget,"S20 weekly_RSI2_ascending_code_tie") for s in ranked[:5] if budget > 0)
        # Even a retained top-five security exits and re-enters: no free turnover.
        return PortfolioSelection(PortfolioDecision(context.session,exits,buys,"S20 sell_old_before_buy_new"),ranked,excluded,values)
