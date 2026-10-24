from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from agent_stocking.engine import run_backtest
from agent_stocking.models import Action, Bar, EngineConfig, Signal


def D(value: str | int) -> Decimal:
    return Decimal(value)


def bar(day: int, price: str = "10", **changes: object) -> Bar:
    value = D(price)
    result = Bar(
        date=date(2026, 1, 1) + timedelta(days=day),
        open=value,
        high=value + D("2"),
        low=value - D("2"),
        close=value,
        volume=10000,
        buy_allowed=True,
        sell_allowed=True,
    )
    return replace(result, **changes)


def config(**changes: object) -> EngineConfig:
    result = EngineConfig(
        initial_cash=D("10000"),
        lot_size=100,
        commission_rate=D("0"),
        min_commission=D("0"),
        sell_tax_rate=D("0"),
        transfer_fee_rate=D("0"),
        slippage_bps=D("0"),
    )
    return replace(result, **changes)


class ScriptedStrategy:
    def __init__(self, *actions: Action):
        self.actions = actions
        self.seen = []

    def on_close(self, history, portfolio):
        self.seen.append((history, portfolio))
        index = len(history) - 1
        action = self.actions[index] if index < len(self.actions) else Action.HOLD
        return Signal(action, f"decision {index}")


class EngineTests(unittest.TestCase):
    def test_signal_uses_next_open_and_final_holdings_are_marked_to_close(self):
        bars = [bar(0, "10"), bar(1, "12", close=D("13")), bar(2, "15", close=D("16"))]
        strategy = ScriptedStrategy(Action.BUY, Action.HOLD, Action.SELL)
        result = run_backtest(bars, strategy, config())

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual((trade.signal_date, trade.date, trade.price), (bars[0].date, bars[1].date, D("12")))
        self.assertEqual(trade.quantity, 800)
        self.assertEqual(result.equity[0].quantity, 0)
        self.assertEqual(result.equity[-1].equity, D("13200.00"))
        self.assertEqual(result.equity[-1].quantity, 800)
        self.assertEqual(result.orders[-1].status, "expired")
        self.assertIsNone(result.orders[-1].execution_date)

    def test_strategy_receives_only_completed_immutable_prefix_and_current_portfolio(self):
        bars = [bar(0), bar(1), bar(2)]
        strategy = ScriptedStrategy(Action.BUY, Action.SELL)
        result = run_backtest(bars, strategy, config())

        self.assertEqual(len(strategy.seen), 3)
        for index, (history, _) in enumerate(strategy.seen):
            self.assertIsInstance(history, tuple)
            self.assertEqual(history, tuple(bars[: index + 1]))
        self.assertEqual([p.quantity for _, p in strategy.seen], [0, 1000, 0])
        self.assertEqual([t.date for t in result.trades], [bars[1].date, bars[2].date])

    def test_buy_sizing_fees_sell_only_tax_and_cash_are_exact(self):
        settings = config(
            commission_rate=D("0.0003"),
            min_commission=D("5"),
            sell_tax_rate=D("0.0005"),
            transfer_fee_rate=D("0.00001"),
        )
        result = run_backtest(
            [bar(0), bar(1), bar(2, "11")],
            ScriptedStrategy(Action.BUY, Action.SELL),
            settings,
        )
        buy, sell = result.trades
        self.assertEqual(buy.quantity, 900)
        self.assertEqual((buy.notional, buy.commission, buy.tax, buy.transfer_fee), (D("9000"), D("5"), D("0"), D("0.09")))
        self.assertEqual(buy.cash_after, D("994.91"))
        self.assertEqual((sell.notional, sell.commission, sell.tax, sell.transfer_fee), (D("9900"), D("5"), D("4.95"), D("0.10")))
        self.assertEqual(sell.cash_after, D("10884.86"))
        self.assertEqual(result.equity[-1].equity, sell.cash_after)

    def test_minimum_commission_is_included_before_whole_lot_purchase(self):
        bars = [bar(0), bar(1)]
        rejected = run_backtest(
            bars, ScriptedStrategy(Action.BUY), config(initial_cash=D("1004.99"), min_commission=D("5"))
        )
        self.assertEqual(rejected.trades, [])
        self.assertEqual(rejected.orders[0].status, "rejected")
        self.assertIn("one lot", rejected.orders[0].reason)

        filled = run_backtest(
            bars, ScriptedStrategy(Action.BUY), config(initial_cash=D("1005"), min_commission=D("5"))
        )
        self.assertEqual(filled.trades[0].quantity, 100)
        self.assertEqual(filled.trades[0].cash_after, D("0.00"))

    def test_sizing_uses_rounded_notional_at_sub_cent_slippage_boundary(self):
        result = run_backtest(
            [bar(0), bar(1)],
            ScriptedStrategy(Action.BUY),
            config(initial_cash=D("1000"), slippage_bps=D("0.01")),
        )
        self.assertEqual(result.trades[0].quantity, 100)
        self.assertEqual(result.trades[0].notional, D("1000.00"))
        self.assertEqual(result.trades[0].cash_after, D("0.00"))

    def test_strict_permissions_fail_before_strategy_is_called(self):
        strategy = ScriptedStrategy(Action.BUY)
        with self.assertRaisesRegex(ValueError, "explicit buy_allowed and sell_allowed"):
            run_backtest([bar(0), bar(1, buy_allowed=None)], strategy, config())
        self.assertEqual(strategy.seen, [])

    def test_missing_permissions_can_be_explicitly_assumed_but_false_still_blocks(self):
        result = run_backtest(
            [bar(0, buy_allowed=None, sell_allowed=None), bar(1, buy_allowed=False, sell_allowed=None)],
            ScriptedStrategy(Action.BUY),
            config(require_trade_permissions=False),
        )
        self.assertEqual(result.trades, [])
        self.assertIn("buy_allowed", result.orders[0].reason)

    def test_zero_volume_or_blocked_session_cancels_without_automatic_retry(self):
        for changes in ({"volume": 0}, {"buy_allowed": False}):
            with self.subTest(changes=changes):
                result = run_backtest(
                    [bar(0), bar(1, **changes), bar(2)],
                    ScriptedStrategy(Action.BUY),
                    config(),
                )
                self.assertEqual(result.trades, [])
                self.assertEqual(len(result.orders), 1)
                self.assertEqual(result.orders[0].status, "rejected")
                self.assertTrue(all(point.quantity == 0 for point in result.equity))

    def test_adverse_slippage_is_applied_and_never_clamped_to_bar_bounds(self):
        result = run_backtest(
            [bar(0), bar(1), bar(2)],
            ScriptedStrategy(Action.BUY, Action.SELL),
            config(slippage_bps=D("10")),
        )
        self.assertEqual([trade.price for trade in result.trades], [D("10.010"), D("9.990")])

        rejected = run_backtest(
            [bar(0), bar(1, high=D("10"))],
            ScriptedStrategy(Action.BUY),
            config(slippage_bps=D("10")),
        )
        self.assertEqual(rejected.trades, [])
        self.assertIn("outside", rejected.orders[0].reason)

    def test_no_pyramiding_or_short_sales(self):
        result = run_backtest(
            [bar(i) for i in range(6)],
            ScriptedStrategy(Action.SELL, Action.BUY, Action.BUY, Action.SELL, Action.SELL),
            config(),
        )
        self.assertEqual([trade.side for trade in result.trades], [Action.BUY, Action.SELL])
        self.assertEqual([event.status for event in result.orders], ["ignored", "filled", "ignored", "filled", "ignored"])

    def test_settlement_blocks_early_exit_and_requires_a_new_signal(self):
        result = run_backtest(
            [bar(i) for i in range(5)],
            ScriptedStrategy(Action.BUY, Action.SELL, Action.HOLD, Action.SELL),
            config(settlement_days=2),
        )
        self.assertEqual([trade.date for trade in result.trades], [date(2026, 1, 2), date(2026, 1, 5)])
        self.assertEqual(result.orders[1].status, "rejected")
        self.assertIn("settlement", result.orders[1].reason)

    def test_sell_fees_cannot_create_negative_cash(self):
        result = run_backtest(
            [bar(0), bar(1), bar(2, "0.01", low=D("0.01"))],
            ScriptedStrategy(Action.BUY, Action.SELL),
            config(initial_cash=D("1005"), min_commission=D("5")),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.orders[-1].status, "rejected")
        self.assertIn("pay transaction fees", result.orders[-1].reason)
        self.assertEqual(result.equity[-1].cash, D("0"))
        self.assertEqual(result.equity[-1].quantity, 100)

    def test_invalid_strategy_outputs_fail_closed(self):
        class InvalidStrategy:
            def __init__(self, value):
                self.value = value

            def on_close(self, history, portfolio):
                return self.value

        for invalid in (None, "buy", Signal("buy"), Signal(Action.BUY, None)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "execution stopped"):
                    run_backtest([bar(0), bar(1)], InvalidStrategy(invalid), config())


if __name__ == "__main__":
    unittest.main()
