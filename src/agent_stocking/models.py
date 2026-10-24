from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Protocol, Sequence


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class Bar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal | None = None
    buy_allowed: bool | None = None
    sell_allowed: bool | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    action: Action
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Portfolio:
    cash: Decimal
    quantity: int


class Strategy(Protocol):
    def on_close(self, history: Sequence[Bar], portfolio: Portfolio) -> Signal:
        """Return a close-time decision using only the supplied completed bars."""
        ...


@dataclass(frozen=True, slots=True)
class EngineConfig:
    initial_cash: Decimal
    lot_size: int
    commission_rate: Decimal
    min_commission: Decimal
    sell_tax_rate: Decimal
    transfer_fee_rate: Decimal
    slippage_bps: Decimal
    settlement_days: int = 1
    require_trade_permissions: bool = True

    def __post_init__(self) -> None:
        amounts = (
            "initial_cash", "commission_rate", "min_commission", "sell_tax_rate",
            "transfer_fee_rate", "slippage_bps",
        )
        for name in amounts:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        try:
            whole_cents = self.initial_cash.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ValueError("initial_cash is outside the supported amount range") from exc
        if self.initial_cash != whole_cents:
            raise ValueError("initial_cash must be specified in whole cents")
        for name in ("commission_rate", "sell_tax_rate", "transfer_fee_rate"):
            if getattr(self, name) >= 1:
                raise ValueError(f"{name} must be below 1 (use a decimal rate)")
        if self.slippage_bps >= 10000:
            raise ValueError("slippage_bps must be below 10000")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")
        if type(self.settlement_days) is not int or self.settlement_days < 0:
            raise ValueError("settlement_days must be a nonnegative integer")
        if type(self.require_trade_permissions) is not bool:
            raise ValueError("require_trade_permissions must be a boolean")


@dataclass(frozen=True, slots=True)
class Trade:
    signal_date: date
    date: date
    side: Action
    quantity: int
    price: Decimal
    notional: Decimal
    commission: Decimal
    tax: Decimal
    transfer_fee: Decimal
    cash_after: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class OrderEvent:
    signal_date: date
    execution_date: date | None
    side: Action
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    date: date
    cash: Decimal
    quantity: int
    close: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: list[Trade]
    orders: list[OrderEvent]
    equity: list[EquityPoint]
    initial_cash: Decimal

