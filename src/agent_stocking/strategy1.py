"""Strategy 1 replay of explicitly supplied, currently visible TDX signals.

This does not reconstruct erased historical rockets and is not a claim that the
supplied signals were the complete set available at the historical time. Normal
fills intentionally use the user's ideal same-close execution assumption.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

_ZERO = Decimal("0.00")
_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class Strategy1RuleError(ValueError):
    """The observations encounter a branch the user has not specified."""


class MissingProfitObservationError(Strategy1RuleError):
    """A close-time decision requires an observation that has not been captured."""

    def __init__(
        self, observation_date: date, held_quantity: int,
        observing_opportunity_ids: Sequence[int], pending_opportunity_ids: Sequence[int],
    ) -> None:
        self.date = observation_date
        self.phase = "close"
        self.required_fields = ("profit_pct",)
        self.held_quantity = held_quantity
        self.observing_opportunity_ids = tuple(observing_opportunity_ids)
        self.pending_opportunity_ids = tuple(pending_opportunity_ids)
        super().__init__(
            f"{observation_date}: missing required profit_pct at close; "
            f"held_quantity={held_quantity}; observing_opportunity_ids={self.observing_opportunity_ids}; "
            f"pending_opportunity_ids={self.pending_opportunity_ids}; capture this date before replaying"
        )


@dataclass(frozen=True, slots=True)
class DailyObservation:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    profit_pct: Decimal | None
    rocket: bool
    close_limit_up: bool = False
    open_buy_allowed: bool = True
    sell_allowed: bool = True
    source: str = ""


@dataclass(frozen=True, slots=True)
class CashDividend:
    event_id: str
    record_date: date
    ex_date: date
    payment_date: date
    cash_per_share: Decimal
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("CashDividend event_id must be a nonempty string")
        if any(type(getattr(self, name)) is not date for name in ("record_date", "ex_date", "payment_date")):
            raise ValueError("CashDividend dates must be calendar dates")
        if not self.record_date < self.ex_date <= self.payment_date:
            raise ValueError("CashDividend requires record_date < ex_date <= payment_date")
        if (not isinstance(self.cash_per_share, Decimal) or not self.cash_per_share.is_finite()
                or self.cash_per_share <= 0):
            raise ValueError("cash_per_share must be a positive finite Decimal")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("CashDividend source must identify the original evidence")


@dataclass(frozen=True, slots=True)
class Strategy1DividendEntitlement:
    event_id: str
    buy_sequence: int
    record_date: date
    ex_date: date
    payment_date: date
    quantity: int
    cash_per_share: Decimal
    amount: Decimal
    source: str
    recognized: bool = False
    paid: bool = False


@dataclass(frozen=True, slots=True)
class Strategy1DividendLedger:
    event_id: str
    date: date
    processed_on: date
    phase: str
    buy_sequence: int | None
    quantity: int
    cash_per_share: Decimal
    amount: Decimal
    cash_after: Decimal
    receivable_after: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class Strategy1Config:
    initial_cash: Decimal = Decimal("1000000.00")
    buy_budget: Decimal = Decimal("100000.00")
    commission_rate: Decimal = Decimal("0.0001")
    min_commission: Decimal = Decimal("5.00")
    minimum_buy_quantity: int = 200
    quantity_step: int = 1

    def __post_init__(self) -> None:
        for name in ("initial_cash", "buy_budget", "commission_rate", "min_commission"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative Decimal")
        if self.initial_cash <= 0 or self.buy_budget <= 0:
            raise ValueError("initial_cash and buy_budget must be positive")
        if self.initial_cash != _money(self.initial_cash) or self.buy_budget != _money(self.buy_budget):
            raise ValueError("initial_cash and buy_budget must be in whole cents")
        if self.commission_rate >= 1:
            raise ValueError("commission_rate must be below 1")
        for name in ("minimum_buy_quantity", "quantity_step"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class Strategy1Fill:
    sequence: int
    date: date
    session: str
    side: str
    signal_date: date
    rocket_date: date | None
    opportunity_id: int | None
    quantity: int
    price: Decimal
    notional: Decimal
    commission: Decimal
    cash_after: Decimal
    holdings_after: int
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class Strategy1Order:
    date: date
    event: str
    opportunity_id: int | None
    rocket_date: date | None
    trigger_date: date | None
    reason: str


@dataclass(frozen=True, slots=True)
class Strategy1Equity:
    date: date
    cash: Decimal
    quantity: int
    close: Decimal
    equity: Decimal
    profit_pct: Decimal | None
    dividend_receivable: Decimal = _ZERO
    market_value: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class Strategy1Lot:
    buy_sequence: int
    opportunity_id: int
    rocket_date: date
    entry_date: date
    quantity: int
    entry_price: Decimal
    entry_notional: Decimal
    entry_commission: Decimal
    dividend_income: Decimal = _ZERO
    dividend_cash_received: Decimal = _ZERO

    @property
    def entry_cost(self) -> Decimal:
        return self.entry_notional + self.entry_commission


@dataclass(frozen=True, slots=True)
class Strategy1ClosedBatch:
    buy_sequence: int
    sell_sequence: int
    opportunity_id: int
    rocket_date: date
    entry_date: date
    exit_date: date
    quantity: int
    entry_cost: Decimal
    exit_notional: Decimal
    exit_commission: Decimal
    pnl: Decimal
    dividend_income: Decimal = _ZERO
    dividend_cash_received: Decimal = _ZERO


@dataclass(slots=True)
class Strategy1Opportunity:
    opportunity_id: int
    rocket_date: date
    rocket_index: int
    status: str = "observing"
    trigger_date: date | None = None
    consecutive_one_price_up_days: int = 0


@dataclass(frozen=True, slots=True)
class Strategy1Result:
    config: Strategy1Config
    fills: list[Strategy1Fill]
    orders: list[Strategy1Order]
    equity: list[Strategy1Equity]
    closed_batches: list[Strategy1ClosedBatch]
    ending_lots: list[Strategy1Lot]
    opportunities: list[Strategy1Opportunity]
    signal_basis: str = "current_visible_rockets_with_erased_signal_survivorship_bias"
    cash_dividend_events: list[CashDividend] = field(default_factory=list)
    dividend_entitlements: list[Strategy1DividendEntitlement] = field(default_factory=list)
    dividend_ledger: list[Strategy1DividendLedger] = field(default_factory=list)


def _validate_observations(
    observations: Sequence[DailyObservation], *, allow_sparse_profit: bool = False,
) -> None:
    if not observations:
        raise ValueError("At least one daily observation is required")
    previous: date | None = None
    for item in observations:
        if not isinstance(item, DailyObservation) or type(item.date) is not date:
            raise ValueError("DailyObservation with a calendar date is required")
        if previous is not None and item.date <= previous:
            raise ValueError("Observation dates must be strictly increasing and unique")
        for name in ("open", "high", "low", "close"):
            value = getattr(item, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{item.date}: {name} must be a finite Decimal")
        if min(item.open, item.high, item.low, item.close) <= 0:
            raise ValueError(f"{item.date}: prices must be positive")
        if not item.low <= min(item.open, item.close) <= max(item.open, item.close) <= item.high:
            raise ValueError(f"{item.date}: inconsistent OHLC prices")
        if item.profit_pct is None:
            if not allow_sparse_profit:
                raise ValueError(f"{item.date}: profit_pct is required; sparse-profit mode is not enabled")
        elif (
            not isinstance(item.profit_pct, Decimal) or not item.profit_pct.is_finite()
            or not 0 <= item.profit_pct <= 100
        ):
            raise ValueError(f"{item.date}: profit_pct must be a finite Decimal in percentage points [0, 100]")
        for name in ("rocket", "close_limit_up", "open_buy_allowed", "sell_allowed"):
            if type(getattr(item, name)) is not bool:
                raise ValueError(f"{item.date}: {name} must be an explicit bool")
        if not isinstance(item.source, str):
            raise ValueError(f"{item.date}: source must be a string")
        previous = item.date


def _commission(notional: Decimal, config: Strategy1Config) -> Decimal:
    return _money(max(config.min_commission, notional * config.commission_rate))


def _quantity_for_budget(price: Decimal, config: Strategy1Config) -> int:
    def cost(quantity: int) -> Decimal:
        notional = _money(price * quantity)
        return notional + _commission(notional, config)

    minimum = config.minimum_buy_quantity
    if cost(minimum) > config.buy_budget:
        return 0
    low = 0
    high = int((config.buy_budget + _CENT) / price) // config.quantity_step + 1
    while low < high:
        steps = (low + high + 1) // 2
        if cost(minimum + steps * config.quantity_step) <= config.buy_budget:
            low = steps
        else:
            high = steps - 1
    return minimum + low * config.quantity_step


def replay_strategy1(
    observations: Sequence[DailyObservation], config: Strategy1Config | None = None,
    *, allow_sparse_profit: bool = False, cash_dividends: Sequence[CashDividend] = (),
) -> Strategy1Result:
    """Replay surviving rockets; only completed prior closes can cancel an open buy.

    A rocket starts its own four-observation window. Missing calendar dates are
    not observations. Explicit open_buy_allowed controls deferred opening fills;
    full-day OHLC is used only after that close to count completed one-price
    limit-up sessions. Unspecified cash/exit branches raise instead of guessing.
    Complete P is required by default. Explicit sparse mode permits None only
    when no shares or unfinished opportunity remain at the closing decision.
    Explicit cash events lock post-trade record-close lots, accrue on ex-date,
    and pay before the payment-date open. Non-bar ex/payment dates are processed
    in calendar order before the next observed open, without future fills.
    """
    if type(allow_sparse_profit) is not bool:
        raise ValueError("allow_sparse_profit must be an explicit bool")
    config = config or Strategy1Config()
    bars = tuple(observations)
    _validate_observations(bars, allow_sparse_profit=allow_sparse_profit)
    dividends = tuple(cash_dividends)
    dividend_ids: set[str] = set()
    dividend_dates: set[tuple[date, date, date]] = set()
    observed_dates = {bar.date for bar in bars}
    for item in dividends:
        if not isinstance(item, CashDividend):
            raise ValueError("cash_dividends must contain explicit CashDividend events")
        key = (item.record_date, item.ex_date, item.payment_date)
        if item.event_id in dividend_ids or key in dividend_dates:
            raise ValueError("Duplicate CashDividend id or date tuple; consolidate an event explicitly")
        dividend_ids.add(item.event_id)
        dividend_dates.add(key)
        if item.record_date < bars[0].date:
            raise Strategy1RuleError(f"{item.event_id}: record_date predates the replay; opening entitlements are unsupported")
        if item.record_date <= bars[-1].date and item.record_date not in observed_dates:
            raise Strategy1RuleError(f"{item.event_id}: record_date {item.record_date} requires an actual daily observation")
    cash = config.initial_cash
    lots: list[Strategy1Lot] = []
    opportunities: list[Strategy1Opportunity] = []
    fills: list[Strategy1Fill] = []
    orders: list[Strategy1Order] = []
    equity: list[Strategy1Equity] = []
    closed_batches: list[Strategy1ClosedBatch] = []
    receivable = _ZERO
    entitlements: list[Strategy1DividendEntitlement] = []
    dividend_ledger: list[Strategy1DividendLedger] = []
    recorded: set[str] = set()
    accrued: set[str] = set()
    paid: set[str] = set()

    def credit_batch(buy_sequence: int, income: Decimal = _ZERO, received: Decimal = _ZERO) -> None:
        for lot_index, lot in enumerate(lots):
            if lot.buy_sequence == buy_sequence:
                lots[lot_index] = replace(lot, dividend_income=lot.dividend_income + income,
                                          dividend_cash_received=lot.dividend_cash_received + received)
                return
        for batch_index, batch in enumerate(closed_batches):
            if batch.buy_sequence == buy_sequence:
                closed_batches[batch_index] = replace(
                    batch, dividend_income=batch.dividend_income + income,
                    dividend_cash_received=batch.dividend_cash_received + received, pnl=batch.pnl + income,
                )
                return
        raise RuntimeError(f"Dividend refers to an unknown buy batch {buy_sequence}")

    def dividend_row(item: CashDividend, event_date: date, bar: DailyObservation, phase: str,
                     entitlement: Strategy1DividendEntitlement | None) -> None:
        dividend_ledger.append(Strategy1DividendLedger(
            item.event_id, event_date, bar.date, phase,
            entitlement.buy_sequence if entitlement else None,
            entitlement.quantity if entitlement else 0, item.cash_per_share,
            entitlement.amount if entitlement else _ZERO, cash, receivable, item.source,
        ))

    def process_dividends_before_open(bar: DailyObservation) -> None:
        nonlocal cash, receivable
        actions = sorted(
            (event_date, priority, item.event_id, item)
            for item in dividends if item.event_id in recorded
            for event_date, priority, done in ((item.ex_date, 0, accrued), (item.payment_date, 1, paid))
            if event_date <= bar.date and item.event_id not in done
        )
        for event_date, priority, _, item in actions:
            members = [(i, e) for i, e in enumerate(entitlements) if e.event_id == item.event_id]
            if priority == 0:
                for i, entitlement in members:
                    receivable = _money(receivable + entitlement.amount)
                    credit_batch(entitlement.buy_sequence, income=entitlement.amount)
                    entitlements[i] = replace(entitlement, recognized=True)
                    dividend_row(item, event_date, bar, "ex_date_receivable_before_open", entitlements[i])
                accrued.add(item.event_id)
            else:
                if item.event_id not in accrued:
                    raise RuntimeError("Dividend payment cannot precede accrual")
                for i, entitlement in members:
                    cash = _money(cash + entitlement.amount)
                    receivable = _money(receivable - entitlement.amount)
                    credit_batch(entitlement.buy_sequence, received=entitlement.amount)
                    entitlements[i] = replace(entitlement, paid=True)
                    dividend_row(item, event_date, bar, "cash_payment_before_open", entitlements[i])
                paid.add(item.event_id)
            if not members:
                dividend_row(item, event_date, bar,
                             "ex_date_receivable_before_open" if priority == 0 else "cash_payment_before_open", None)

    def record_dividends_after_close(bar: DailyObservation) -> None:
        for item in dividends:
            if item.record_date != bar.date:
                continue
            cumulative_quantity, allocated = 0, _ZERO
            for lot in lots:
                cumulative_quantity += lot.quantity
                cumulative_amount = _money(item.cash_per_share * cumulative_quantity)
                entitlement = Strategy1DividendEntitlement(
                    item.event_id, lot.buy_sequence, item.record_date, item.ex_date,
                    item.payment_date, lot.quantity, item.cash_per_share,
                    cumulative_amount - allocated, item.source,
                )
                allocated = cumulative_amount
                entitlements.append(entitlement)
                dividend_row(item, bar.date, bar, "record_date_after_close", entitlement)
            if not lots:
                dividend_row(item, bar.date, bar, "record_date_after_close", None)
            recorded.add(item.event_id)

    def mark_equity(bar: DailyObservation) -> None:
        market_value = _money(holdings() * bar.close)
        equity.append(Strategy1Equity(bar.date, cash, holdings(), bar.close,
                                     _money(cash + market_value + receivable), bar.profit_pct,
                                     receivable, market_value))

    def holdings() -> int:
        return sum(lot.quantity for lot in lots)

    def event(bar: DailyObservation, action: str, opportunity: Strategy1Opportunity | None, reason: str) -> None:
        orders.append(Strategy1Order(
            bar.date, action, opportunity.opportunity_id if opportunity else None,
            opportunity.rocket_date if opportunity else None,
            opportunity.trigger_date if opportunity else bar.date, reason,
        ))

    def buy(bar: DailyObservation, opportunity: Strategy1Opportunity, session: str) -> None:
        nonlocal cash
        if cash < config.buy_budget:
            raise Strategy1RuleError(
                f"{bar.date} opportunity {opportunity.opportunity_id}: available cash {cash} "
                f"is below the {config.buy_budget} buy budget; cash-shortfall handling is unspecified"
            )
        price = bar.open if session == "open" else bar.close
        quantity = _quantity_for_budget(price, config)
        if not quantity:
            opportunity.status = "rejected"
            event(bar, "buy_rejected", opportunity, "Budget cannot fund the minimum buy quantity including commission")
            return
        notional = _money(price * quantity)
        commission = _commission(notional, config)
        cash = _money(cash - notional - commission)
        sequence = len(fills) + 1
        lots.append(Strategy1Lot(
            sequence, opportunity.opportunity_id, opportunity.rocket_date, bar.date,
            quantity, price, notional, commission,
        ))
        reason = "Deferred buy at explicitly permitted next available open" if session == "open" else "First close profit strictly above 45%; ideal close fill"
        fills.append(Strategy1Fill(
            sequence, bar.date, session, "buy", opportunity.trigger_date or bar.date,
            opportunity.rocket_date, opportunity.opportunity_id, quantity, price,
            notional, commission, cash, holdings(), reason, bar.source,
        ))
        opportunity.status = "filled"
        event(bar, "buy_filled", opportunity, reason)

    for index, bar in enumerate(bars):
        process_dividends_before_open(bar)
        # This phase deliberately never accesses today's profit, high, low or
        # closing limit-up flag. These are not known at the opening timestamp.
        for opportunity in opportunities:
            if opportunity.status == "pending":
                if bar.open_buy_allowed:
                    buy(bar, opportunity, "open")
                else:
                    event(bar, "buy_deferred", opportunity, "Explicit open_buy_allowed is false; retry a later open")

        if bar.rocket:
            opportunity = Strategy1Opportunity(len(opportunities) + 1, bar.date, index)
            opportunities.append(opportunity)
            event(bar, "opportunity_created", opportunity, "Currently visible rocket; four actual daily observations including this day")

        # Missing P is allowed only after opening events and today's rocket are
        # known to leave neither shares nor an unfinished buying opportunity.
        # In particular, today's unknown close cannot cancel an opening fill.
        if bar.profit_pct is None:
            observing_ids = [o.opportunity_id for o in opportunities if o.status == "observing"]
            pending_ids = [o.opportunity_id for o in opportunities if o.status == "pending"]
            if lots or observing_ids or pending_ids:
                raise MissingProfitObservationError(bar.date, holdings(), observing_ids, pending_ids)
            record_dividends_after_close(bar)
            mark_equity(bar)
            continue

        for opportunity in opportunities:
            if opportunity.status not in ("observing", "pending"):
                continue
            if bar.profit_pct < 40:
                opportunity.status = "cancelled"
                event(bar, "buy_cancelled", opportunity, "Close profit strictly below 40% before execution")
                continue
            if opportunity.status == "observing":
                if index - opportunity.rocket_index >= 4:
                    opportunity.status = "expired"
                    event(bar, "buy_expired", opportunity, "Four-observation buy window ended without a trigger")
                    continue
                if bar.profit_pct > 45:
                    opportunity.trigger_date = bar.date
                    event(bar, "buy_triggered", opportunity, "First close profit strictly above 45% in the four-observation window")
                    if bar.close_limit_up:
                        opportunity.status = "pending"
                        event(bar, "buy_deferred", opportunity, "Explicit close limit-up; first attempt at next available open")
                    else:
                        buy(bar, opportunity, "close")
                elif index - opportunity.rocket_index == 3:
                    opportunity.status = "expired"
                    event(bar, "buy_expired", opportunity, "Four-observation buy window ended without a trigger")

            if opportunity.status == "pending":
                one_price_up = bar.close_limit_up and bar.open == bar.high == bar.low == bar.close
                opportunity.consecutive_one_price_up_days = (
                    opportunity.consecutive_one_price_up_days + 1 if one_price_up else 0
                )
                if opportunity.consecutive_one_price_up_days >= 3:
                    opportunity.status = "cancelled"
                    event(bar, "buy_cancelled", opportunity, "Three completed consecutive one-price limit-up sessions, counting from trigger day")

        if bar.profit_pct < 40:
            sell_lots = [lot for lot in lots if lot.entry_date < bar.date]
            same_day_lots = [lot for lot in lots if lot.entry_date == bar.date]
            if sell_lots:
                if not bar.sell_allowed:
                    raise Strategy1RuleError(
                        f"{bar.date}: exit triggered but sell_allowed is false; blocked-sell handling is unspecified"
                    )
                quantity = sum(lot.quantity for lot in sell_lots)
                notional = _money(quantity * bar.close)
                commission = _commission(notional, config)
                if cash + notional < commission:
                    raise Strategy1RuleError(
                        f"{bar.date}: cash and sale proceeds do not cover commission; "
                        "fee-funding handling is unspecified"
                    )
                cash = _money(cash + notional - commission)
                sequence = len(fills) + 1
                lots = same_day_lots
                fills.append(Strategy1Fill(
                    sequence, bar.date, "close", "sell", bar.date, None, None,
                    quantity, bar.close, notional, commission, cash, holdings(),
                    "Close profit strictly below 40%; sell old shares and retain today's purchases", bar.source,
                ))
                allocated_notional, allocated_commission, allocated_quantity = _ZERO, _ZERO, 0
                for lot in sell_lots:
                    # Cumulative rounding preserves every cent and cannot make
                    # the final lot's allocated fee negative after many lots.
                    allocated_quantity += lot.quantity
                    cumulative_notional = _money(notional * allocated_quantity / quantity)
                    cumulative_commission = _money(commission * allocated_quantity / quantity)
                    proceeds = cumulative_notional - allocated_notional
                    cost = cumulative_commission - allocated_commission
                    allocated_notional, allocated_commission = cumulative_notional, cumulative_commission
                    closed_batches.append(Strategy1ClosedBatch(
                        lot.buy_sequence, sequence, lot.opportunity_id, lot.rocket_date,
                        lot.entry_date, bar.date, lot.quantity, lot.entry_cost,
                        proceeds, cost, _money(proceeds - cost - lot.entry_cost + lot.dividend_income),
                        lot.dividend_income, lot.dividend_cash_received,
                    ))
                event(bar, "sell_filled", None, "Exit at ideal close; FIFO batch accounting; no order carried forward")
            if same_day_lots:
                event(bar, "sell_t1_retained", None, "Today's purchases are ineligible; next close is checked afresh")

        record_dividends_after_close(bar)
        mark_equity(bar)

    for opportunity in opportunities:
        if opportunity.status in ("observing", "pending"):
            event(bars[-1], "buy_unfilled_at_end", opportunity, "Data ended; no fabricated future fill")
    return Strategy1Result(
        config, fills, orders, equity, closed_batches, lots,
        [replace(opportunity) for opportunity in opportunities],
        cash_dividend_events=list(dividends), dividend_entitlements=entitlements,
        dividend_ledger=dividend_ledger,
    )


def calculate_strategy1_metrics(result: Strategy1Result) -> dict[str, object]:
    """Account metrics use all cash, realized batches and marked open holdings."""
    if not result.equity:
        raise ValueError("Cannot calculate metrics without daily equity")
    initial, ending = result.config.initial_cash, result.equity[-1]
    peak, max_drawdown = initial, Decimal(0)
    for point in result.equity:
        peak = max(peak, point.equity)
        max_drawdown = max(max_drawdown, (peak - point.equity) / peak)
    total_return = ending.equity / initial - 1
    days = (ending.date - result.equity[0].date).days
    annualized: float | None = None
    if days:
        try:
            annualized = math.expm1(math.log(float(ending.equity / initial)) * 365.25 / days) if ending.equity else -1.0
            if not math.isfinite(annualized):
                annualized = None
        except (OverflowError, ValueError):
            annualized = None
    fees = sum((fill.commission for fill in result.fills), _ZERO)
    realized = sum((batch.pnl for batch in result.closed_batches), _ZERO)
    open_cost = sum((lot.entry_cost for lot in result.ending_lots), _ZERO)
    market_value = _money(ending.quantity * ending.close)
    open_dividend_income = sum((lot.dividend_income for lot in result.ending_lots), _ZERO)
    unrealized_price = market_value - open_cost
    unrealized = unrealized_price + open_dividend_income
    dividend_income = sum((row.amount for row in result.dividend_ledger if row.phase == "ex_date_receivable_before_open"), _ZERO)
    dividend_cash = sum((row.amount for row in result.dividend_ledger if row.phase == "cash_payment_before_open"), _ZERO)
    if realized + unrealized != ending.equity - initial:
        raise RuntimeError("Batch PnL does not reconcile with account equity")
    batches = result.closed_batches
    return {
        "start_date": result.equity[0].date,
        "end_date": ending.date,
        "bars": len(result.equity),
        "initial_cash": initial,
        "final_equity": ending.equity,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "transaction_count": len(result.fills),
        "buy_count": sum(fill.side == "buy" for fill in result.fills),
        "sell_count": sum(fill.side == "sell" for fill in result.fills),
        "closed_batch_count": len(batches),
        "win_rate": Decimal(sum(batch.pnl > 0 for batch in batches)) / len(batches) if batches else None,
        "total_fees": fees,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "unrealized_price_pnl": unrealized_price,
        "open_batch_dividend_income": open_dividend_income,
        "dividend_income": dividend_income,
        "dividend_cash_received": dividend_cash,
        "ending_dividend_receivable": ending.dividend_receivable,
        "cash_dividend_event_count": len(result.cash_dividend_events),
        "ending_cash": ending.cash,
        "ending_quantity": ending.quantity,
        "ending_market_value": market_value,
        "underlying_close_return": ending.close / result.equity[0].close - 1,
        "cancelled_opportunities": sum(o.status == "cancelled" for o in result.opportunities),
        "expired_opportunities": sum(o.status == "expired" for o in result.opportunities),
        "pending_opportunities": sum(o.status in ("observing", "pending") for o in result.opportunities),
        "signal_basis": result.signal_basis,
    }
