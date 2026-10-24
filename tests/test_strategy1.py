"""Synthetic observations test execution rules; these are not market returns."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from agent_stocking.strategy1 import (
    DailyObservation,
    MissingProfitObservationError,
    Strategy1Config,
    Strategy1RuleError,
    calculate_strategy1_metrics,
    replay_strategy1,
)


def D(value: str | int) -> Decimal:
    return Decimal(value)


def observation(day: int, price: str = "10", profit: str = "50", **changes: object) -> DailyObservation:
    price_value = D(price)
    result = DailyObservation(
        date(2026, 1, 1) + timedelta(days=day),
        price_value, price_value + D("2"), price_value - D("2"), price_value,
        D(profit), False, source="synthetic execution-rule test",
    )
    return replace(result, **changes)


def one_price(day: int, price: str, **changes: object) -> DailyObservation:
    value = D(price)
    return observation(day, price, high=value, low=value, close_limit_up=True, **changes)


class Strategy1Tests(unittest.TestCase):
    def test_sparse_profit_is_opt_in_and_keeps_all_idle_daily_bars(self):
        bars = [observation(day, profit_pct=None) for day in range(5)]
        with self.assertRaisesRegex(ValueError, "sparse-profit mode is not enabled"):
            replay_strategy1(bars)
        result = replay_strategy1(bars, allow_sparse_profit=True)
        self.assertEqual(len(result.equity), 5)
        self.assertTrue(all(point.profit_pct is None for point in result.equity))
        self.assertEqual(result.fills, [])
        self.assertEqual(calculate_strategy1_metrics(result)["final_equity"], D("1000000"))

    def test_sparse_profit_requests_new_rocket_date_with_opportunity_details(self):
        bars = [observation(0, profit_pct=None), observation(1, rocket=True, profit_pct=None)]
        with self.assertRaises(MissingProfitObservationError) as failure:
            replay_strategy1(bars, allow_sparse_profit=True)
        error = failure.exception
        self.assertEqual(error.date, bars[1].date)
        self.assertEqual(error.phase, "close")
        self.assertEqual(error.held_quantity, 0)
        self.assertEqual(error.observing_opportunity_ids, (1,))
        self.assertIn(str(bars[1].date), str(error))

    def test_sparse_profit_requires_each_active_window_day_including_fourth(self):
        for missing_day in (1, 3):
            bars = [observation(day, profit="45", rocket=day == 0) for day in range(4)]
            bars[missing_day] = replace(bars[missing_day], profit_pct=None)
            with self.subTest(missing_day=missing_day), self.assertRaises(MissingProfitObservationError) as failure:
                replay_strategy1(bars, allow_sparse_profit=True)
            self.assertEqual(failure.exception.date, bars[missing_day].date)
        expired = replay_strategy1(
            [observation(day, profit="45", rocket=day == 0) for day in range(4)]
            + [observation(4, profit_pct=None)], allow_sparse_profit=True,
        )
        self.assertEqual(expired.opportunities[0].status, "expired")
        self.assertIsNone(expired.equity[-1].profit_pct)

    def test_sparse_profit_after_cancellation_or_exit_is_allowed_but_not_while_held(self):
        cancelled = replay_strategy1([
            observation(0, profit="39", rocket=True), observation(1, profit_pct=None),
        ], allow_sparse_profit=True)
        self.assertEqual(cancelled.opportunities[0].status, "cancelled")
        with self.assertRaises(MissingProfitObservationError) as failure:
            replay_strategy1([observation(0, rocket=True), observation(1, profit_pct=None)], allow_sparse_profit=True)
        self.assertEqual(failure.exception.held_quantity, 9999)
        exited = replay_strategy1([
            observation(0, rocket=True), observation(1, profit="39"), observation(2, profit_pct=None),
        ], allow_sparse_profit=True)
        self.assertEqual(exited.equity[-1].quantity, 0)
        self.assertEqual(len(exited.fills), 2)

    def test_sparse_pending_requires_close_p_even_on_third_blocked_one_price_day(self):
        bars = [one_price(0, "10", rocket=True), one_price(1, "12", open_buy_allowed=False),
                one_price(2, "14", open_buy_allowed=False, profit_pct=None)]
        with self.assertRaises(MissingProfitObservationError) as failure:
            replay_strategy1(bars, allow_sparse_profit=True)
        self.assertEqual(failure.exception.date, bars[2].date)
        self.assertEqual(failure.exception.pending_opportunity_ids, (1,))

    def test_sparse_open_fill_is_processed_before_missing_close_p_gate(self):
        bars = [observation(0, rocket=True, close_limit_up=True), observation(1, profit_pct=None)]
        with self.assertRaises(MissingProfitObservationError) as failure:
            replay_strategy1(bars, allow_sparse_profit=True)
        self.assertEqual(failure.exception.held_quantity, 9999)
        self.assertEqual(failure.exception.pending_opportunity_ids, ())

    def test_sparse_overlapping_second_window_still_needs_p_after_first_expires(self):
        bars = [observation(day, profit="44", rocket=day in (0, 1)) for day in range(4)]
        bars.append(observation(4, profit_pct=None))
        with self.assertRaises(MissingProfitObservationError) as failure:
            replay_strategy1(bars, allow_sparse_profit=True)
        self.assertEqual(failure.exception.observing_opportunity_ids, (2,))

    def test_sparse_omission_of_only_unneeded_p_preserves_fills_orders_and_metrics(self):
        bars = [observation(0), observation(1, profit="44", rocket=True),
                observation(2, profit="46"), observation(3, profit="39"), observation(4)]
        full = replay_strategy1(bars)
        sparse_bars = [replace(bar, profit_pct=None) if index in (0, 4) else bar for index, bar in enumerate(bars)]
        sparse = replay_strategy1(sparse_bars, allow_sparse_profit=True)
        self.assertEqual(full.fills, sparse.fills)
        self.assertEqual(full.orders, sparse.orders)
        self.assertEqual(calculate_strategy1_metrics(full), calculate_strategy1_metrics(sparse))
        self.assertEqual([(p.date, p.cash, p.quantity, p.close, p.equity) for p in full.equity],
                         [(p.date, p.cash, p.quantity, p.close, p.equity) for p in sparse.equity])

    def test_sparse_mode_does_not_relax_invalid_p_or_use_future_missing_p_early(self):
        for invalid in (D("NaN"), D("101"), 45.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replay_strategy1([observation(0, profit_pct=invalid)], allow_sparse_profit=True)
        prefix = [observation(0, profit_pct=None), observation(1, profit_pct=None)]
        self.assertEqual(replay_strategy1(prefix, allow_sparse_profit=True).fills, [])
        for future_price in ("9", "99"):
            with self.assertRaises(MissingProfitObservationError) as failure:
                replay_strategy1(prefix + [observation(2, future_price, rocket=True, profit_pct=None)], allow_sparse_profit=True)
            self.assertEqual(failure.exception.date, observation(2).date)

    def test_budget_includes_rounded_commission_and_step_is_one_share(self):
        result = replay_strategy1([observation(0, rocket=True)])
        fill = result.fills[0]
        self.assertEqual((fill.quantity, fill.notional, fill.commission), (9999, D("99990.00"), D("10.00")))
        self.assertEqual(fill.cash_after, D("900000.00"))
        self.assertEqual(fill.session, "close")
        self.assertEqual(fill.date, fill.signal_date)
        self.assertEqual(result.equity[-1].equity, D("999990.00"))

    def test_minimum_200_and_one_share_increment_with_minimum_fee(self):
        config = replace(Strategy1Config(), buy_budget=D("2015"))
        result = replay_strategy1([observation(0, rocket=True)], config)
        self.assertEqual((result.fills[0].quantity, result.fills[0].commission), (201, D("5.00")))
        too_small = replay_strategy1([observation(0, rocket=True)], replace(config, buy_budget=D("2004.99")))
        self.assertEqual(too_small.fills, [])
        self.assertEqual(too_small.opportunities[0].status, "rejected")
        self.assertTrue(any(order.event == "buy_rejected" for order in too_small.orders))

    def test_each_rocket_buys_once_and_new_rocket_can_add_a_position(self):
        result = replay_strategy1([
            observation(0, rocket=True), observation(1), observation(2, rocket=True), observation(3),
        ])
        self.assertEqual([fill.date for fill in result.fills], [date(2026, 1, 1), date(2026, 1, 3)])
        self.assertEqual([fill.opportunity_id for fill in result.fills], [1, 2])
        self.assertEqual(result.equity[-1].quantity, 19998)
        self.assertEqual(result.equity[-1].cash, D("800000.00"))

    def test_overlapping_rocket_windows_have_independent_budgets(self):
        result = replay_strategy1([
            observation(0, profit="44", rocket=True), observation(1, profit="44", rocket=True),
            observation(2, profit="46"),
        ])
        self.assertEqual(len(result.fills), 2)
        self.assertTrue(all(fill.date == date(2026, 1, 3) for fill in result.fills))
        self.assertEqual({fill.rocket_date for fill in result.fills}, {date(2026, 1, 1), date(2026, 1, 2)})

    def test_fourth_actual_bar_can_trigger_despite_calendar_gap(self):
        result = replay_strategy1([
            observation(0, profit="45", rocket=True), observation(1, profit="45"),
            observation(100, profit="45"), observation(101, profit="45.01"),
        ])
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].date, date(2026, 1, 1) + timedelta(days=101))

    def test_fifth_bar_cannot_revive_expired_window(self):
        result = replay_strategy1([
            observation(0, profit="45", rocket=True), observation(1, profit="45"),
            observation(2, profit="45"), observation(3, profit="45"), observation(4, profit="46"),
        ])
        self.assertEqual(result.fills, [])
        self.assertEqual(result.opportunities[0].status, "expired")
        self.assertEqual(next(order.date for order in result.orders if order.event == "buy_expired"), date(2026, 1, 4))

    def test_strict_40_and_45_boundaries_and_prebuy_cancellation(self):
        kept = replay_strategy1([
            observation(0, profit="40", rocket=True), observation(1, profit="45"), observation(2, profit="46"),
        ])
        self.assertEqual(kept.fills[0].date, date(2026, 1, 3))
        cancelled = replay_strategy1([
            observation(0, profit="44", rocket=True), observation(1, profit="39.99"), observation(2, profit="50"),
        ])
        self.assertEqual(cancelled.fills, [])
        self.assertEqual(cancelled.opportunities[0].status, "cancelled")
        held = replay_strategy1([observation(0, rocket=True), observation(1, profit="40")])
        self.assertEqual(len(held.fills), 1)
        self.assertEqual(held.equity[-1].quantity, 9999)

    def test_close_limit_up_defers_to_explicitly_permitted_open(self):
        result = replay_strategy1([
            observation(0, rocket=True, close_limit_up=True), observation(1, "12"),
        ])
        fill = result.fills[0]
        self.assertEqual((fill.date, fill.session, fill.price), (date(2026, 1, 2), "open", D("12")))
        self.assertEqual(fill.signal_date, date(2026, 1, 1))
        self.assertEqual(fill.quantity, 8332)

    def test_three_completed_one_price_up_days_include_trigger_day(self):
        result = replay_strategy1([
            one_price(0, "10", rocket=True), one_price(1, "12", open_buy_allowed=False),
            one_price(2, "14", open_buy_allowed=False), observation(3, "15"),
        ])
        self.assertEqual(result.fills, [])
        self.assertEqual(result.opportunities[0].status, "cancelled")
        cancel = next(order for order in result.orders if order.event == "buy_cancelled")
        self.assertEqual(cancel.date, date(2026, 1, 3))

    def test_open_does_not_look_ahead_to_current_full_day_ohlc(self):
        prefix = [one_price(0, "10", rocket=True), one_price(1, "12", open_buy_allowed=False)]
        # Explicit opening permission allows execution. The later full-day bar
        # cannot retroactively cancel an opening fill as a third one-price day.
        result = replay_strategy1(prefix + [one_price(2, "14", open_buy_allowed=True)])
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].session, "open")
        self.assertEqual(result.opportunities[0].status, "filled")

    def test_close_below_40_cancels_blocked_pending_before_next_open(self):
        result = replay_strategy1([
            observation(0, rocket=True, close_limit_up=True),
            observation(1, profit="39", open_buy_allowed=False), observation(2),
        ])
        self.assertEqual(result.fills, [])
        self.assertEqual(result.opportunities[0].status, "cancelled")

    def test_trigger_window_does_not_expire_an_already_pending_buy(self):
        result = replay_strategy1([
            observation(0, profit="44", rocket=True), observation(1, profit="44"),
            observation(2, profit="44"), observation(3, close_limit_up=True),
            observation(4, open_buy_allowed=False), observation(5, "12"),
        ])
        self.assertEqual(result.fills[0].date, date(2026, 1, 6))
        self.assertEqual(result.fills[0].signal_date, date(2026, 1, 4))

    def test_open_new_buy_survives_close_exit_while_old_lot_is_sold(self):
        result = replay_strategy1([
            observation(0, rocket=True), observation(1, "11", rocket=True, close_limit_up=True),
            observation(2, "12", profit="30"), observation(3, "12", profit="50"),
        ])
        self.assertEqual([(fill.side, fill.session) for fill in result.fills], [("buy", "close"), ("buy", "open"), ("sell", "close")])
        self.assertEqual(result.fills[2].quantity, result.fills[0].quantity)
        self.assertEqual(result.equity[-1].quantity, result.fills[1].quantity)
        self.assertEqual(len(result.closed_batches), 1)
        self.assertEqual(result.closed_batches[0].pnl, D("19976.00"))
        metrics = calculate_strategy1_metrics(result)
        self.assertEqual(metrics["realized_pnl"], D("19976.00"))
        self.assertEqual(metrics["unrealized_pnl"], D("-10.00"))
        self.assertEqual(metrics["final_equity"], D("1019966.00"))
        self.assertEqual(metrics["final_equity"] - metrics["initial_cash"], metrics["realized_pnl"] + metrics["unrealized_pnl"])

    def test_recheck_close_for_new_lot_instead_of_carrying_exit_order(self):
        result = replay_strategy1([
            observation(0, rocket=True, close_limit_up=True), observation(1, profit="30"),
            observation(2, profit="50"), observation(3, profit="39"),
        ])
        self.assertEqual([(fill.side, fill.date) for fill in result.fills], [("buy", date(2026, 1, 2)), ("sell", date(2026, 1, 4))])

    def test_combined_exit_allocates_costs_to_each_buy_without_overwriting(self):
        result = replay_strategy1([
            observation(0, rocket=True), observation(1, "20", rocket=True), observation(2, "15", profit="39"),
        ])
        self.assertEqual(len(result.closed_batches), 2)
        self.assertEqual([batch.buy_sequence for batch in result.closed_batches], [1, 2])
        self.assertEqual(sum((batch.exit_commission for batch in result.closed_batches), D(0)), result.fills[-1].commission)
        self.assertEqual(sum((batch.exit_notional for batch in result.closed_batches), D(0)), result.fills[-1].notional)
        metrics = calculate_strategy1_metrics(result)
        self.assertEqual(metrics["win_rate"], D("0.5"))
        self.assertEqual(metrics["unrealized_pnl"], D("0"))
        self.assertEqual(metrics["final_equity"] - metrics["initial_cash"], metrics["realized_pnl"])

    def test_cash_shortfall_and_blocked_sell_stop_instead_of_fabricating_rules(self):
        with self.assertRaisesRegex(Strategy1RuleError, "cash-shortfall"):
            replay_strategy1([observation(0, rocket=True), observation(1, rocket=True)], replace(Strategy1Config(), initial_cash=D("100000")))
        with self.assertRaisesRegex(Strategy1RuleError, "blocked-sell"):
            replay_strategy1([observation(0, rocket=True), observation(1, profit="39", sell_allowed=False)])

    def test_sell_minimum_commission_cannot_silently_create_negative_cash(self):
        with self.assertRaisesRegex(Strategy1RuleError, "fee-funding"):
            replay_strategy1([
                observation(0, rocket=True),
                observation(1, "0.0001", profit="39", low=D("0.0001")),
            ], replace(Strategy1Config(), initial_cash=D("100000")))

    def test_final_position_is_marked_without_fake_sale_and_drawdown_includes_initial_cash(self):
        result = replay_strategy1([observation(0, rocket=True)])
        metrics = calculate_strategy1_metrics(result)
        self.assertEqual(metrics["sell_count"], 0)
        self.assertEqual(metrics["closed_batch_count"], 0)
        self.assertIsNone(metrics["win_rate"])
        self.assertIsNone(metrics["annualized_return"])
        self.assertEqual(metrics["max_drawdown"], D("0.00001"))
        self.assertEqual(metrics["total_fees"], D("10"))
        self.assertIn("survivorship", str(metrics["signal_basis"]))

    def test_valid_future_changes_do_not_change_past_fills_or_equity(self):
        prefix = [observation(0, rocket=True, close_limit_up=True), observation(1, "12", profit="50")]
        first = replay_strategy1(prefix + [observation(2, "15", profit="39")])
        second = replay_strategy1(prefix + [observation(2, "8", profit="90", rocket=True)])
        self.assertEqual(first.fills[:1], second.fills[:1])
        self.assertEqual(first.equity[:2], second.equity[:2])
        # The opening fill also remains identical when that day's later close
        # profit changes; T+1 prevents the newly bought lot being sold at close.
        changed_close = replay_strategy1([prefix[0], replace(prefix[1], profit_pct=D("30"))])
        self.assertEqual(first.fills[0], changed_close.fills[0])

    def test_input_requires_exact_decimals_and_explicit_ordered_observations(self):
        invalid = [
            [], [observation(1), observation(0)], [observation(0), observation(0)],
            [observation(0, profit_pct=D("101"))], [observation(0, close=D("NaN"))],
            [observation(0, profit_pct=0.5)], [observation(0, open_buy_allowed=None)],
        ]
        for bars in invalid:
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                replay_strategy1(bars)


if __name__ == "__main__":
    unittest.main()
