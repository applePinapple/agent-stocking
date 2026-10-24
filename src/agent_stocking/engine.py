"""Deterministic, long-only, next-open execution for one daily stock series."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from .data import validate_bars
from .models import (
    Action,
    BacktestResult,
    Bar,
    EngineConfig,
    EquityPoint,
    OrderEvent,
    Portfolio,
    Signal,
    Strategy,
    Trade,
)

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_BPS = Decimal("10000")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _fees(
    notional: Decimal, side: Action, config: EngineConfig
) -> tuple[Decimal, Decimal, Decimal]:
    commission = _money(max(notional * config.commission_rate, config.min_commission))
    tax = _money(notional * config.sell_tax_rate) if side is Action.SELL else _ZERO
    transfer_fee = _money(notional * config.transfer_fee_rate)
    return commission, tax, transfer_fee


def _buy_cost(quantity: int, price: Decimal, config: EngineConfig) -> Decimal:
    notional = _money(price * quantity)
    return notional + sum(_fees(notional, Action.BUY, config), _ZERO)


def _affordable_quantity(cash: Decimal, price: Decimal, config: EngineConfig) -> int:
    """Find the largest whole-lot order, including rounded minimum fees."""
    low = 0
    # A sub-cent execution price can round a notional down to the available
    # cash. Keep a conservative upper bound and test the rounded cost below.
    high = int((cash + _CENT) // (price * config.lot_size))
    while low < high:
        lots = (low + high + 1) // 2
        if _buy_cost(lots * config.lot_size, price, config) <= cash:
            low = lots
        else:
            high = lots - 1
    return low * config.lot_size


def _checked_signal(value: object) -> Signal:
    if not isinstance(value, Signal) or not isinstance(value.action, Action):
        raise ValueError("Strategy must return Signal with an Action enum; execution stopped")
    if not isinstance(value.reason, str):
        raise ValueError("Signal.reason must be a string; execution stopped")
    return value


def run_backtest(
    bars: Sequence[Bar], strategy: Strategy, config: EngineConfig
) -> BacktestResult:
    """Run close-time decisions at the next observed session's open.

    A rejected order expires immediately; only a new strategy signal can retry.
    Settlement is measured in observed daily sessions, including supplied zero-volume
    sessions. Prices retain exact configured slippage; monetary amounts round to
    cents using HALF_UP. Final positions remain marked to the final close.
    """
    history_bars = tuple(bars)
    validate_bars(history_bars)
    if config.require_trade_permissions:
        for bar in history_bars:
            if type(bar.buy_allowed) is not bool or type(bar.sell_allowed) is not bool:
                raise ValueError(
                    f"{bar.date}: strict execution requires explicit buy_allowed and sell_allowed"
                )

    initial_cash = _money(config.initial_cash)
    cash = initial_cash
    quantity = 0
    entry_index: int | None = None
    pending: tuple[date, Signal] | None = None
    trades: list[Trade] = []
    orders: list[OrderEvent] = []
    equity: list[EquityPoint] = []

    for index, bar in enumerate(history_bars):
        if pending is not None:
            signal_date, signal = pending
            pending = None
            side = signal.action
            status = "rejected"
            reason = ""

            if side is Action.BUY and quantity:
                status, reason = "ignored", "BUY ignored: a position is already held (no pyramiding)"
            elif side is Action.SELL and not quantity:
                status, reason = "ignored", "SELL ignored: no position is held (no short selling)"
            elif bar.volume == 0:
                reason = "Order cancelled: session has zero volume"
            elif side is Action.BUY and bar.buy_allowed is False:
                reason = "Order cancelled: buy_allowed is false"
            elif side is Action.SELL and bar.sell_allowed is False:
                reason = "Order cancelled: sell_allowed is false"
            elif (
                side is Action.SELL
                and entry_index is not None
                and index - entry_index < config.settlement_days
            ):
                reason = "Order cancelled: position has not reached the configured settlement session"
            else:
                direction = Decimal(1) if side is Action.BUY else Decimal(-1)
                price = bar.open * (Decimal(1) + direction * config.slippage_bps / _BPS)
                if price < bar.low or price > bar.high or price <= 0:
                    reason = "Order cancelled: slippage-adjusted open is outside the observed low/high"
                else:
                    fill_quantity = (
                        _affordable_quantity(cash, price, config)
                        if side is Action.BUY
                        else quantity
                    )
                    if fill_quantity == 0:
                        reason = "Order cancelled: insufficient cash for one lot including fees"
                    else:
                        notional = _money(price * fill_quantity)
                        commission, tax, transfer_fee = _fees(notional, side, config)
                        fees = commission + tax + transfer_fee
                        cash_after = _money(
                            cash - notional - fees
                            if side is Action.BUY
                            else cash + notional - fees
                        )
                        if cash_after < 0:
                            reason = "Order cancelled: insufficient cash to pay transaction fees"
                        else:
                            cash = cash_after
                            if side is Action.BUY:
                                quantity = fill_quantity
                                entry_index = index
                            else:
                                quantity = 0
                                entry_index = None
                            trades.append(
                                Trade(
                                    signal_date=signal_date,
                                    date=bar.date,
                                    side=side,
                                    quantity=fill_quantity,
                                    price=price,
                                    notional=notional,
                                    commission=commission,
                                    tax=tax,
                                    transfer_fee=transfer_fee,
                                    cash_after=cash,
                                    reason=signal.reason,
                                )
                            )
                            status, reason = "filled", "Executed at the next observed session open"
            if signal.reason:
                reason = f"{reason}; signal: {signal.reason}"
            orders.append(OrderEvent(signal_date, bar.date, side, status, reason))

        equity.append(
            EquityPoint(bar.date, cash, quantity, bar.close, _money(cash + quantity * bar.close))
        )
        signal = _checked_signal(
            strategy.on_close(history_bars[: index + 1], Portfolio(cash, quantity))
        )
        if signal.action is not Action.HOLD:
            pending = (bar.date, signal)

    if pending is not None:
        signal_date, signal = pending
        reason = "Order expired: no subsequent session exists in the supplied data"
        if signal.reason:
            reason = f"{reason}; signal: {signal.reason}"
        orders.append(OrderEvent(signal_date, None, signal.action, "expired", reason))

    return BacktestResult(trades, orders, equity, initial_cash)
