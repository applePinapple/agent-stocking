"""Causal S11/S13 order state; execution prices come only from explicit fills.

This is an execution adapter, not a market-data replay or a return report. Policy
values must be supplied explicitly. It cannot infer permissions from quotations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from typing import Literal


def _decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} Decimal")


@dataclass(frozen=True)
class ScaleInPolicy:
    sid: Literal[11, 13]
    initial_cash: Decimal
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    quantity_unit: int
    weights: tuple[Decimal, ...]
    add_requires_above_ma: bool
    price_basis: str
    # Freezing each cycle's first-signal available cash includes expected buy fees.
    capital_basis: str

    def __post_init__(self) -> None:
        if self.sid not in {11, 13}:
            raise ValueError("Only S11 and S13 are implemented")
        for name in ["initial_cash", "buy_fee_rate", "sell_fee_rate"]:
            _decimal(getattr(self, name), name, positive=name == "initial_cash")
        if not isinstance(self.quantity_unit, int) or isinstance(self.quantity_unit, bool) or self.quantity_unit <= 0:
            raise ValueError("quantity_unit must be a positive integer")
        expected = (Decimal("0.5"), Decimal("0.5")) if self.sid == 11 else tuple(map(Decimal, ["0.1", "0.2", "0.3", "0.4"]))
        if self.weights != expected:
            raise ValueError("This explicit adapter uses equal half budgets for S11 or exact TPS weights for S13")
        if self.sid == 13 and not self.add_requires_above_ma:
            raise ValueError("TPS cannot add at or below MA200")
        if not self.price_basis or self.capital_basis != "first_signal_cash_including_buy_fees":
            raise ValueError("An explicit consistent price basis and supported capital basis are required")


@dataclass(frozen=True)
class ScaleInObservation:
    date: date
    close: Decimal
    ma200: Decimal | None
    rsi: Decimal | None
    source: str
    price_basis: str
    complete: bool = True


@dataclass(frozen=True)
class ScaleInOrder:
    order_id: int
    sid: int
    cycle: int
    side: Literal["buy", "sell"]
    stage: int
    signal_date: date
    signal_close: Decimal
    gross_cash_budget: Decimal | None
    requested_sell_quantity: int | None
    reference_actual_fill_price: Decimal | None


@dataclass(frozen=True)
class ExecutionQuote:
    date: date
    price: Decimal
    buy_allowed: bool | None
    sell_allowed: bool | None
    source: str
    price_basis: str


@dataclass(frozen=True)
class SizedScaleInOrder:
    request: ScaleInOrder
    execution_date: date
    quantity: int
    sizing_quote_price: Decimal


@dataclass(frozen=True)
class ScaleInFill:
    order_id: int
    date: date
    quantity: int
    actual_price: Decimal
    actual_fee: Decimal
    permission: bool
    source: str
    price_basis: str


@dataclass(frozen=True)
class ScaleInTerminalFill:
    """Explicit final-close receipt, distinct from a next-open strategy order."""
    date: date
    quantity: int
    actual_price: Decimal
    actual_fee: Decimal
    permission: bool
    source: str
    price_basis: str


class ScaleInState:
    """One security/account. Stages advance only on complete, explicit fills.

    Pending orders can be left pending for an externally confirmed retry rule or
    explicitly rejected. A pending request blocks a new request; neither prices
    nor a new RSI signal silently imply that an old order has filled.
    """

    def __init__(self, policy: ScaleInPolicy):
        self.policy = policy
        self.cash = policy.initial_cash
        self.quantity = 0
        self.stage = 0
        self.cycle = 0
        self.cycle_cash: Decimal | None = None
        self.last_tranche_fill_price: Decimal | None = None
        self.last_fill_date: date | None = None
        self.last_observation_date: date | None = None
        self.previous_rsi: Decimal | None = None
        self.pending: ScaleInOrder | None = None
        self.sized: SizedScaleInOrder | None = None
        self._order_sequence = 0
        self.terminated = False
        self.audit: list[dict[str, object]] = []

    def _record(self, event: str, **values: object) -> None:
        self.audit.append({"event": event, **values})

    def _basis(self, basis: str, source: str) -> None:
        if not source or basis != self.policy.price_basis:
            raise ValueError("Explicit source and consistent price basis required")

    def on_close(self, observation: ScaleInObservation) -> ScaleInOrder | None:
        if self.terminated:
            raise ValueError("Cannot add observations after explicit terminal execution")
        self._basis(observation.price_basis, observation.source)
        _decimal(observation.close, "close", positive=True)
        if not observation.complete:
            raise ValueError("Incomplete daily data cannot trigger an order")
        if self.last_observation_date is not None and observation.date <= self.last_observation_date:
            raise ValueError("Completed observations must be strictly chronological")
        if self.last_fill_date is not None and observation.date < self.last_fill_date:
            raise ValueError("Cannot submit a close before an already applied execution")
        if observation.ma200 is not None:
            _decimal(observation.ma200, "ma200", positive=True)
        if observation.rsi is not None:
            _decimal(observation.rsi, "rsi")
            if observation.rsi > 100:
                raise ValueError("RSI must be within 0..100")
        prior_rsi = self.previous_rsi
        self.previous_rsi = observation.rsi
        self.last_observation_date = observation.date
        if self.pending is not None:
            self._record("no_new_order_pending", date=observation.date, order_id=self.pending.order_id)
            return None
        if observation.rsi is None:
            return None
        above = observation.ma200 is not None and observation.close > observation.ma200
        exit_threshold = Decimal(55 if self.policy.sid == 11 else 70)
        if self.quantity and observation.rsi > exit_threshold:
            return self._request("sell", observation)
        if not self.quantity:
            initial = above and observation.rsi < 25
            if self.policy.sid == 13:
                initial = initial and prior_rsi is not None and prior_rsi < 25
            if initial:
                if self.cycle_cash is None:
                    self.cycle += 1
                    self.cycle_cash = self.cash
                return self._request("buy", observation)
            return None
        if self.stage >= len(self.policy.weights):
            return None
        if self.policy.add_requires_above_ma and not above:
            return None
        if self.policy.sid == 11:
            add = observation.rsi < 20
        else:
            add = self.last_tranche_fill_price is not None and observation.close < self.last_tranche_fill_price
        if add:
            return self._request("buy", observation)
        return None

    def _request(self, side: Literal["buy", "sell"], observation: ScaleInObservation) -> ScaleInOrder:
        self._order_sequence += 1
        assert self.cycle_cash is not None
        order = ScaleInOrder(self._order_sequence, self.policy.sid, self.cycle, side,
                             self.stage + 1 if side == "buy" else self.stage,
                             observation.date, observation.close,
                             self.cycle_cash * self.policy.weights[self.stage] if side == "buy" else None,
                             self.quantity if side == "sell" else None,
                             self.last_tranche_fill_price)
        self.pending, self.sized = order, None
        self._record("order_requested", order=order)
        return order

    def size_at_execution(self, quote: ExecutionQuote) -> SizedScaleInOrder | None:
        self._basis(quote.price_basis, quote.source)
        _decimal(quote.price, "execution quote", positive=True)
        if self.pending is None:
            raise ValueError("No pending request")
        if quote.date <= self.pending.signal_date or (self.last_observation_date is not None and quote.date <= self.last_observation_date):
            raise ValueError("Next-open execution must follow its completed signal and cannot be backdated")
        permission = quote.buy_allowed if self.pending.side == "buy" else quote.sell_allowed
        if permission is not True:
            self.sized = None
            self._record("execution_blocked", date=quote.date, order_id=self.pending.order_id, permission=permission, source=quote.source)
            return None
        if self.pending.side == "buy":
            assert self.pending.gross_cash_budget is not None
            budget = min(self.pending.gross_cash_budget, self.cash)
            unit_cost = quote.price * (1 + self.policy.buy_fee_rate) * self.policy.quantity_unit
            units = int((budget / unit_cost).to_integral_value(rounding=ROUND_FLOOR))
            quantity = units * self.policy.quantity_unit
        else:
            quantity = self.quantity
        if quantity <= 0:
            self.sized = None
            self._record("zero_affordable_quantity", date=quote.date, order_id=self.pending.order_id)
            return None
        self.sized = SizedScaleInOrder(self.pending, quote.date, quantity, quote.price)
        self._record("order_sized_from_execution_quote", sized=self.sized, source=quote.source)
        return self.sized

    def reject(self, order_id: int, *, date: date, reason: str) -> None:
        if self.pending is None or self.pending.order_id != order_id or not reason:
            raise ValueError("Reject must identify the actual pending request and reason")
        if date < self.pending.signal_date:
            raise ValueError("Reject cannot predate signal")
        self._record("order_rejected", date=date, order_id=order_id, reason=reason, stage_unchanged=self.stage)
        self.pending = self.sized = None

    def reduce_sized_buy_quantity(self, quantity: int, *, reason: str) -> SizedScaleInOrder:
        """Explicit sizing-only reduction for an externally supplied fee model.

        It does not fill an order, increase its budget or advance its stage.
        A zero affordable quantity must use the normal rejection lifecycle.
        """
        if (self.pending is None or self.pending.side != "buy" or self.sized is None
                or type(quantity) is not int or not 0 < quantity <= self.sized.quantity
                or quantity % self.policy.quantity_unit or not reason):
            raise ValueError("Only positive whole-unit reductions of a pending sized buy are accepted")
        previous=self.sized.quantity
        self.sized=SizedScaleInOrder(self.pending,self.sized.execution_date,quantity,self.sized.sizing_quote_price)
        self._record("sized_buy_quantity_reduced_before_fill", previous_quantity=previous, quantity=quantity, reason=reason)
        return self.sized

    def apply_fill(self, fill: ScaleInFill) -> None:
        self._basis(fill.price_basis, fill.source)
        _decimal(fill.actual_price, "actual_price", positive=True)
        _decimal(fill.actual_fee, "actual_fee")
        if fill.permission is not True or self.pending is None or self.sized is None:
            raise ValueError("Explicit permission, pending request and execution sizing are required")
        if fill.order_id != self.pending.order_id or fill.date != self.sized.execution_date:
            raise ValueError("Fill does not match pending execution")
        if self.last_observation_date is not None and fill.date <= self.last_observation_date:
            raise ValueError("An opening fill cannot be backdated after that day's close was observed")
        if fill.quantity != self.sized.quantity or not isinstance(fill.quantity, int) or isinstance(fill.quantity, bool):
            raise ValueError("Adapter accepts complete fills only; partial fills require a separate explicit lifecycle")
        notional = fill.actual_price * fill.quantity
        if self.pending.side == "buy":
            assert self.pending.gross_cash_budget is not None
            cost = notional + fill.actual_fee
            if cost > self.pending.gross_cash_budget or cost > self.cash:
                raise ValueError("Actual fill and fee exceed the frozen stage budget or available cash")
            self.cash -= cost
            self.quantity += fill.quantity
            self.stage += 1
            self.last_tranche_fill_price = fill.actual_price
        else:
            if fill.quantity != self.quantity or self.cash + notional < fill.actual_fee:
                raise ValueError("Exit must clear the actual whole position without negative cash")
            self.cash += notional - fill.actual_fee
            self.quantity = self.stage = 0
            self.cycle_cash = self.last_tranche_fill_price = None
        self.last_fill_date = fill.date
        self._record("actual_fill_applied", fill=fill, resulting_stage=self.stage,
                     cash=self.cash, quantity=self.quantity)
        self.pending = self.sized = None

    def apply_terminal_fill(self, fill: ScaleInTerminalFill, *, allow_same_day_acquired_sale: bool) -> None:
        """Apply a caller-confirmed final close; never invent a next-day fill.

        The caller must supply the actual or explicitly modeled receipt and the
        separately approved same-day settlement policy. Pending next-open orders
        must be explicitly expired/rejected first, preserving their audit trail.
        """
        self._basis(fill.price_basis, fill.source)
        _decimal(fill.actual_price, "terminal price", positive=True)
        _decimal(fill.actual_fee, "terminal fee")
        if type(allow_same_day_acquired_sale) is not bool:
            raise ValueError("Explicit terminal settlement policy required")
        if (self.terminated or self.pending is not None or self.sized is not None
                or fill.permission is not True or self.quantity <= 0
                or fill.date != self.last_observation_date
                or type(fill.quantity) is not int or fill.quantity != self.quantity):
            raise ValueError("Terminal receipt must clear the actual final-day position with explicit permission")
        if not allow_same_day_acquired_sale and self.last_fill_date == fill.date:
            raise ValueError("Terminal sale violates explicit same-day acquisition settlement restriction")
        proceeds=fill.actual_price*fill.quantity
        if self.cash+proceeds<fill.actual_fee:
            raise ValueError("Terminal fee would create negative cash")
        self.cash+=proceeds-fill.actual_fee
        self.quantity=self.stage=0
        self.cycle_cash=self.last_tranche_fill_price=None
        self.last_fill_date=fill.date
        self.terminated=True
        self._record("explicit_terminal_fill_applied", fill=fill, cash=self.cash, quantity=0)

    def snapshot(self) -> dict[str, object]:
        """No equity/return metric: only causal order, cash and position state."""
        return {"sid": self.policy.sid, "cash": str(self.cash), "quantity": self.quantity,
                "stage": self.stage, "cycle": self.cycle,
                "cycle_cash": str(self.cycle_cash) if self.cycle_cash is not None else None,
                "last_actual_tranche_price": str(self.last_tranche_fill_price) if self.last_tranche_fill_price is not None else None,
                "pending_order_id": self.pending.order_id if self.pending else None,
                "pending_budget": str(self.pending.gross_cash_budget) if self.pending and self.pending.gross_cash_budget is not None else None}
