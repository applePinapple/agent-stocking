"""Shared-cash execution, isolated from single-stock all-cash backtests.

All fill policies and fees are explicit. This module does not infer trading
permission from OHLCV and does not download, alter or manufacture market data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Callable, Mapping, Sequence, Any

CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_nonnegative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative Decimal")


@dataclass(frozen=True, slots=True)
class PortfolioExecutionConfig:
    initial_cash: Decimal
    max_positions: int
    lot_sizes: Mapping[str, int]
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    minimum_buy_fee: Decimal
    minimum_sell_fee: Decimal
    slippage_bps: Decimal
    settlement_market_sessions: int
    insufficient_cash: str
    failed_order_policy: str
    price_basis: str
    corporate_action_policy: str
    data_kind: str

    def __post_init__(self):
        for name in ("initial_cash", "buy_fee_rate", "sell_fee_rate", "minimum_buy_fee", "minimum_sell_fee", "slippage_bps"):
            decimal_nonnegative(getattr(self, name), name)
        if self.initial_cash <= 0 or money(self.initial_cash) != self.initial_cash:
            raise ValueError("initial_cash must be positive whole cents")
        if type(self.max_positions) is not int or self.max_positions <= 0:
            raise ValueError("max_positions must be a positive integer")
        if type(self.settlement_market_sessions) is not int or self.settlement_market_sessions < 0:
            raise ValueError("settlement_market_sessions must be a nonnegative integer")
        if not self.lot_sizes or any(not s or type(n) is not int or n <= 0 for s, n in self.lot_sizes.items()):
            raise ValueError("Explicit positive per-security lot sizes are required")
        object.__setattr__(self, "lot_sizes", MappingProxyType(dict(self.lot_sizes)))
        if self.buy_fee_rate >= 1 or self.sell_fee_rate >= 1 or self.slippage_bps >= 10000:
            raise ValueError("Invalid fee rate or slippage")
        if self.insufficient_cash not in ("reject", "resize"):
            raise ValueError("Explicit insufficient_cash=reject/resize required")
        if self.failed_order_policy not in ("cancel", "retry_next_market_open"):
            raise ValueError("Explicit failed_order_policy required")
        if self.data_kind not in ("synthetic", "verified_historical"):
            raise ValueError("data_kind must distinguish synthetic from verified inputs")
        if not self.price_basis or self.corporate_action_policy not in ("verified_none_required", "explicitly_accepted_adjusted_price_model"):
            raise ValueError("Price basis and a verified/accepted corporate-action policy are required")


@dataclass(frozen=True, slots=True)
class OpenQuote:
    price: Decimal
    buy_allowed: bool | None
    sell_allowed: bool | None

    def __post_init__(self):
        decimal_nonnegative(self.price, "price")
        if self.price <= 0:
            raise ValueError("Execution quote must be positive")
        if any(x is not None and type(x) is not bool for x in (self.buy_allowed, self.sell_allowed)):
            raise ValueError("Permissions must be explicit booleans or unknown")


@dataclass(frozen=True, slots=True)
class PortfolioOrderScope:
    """Exact executable order offered to an optional evidence-backed checker.

    Quantity is shares, never an implicitly converted native-ledger unit.
    Price includes configured slippage and retains the configured price basis.
    """
    execution_date: date
    symbol: str
    phase: str
    side: str
    quantity: int
    quantity_unit: str
    price: Decimal
    price_basis: str

    def __post_init__(self):
        if type(self.execution_date) is not date or not self.symbol or self.phase not in ("open","close") or self.side not in ("buy","sell"):
            raise ValueError("Exact date, security, phase and side required for scoped permission")
        if type(self.quantity) is not int or self.quantity<=0 or self.quantity_unit!="shares" or not self.price_basis:
            raise ValueError("Exact positive share quantity and price basis required")
        decimal_nonnegative(self.price,"scoped execution price")
        if self.price<=0:raise ValueError("Scoped execution price must be positive")


OrderAuthorizer = Callable[[PortfolioOrderScope], bool | None]


class PortfolioOrderAuthorizationError(ValueError):
    def __init__(self, message: str, scope: PortfolioOrderScope):
        super().__init__(message)
        self.order_scope=scope


@dataclass(frozen=True, slots=True)
class BuyIntent:
    symbol: str
    cash_budget: Decimal
    reason: str

    def __post_init__(self):
        decimal_nonnegative(self.cash_budget, "cash_budget")
        if not self.symbol or self.cash_budget <= 0:
            raise ValueError("BuyIntent needs a symbol and positive fee-inclusive cash budget")


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    signal_date: date
    sell_symbols: tuple[str, ...]
    buys: tuple[BuyIntent, ...]
    reason: str

    def __post_init__(self):
        if type(self.signal_date) is not date:
            raise ValueError("signal_date must be a calendar date")
        if len(set(self.sell_symbols)) != len(self.sell_symbols) or len({b.symbol for b in self.buys}) != len(self.buys):
            raise ValueError("Duplicate orders in one portfolio decision")


@dataclass(frozen=True, slots=True)
class HeldPosition:
    symbol: str
    quantity: int
    entry_session_index: int
    entry_cost: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioFill:
    signal_date: date
    execution_date: date
    symbol: str
    side: str
    quantity: int
    price: Decimal
    notional: Decimal
    fee: Decimal
    cash_after: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class PortfolioOrderEvent:
    signal_date: date
    execution_date: date | None
    symbol: str
    side: str
    status: str
    reason: str


class SharedCashAccount:
    """One cash balance for every symbol; sells run before ranked buys.

    submit/execute are separate phases. Only an explicit future OPEN quote may
    execute a close signal. A denied sell keeps its position and slot occupied.
    End holdings are marked, never silently closed.
    """
    def __init__(self, config: PortfolioExecutionConfig, market_sessions: Sequence[date]):
        sessions=tuple(market_sessions)
        if not sessions or any(type(d) is not date for d in sessions) or sessions!=tuple(sorted(set(sessions))):
            raise ValueError("An explicit ascending unique market calendar is required")
        self.config = config
        self._session_indices={d:i for i,d in enumerate(sessions)}
        self.cash = config.initial_cash
        self.positions: dict[str, HeldPosition] = {}
        self.fills: list[PortfolioFill] = []
        self.orders: list[PortfolioOrderEvent] = []
        self._pending: list[PortfolioDecision] = []
        self._last_execution_date: date | None = None

    def submit(self, decision: PortfolioDecision) -> None:
        if self._pending:
            raise ValueError("Resolve pending market-open decisions before submitting a new close")
        self._pending.append(decision)

    def _fee(self, notional: Decimal, side: str) -> Decimal:
        rate = self.config.buy_fee_rate if side == "buy" else self.config.sell_fee_rate
        minimum = self.config.minimum_buy_fee if side == "buy" else self.config.minimum_sell_fee
        return money(max(notional * rate, minimum))

    def _quantity(self, budget: Decimal, price: Decimal, symbol: str) -> int:
        lot = self.config.lot_sizes[symbol]
        lo, hi = 0, int((budget + CENT) // (price * lot))
        while lo < hi:
            mid = (lo + hi + 1) // 2
            value = money(price * mid * lot)
            if value + self._fee(value, "buy") <= budget:
                lo = mid
            else:
                hi = mid - 1
        return lo * lot

    def _staged(self) -> SharedCashAccount:
        staged=SharedCashAccount(self.config,tuple(self._session_indices))
        staged.cash=self.cash
        staged.positions=dict(self.positions)
        staged.fills=list(self.fills)
        staged.orders=list(self.orders)
        staged._pending=list(self._pending)
        staged._last_execution_date=self._last_execution_date
        return staged

    def _commit(self, staged: SharedCashAccount) -> None:
        self.cash=staged.cash
        self.positions=staged.positions
        self.fills=staged.fills
        self.orders=staged.orders
        self._pending=staged._pending
        self._last_execution_date=staged._last_execution_date

    def _allowed(self, raw: bool | None, scope: PortfolioOrderScope,
                 order_authorizer: OrderAuthorizer | None) -> bool:
        if raw is False:return False
        # When selected, the scoped checker is an additional exact-match gate;
        # a generic True quote cannot bypass an unknown quantity/price witness.
        try:value=raw if order_authorizer is None else order_authorizer(scope)
        except Exception as error:
            raise PortfolioOrderAuthorizationError("Scoped order authorization failed: "+str(error),scope) from error
        if value is None:
            raise PortfolioOrderAuthorizationError(f"{scope.execution_date} {scope.symbol}: execution permission unknown; no OHLCV inference",scope)
        if type(value) is not bool:raise PortfolioOrderAuthorizationError("Scoped order authorizer must return bool or None",scope)
        return value

    def execute_open(self, execution_date: date, quotes: Mapping[str, OpenQuote], *,
                     order_authorizer: OrderAuthorizer | None = None) -> None:
        # Stage the actual sell-first sequence. An unknown permission aborts the
        # whole batch without partial cash/fills, but ignored or otherwise
        # impossible orders never need a permission flag. Simulating the real
        # sequence also handles sell-and-rebuy of the same symbol correctly.
        staged=self._staged()
        staged._execute_open_in_place(execution_date,quotes,order_authorizer)
        self._commit(staged)

    def _execute_open_in_place(self, execution_date: date, quotes: Mapping[str, OpenQuote],
                               order_authorizer: OrderAuthorizer | None) -> None:
        if execution_date not in self._session_indices:
            raise ValueError("Execution date is not in the explicit market calendar")
        session_index=self._session_indices[execution_date]
        if self._last_execution_date is not None and execution_date <= self._last_execution_date:
            raise ValueError("Market execution sessions must strictly increase")
        for decision in self._pending:
            if execution_date <= decision.signal_date:
                raise ValueError("A completed close signal requires a strictly later market open")
        self._last_execution_date = execution_date
        retry: list[PortfolioDecision] = []
        pending, self._pending = self._pending, []
        for decision in pending:
            if execution_date <= decision.signal_date:
                raise ValueError("A completed close signal requires a strictly later market open")
            retry_sells, retry_buys = [], []
            sequence = [(s, "sell", None) for s in decision.sell_symbols] + [(b.symbol, "buy", b) for b in decision.buys]
            for symbol, side, intent in sequence:
                reason, status = "", "rejected"
                quote = quotes.get(symbol)
                held = self.positions.get(symbol)
                if symbol not in self.config.lot_sizes:
                    raise ValueError(f"Missing lot-size contract: {symbol}")
                if side == "sell" and held is None:
                    status, reason = "ignored", "no_long_position"
                elif side == "buy" and held is not None:
                    status, reason = "ignored", "already_held_no_pyramiding"
                elif quote is None:
                    reason = "missing_execution_quote"
                elif side == "sell" and session_index - held.entry_session_index < self.config.settlement_market_sessions:
                    reason = "settlement_wait"
                elif side == "buy" and len(self.positions) >= self.config.max_positions:
                    reason = "portfolio_position_limit"
                else:
                    direction = Decimal(1) if side == "buy" else Decimal(-1)
                    price = quote.price * (1 + direction * self.config.slippage_bps / Decimal(10000))
                    if side == "buy":
                        if self.cash < intent.cash_budget and self.config.insufficient_cash == "reject":
                            reason = "shared_cash_below_planned_budget"
                            quantity = 0
                        else:
                            quantity = self._quantity(min(self.cash, intent.cash_budget), price, symbol)
                            if not quantity:
                                reason = "insufficient_cash_for_lot_and_fee"
                    else:
                        quantity = held.quantity
                    if quantity:
                        notional = money(price * quantity)
                        fee = self._fee(notional, side)
                        new_cash = money(self.cash - notional - fee if side == "buy" else self.cash + notional - fee)
                        if new_cash < 0:
                            reason = "fee_exceeds_shared_cash"
                        elif not self._allowed(quote.buy_allowed if side=="buy" else quote.sell_allowed,
                            PortfolioOrderScope(execution_date,symbol,"open",side,quantity,"shares",price,self.config.price_basis),order_authorizer):
                            reason="explicit_permission_denied" if order_authorizer is None else "scoped_or_explicit_permission_denied"
                        else:
                            self.cash = new_cash
                            if side == "buy":
                                self.positions[symbol] = HeldPosition(symbol, quantity, session_index, notional + fee)
                            else:
                                del self.positions[symbol]
                            self.fills.append(PortfolioFill(decision.signal_date, execution_date, symbol, side, quantity, price, notional, fee, self.cash, intent.reason if intent else decision.reason))
                            status, reason = "filled", "next_market_open"
                self.orders.append(PortfolioOrderEvent(decision.signal_date, execution_date, symbol, side, status, reason))
                if status == "rejected" and self.config.failed_order_policy == "retry_next_market_open":
                    if side == "sell": retry_sells.append(symbol)
                    else: retry_buys.append(intent)
            if retry_sells or retry_buys:
                retry.append(PortfolioDecision(decision.signal_date, tuple(retry_sells), tuple(retry_buys), decision.reason))
        self._pending = retry

    def equity(self, closes: Mapping[str, Decimal]) -> Decimal:
        missing = self.positions.keys() - closes.keys()
        if missing:
            raise ValueError(f"Missing explicit end valuation marks: {sorted(missing)}")
        for value in closes.values():
            decimal_nonnegative(value, "valuation mark")
            if value <= 0: raise ValueError("Valuation marks must be positive")
        return money(self.cash + sum((p.quantity * closes[s] for s, p in self.positions.items()), ZERO))

    def expire_pending_at_end(self) -> None:
        for decision in self._pending:
            for symbol,side in [(s,"sell") for s in decision.sell_symbols]+[(b.symbol,"buy") for b in decision.buys]:
                self.orders.append(PortfolioOrderEvent(decision.signal_date,None,symbol,side,"expired","no_subsequent_market_open"))
        self._pending=[]

    def terminal_close(self, session: date, quotes: Mapping[str, OpenQuote], *, respect_settlement: bool,
                       order_authorizer: OrderAuthorizer | None = None) -> None:
        """Only an explicitly requested terminal model can create these closes."""
        staged=self._staged()
        staged._terminal_close_in_place(session,quotes,respect_settlement,order_authorizer)
        self._commit(staged)

    def _terminal_close_in_place(self, session: date, quotes: Mapping[str, OpenQuote],
                                  respect_settlement: bool,order_authorizer: OrderAuthorizer | None) -> None:
        if type(respect_settlement) is not bool or session != self._last_execution_date:
            raise ValueError("Terminal close needs the completed final session and explicit settlement policy")
        for symbol in self.positions:
            if symbol not in quotes:
                raise ValueError("Explicit terminal close price required")
        for symbol,held in list(self.positions.items()):
            quote=quotes[symbol]
            reason=""
            if quote.sell_allowed is False:
                reason="terminal_sell_permission_denied"
            elif respect_settlement and self._session_indices[session]-held.entry_session_index<self.config.settlement_market_sessions:
                reason="terminal_settlement_wait"
            if reason:
                self.orders.append(PortfolioOrderEvent(session,session,symbol,"sell","rejected",reason))
                continue
            price=quote.price*(1-self.config.slippage_bps/Decimal(10000))
            notional=money(price*held.quantity)
            fee=self._fee(notional,"sell")
            if self.cash+notional-fee<0:
                self.orders.append(PortfolioOrderEvent(session,session,symbol,"sell","rejected","terminal_fee_exceeds_cash"))
                continue
            if not self._allowed(quote.sell_allowed,PortfolioOrderScope(session,symbol,"close","sell",held.quantity,"shares",price,self.config.price_basis),order_authorizer):
                self.orders.append(PortfolioOrderEvent(session,session,symbol,"sell","rejected","terminal_scoped_permission_denied"))
                continue
            self.cash=money(self.cash+notional-fee)
            del self.positions[symbol]
            self.fills.append(PortfolioFill(session,session,symbol,"sell",held.quantity,price,notional,fee,self.cash,"configured_terminal_close_not_natural_exit"))
            self.orders.append(PortfolioOrderEvent(session,session,symbol,"sell","filled","configured_terminal_close_not_natural_exit"))

    @property
    def pending_decisions(self) -> tuple[PortfolioDecision, ...]:
        return tuple(self._pending)


@dataclass(frozen=True,slots=True)
class PortfolioEquity:
    session: date
    cash: Decimal
    holdings: tuple[tuple[str,int],...]
    equity: Decimal


@dataclass(frozen=True,slots=True)
class PortfolioReplayResult:
    initial_cash: Decimal
    fills: tuple[PortfolioFill,...]
    orders: tuple[PortfolioOrderEvent,...]
    equity_curve: tuple[PortfolioEquity,...]
    terminal_policy: str
    remaining_positions: tuple[tuple[str,int],...]
    source_validation_status: str


@dataclass(frozen=True,slots=True)
class PortfolioReplaySnapshot:
    """Committed audit state at interruption, never a completed performance."""
    cash: Decimal
    fills: tuple[PortfolioFill,...]
    orders: tuple[PortfolioOrderEvent,...]
    equity_curve: tuple[PortfolioEquity,...]
    positions: tuple[tuple[str,HeldPosition],...]
    pending_decisions: tuple[PortfolioDecision,...]
    failed_session: date | None
    failed_phase: str
    source_validation_status: str


class PortfolioReplayInterrupted(ValueError):
    def __init__(self, message: str, snapshot: PortfolioReplaySnapshot,
                 failing_order_scope: PortfolioOrderScope | None):
        super().__init__(message)
        self.portfolio_snapshot=snapshot
        self.failing_order_scope=failing_order_scope


def run_shared_portfolio(
    config: PortfolioExecutionConfig,
    market_sessions: Sequence[date],
    open_quotes: Mapping[date,Mapping[str,OpenQuote]],
    closing_marks: Mapping[date,Mapping[str,Decimal]],
    strategy: Any,
    context_factory: Callable[[date,Decimal,tuple[str,...]],Any],
    *, source_validation_status: str,
    terminal_policy: str,
    terminal_quotes: Mapping[str,OpenQuote] | None,
    terminal_respects_settlement: bool,
    order_authorizer: OrderAuthorizer | None = None,
) -> PortfolioReplayResult:
    """Integrated close-selection/next-open replay with one cash/NAV curve.

    Context construction must bind the caller's prevalidated data and published
    calendar. Pending strategy rules reject before the first decision. No native
    parity or market return is implied by executing synthetic fixtures.
    """
    expected="synthetic_test" if config.data_kind=="synthetic" else "verified_historical_ready"
    if source_validation_status!=expected:
        raise ValueError("Formal shared replay requires verified input readiness, not pending data")
    if terminal_policy not in ("mark","force_close"):
        raise ValueError("Explicit terminal_policy=mark/force_close required")
    if config.max_positions!=strategy.max_positions:
        raise ValueError("Shared account position limit must match the selected strategy")
    strategy.rules.require_confirmed()
    if config.data_kind=="verified_historical" and strategy.rules.approval_status!="confirmed":
        raise ValueError("Synthetic test approval cannot authorize real-data portfolio results")
    sessions=tuple(market_sessions)
    account=SharedCashAccount(config,sessions)
    curve=[]
    phase="before_first_session"
    session=None
    try:
        for session in sessions:
            phase="open"
            account.execute_open(session,open_quotes.get(session,{}),order_authorizer=order_authorizer)
            phase="close_valuation_and_selection"
            marks=closing_marks.get(session,{})
            nav=account.equity(marks)
            held=tuple(sorted(account.positions))
            context=context_factory(session,nav,held)
            if context.session!=session or context.nav!=nav or set(context.held_symbols)!=set(held) or context.price_basis!=config.price_basis:
                raise ValueError("Close context must use the actual shared NAV, holdings, session and price basis")
            selection=strategy.decide(context)
            if selection.decision.signal_date!=session:
                raise ValueError("Strategy returned a decision for a different close")
            if not account.pending_decisions:
                account.submit(selection.decision)
            elif selection.decision.buys or selection.decision.sell_symbols:
                raise ValueError("Retry/new-signal conflict needs an explicit resolved execution convention")
            curve.append(PortfolioEquity(session,account.cash,tuple(sorted((s,p.quantity) for s,p in account.positions.items())),nav))
        phase="expire_pending_at_end"
        account.expire_pending_at_end()
        if terminal_policy=="force_close":
            phase="terminal_close"
            if terminal_quotes is None:
                raise ValueError("Forced terminal close requires explicit close-price quotes and permissions")
            account.terminal_close(sessions[-1],terminal_quotes,respect_settlement=terminal_respects_settlement,order_authorizer=order_authorizer)
            curve[-1]=PortfolioEquity(sessions[-1],account.cash,tuple(sorted((s,p.quantity) for s,p in account.positions.items())),account.equity(closing_marks.get(sessions[-1],{})))
    except Exception as error:
        snapshot=PortfolioReplaySnapshot(account.cash,tuple(account.fills),tuple(account.orders),tuple(curve),tuple(sorted(account.positions.items())),account.pending_decisions,session,phase,source_validation_status)
        raise PortfolioReplayInterrupted(str(error),snapshot,getattr(error,"order_scope",None)) from error
    return PortfolioReplayResult(config.initial_cash,tuple(account.fills),tuple(account.orders),tuple(curve),terminal_policy,tuple(sorted((s,p.quantity) for s,p in account.positions.items())),source_validation_status)
