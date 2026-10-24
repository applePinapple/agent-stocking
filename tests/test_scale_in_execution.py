"""SYNTHETIC S11/S13 execution-state tests; never market-return evidence."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import unittest

from agent_stocking.scale_in import (
    ExecutionQuote, ScaleInFill, ScaleInObservation, ScaleInPolicy, ScaleInState,
)

D = Decimal
ORIGIN = date(2021, 1, 1)
BASIS = "synthetic_execution_test_only"


def policy(sid, **changes):
    result = ScaleInPolicy(sid=sid, initial_cash=D("10000"), buy_fee_rate=D("0.0005"),
                           sell_fee_rate=D("0.0015"), quantity_unit=1,
                           weights=(D("0.5"), D("0.5")) if sid == 11 else tuple(map(D, ["0.1", "0.2", "0.3", "0.4"])),
                           add_requires_above_ma=sid == 13, price_basis=BASIS,
                           capital_basis="first_signal_cash_including_buy_fees")
    return replace(result, **changes)


def observation(day, close="10", ma="9", rsi="20", **changes):
    result = ScaleInObservation(ORIGIN + timedelta(days=day), D(close), D(ma) if ma is not None else None,
                                D(rsi) if rsi is not None else None,
                                "synthetic_completed_daily_indicator", BASIS)
    return replace(result, **changes)


def execute(state, day, price, *, permission=True):
    quote = ExecutionQuote(ORIGIN + timedelta(days=day), D(price), permission, permission,
                            "synthetic_actual_open_quote", BASIS)
    sized = state.size_at_execution(quote)
    if sized is None:
        return None
    rate = state.policy.buy_fee_rate if sized.request.side == "buy" else state.policy.sell_fee_rate
    fill = ScaleInFill(sized.request.order_id, quote.date, sized.quantity, D(price),
                       D(price) * sized.quantity * rate, True, "synthetic_complete_execution_receipt", BASIS)
    state.apply_fill(fill)
    return fill


class ScaleInExecutionTests(unittest.TestCase):
    def test_s11_two_equal_frozen_budgets_no_third_entry(self):
        state = ScaleInState(policy(11))
        first = state.on_close(observation(0, rsi="15"))
        self.assertEqual((state.stage, state.quantity), (0, 0))
        self.assertEqual(first.gross_cash_budget, D("5000"))
        execute(state, 1, "12")
        second = state.on_close(observation(1, close="9", ma="10", rsi="19"))
        self.assertEqual(second.gross_cash_budget, D("5000"))
        self.assertEqual(second.reference_actual_fill_price, D("12"))
        self.assertEqual(state.stage, 1)
        execute(state, 2, "9")
        self.assertIsNone(state.on_close(observation(2, close="8", ma="10", rsi="1")))
        self.assertEqual(state.stage, 2)
        self.assertGreaterEqual(state.cash, 0)

    def test_s11_add_filter_is_explicit_configuration(self):
        state = ScaleInState(policy(11, add_requires_above_ma=True))
        state.on_close(observation(0, rsi="15"))
        execute(state, 1, "12")
        self.assertIsNone(state.on_close(observation(1, close="9", ma="10", rsi="19")))

    def test_s11_exact_exit_boundary_and_whole_position(self):
        state = ScaleInState(policy(11))
        state.on_close(observation(0, rsi="24"))
        execute(state, 1, "10")
        self.assertIsNone(state.on_close(observation(1, rsi="55")))
        sell = state.on_close(observation(2, rsi="55.01"))
        self.assertEqual(sell.requested_sell_quantity, state.quantity)
        quantity = state.quantity
        receipt = execute(state, 3, "11")
        self.assertEqual(receipt.quantity, quantity)
        self.assertEqual((state.stage, state.quantity, state.cycle_cash), (0, 0, None))

    def test_s13_uses_prior_actual_fill_not_signal_close(self):
        state = ScaleInState(policy(13))
        self.assertIsNone(state.on_close(observation(0, close="10", rsi="24")))
        state.on_close(observation(1, close="10", rsi="24"))
        execute(state, 2, "12")
        add = state.on_close(observation(2, close="11", ma="5", rsi="20"))
        self.assertIsNotNone(add)  # 11 < actual12, even though 11 > signal10.
        self.assertEqual(add.reference_actual_fill_price, D("12"))
        self.assertEqual(add.gross_cash_budget, D("2000"))

    def test_s13_four_exact_cycle_budgets_pause_resume_then_exit(self):
        state = ScaleInState(policy(13))
        state.on_close(observation(0, rsi="24"))
        first = state.on_close(observation(1, rsi="24"))
        self.assertEqual(first.gross_cash_budget, D("1000"))
        execute(state, 2, "12")
        self.assertIsNone(state.on_close(observation(2, close="11", ma="11", rsi="20")))
        self.assertEqual((state.stage, state.quantity > 0), (1, True))
        second = state.on_close(observation(3, close="11", ma="10", rsi="20"))
        self.assertEqual(second.gross_cash_budget, D("2000"))
        execute(state, 4, "11")
        third = state.on_close(observation(4, close="10.5", ma="10", rsi="50"))
        self.assertEqual(third.gross_cash_budget, D("3000"))
        execute(state, 5, "10.5")
        fourth = state.on_close(observation(5, close="10", ma="9", rsi="50"))
        self.assertEqual(fourth.gross_cash_budget, D("4000"))
        execute(state, 6, "10")
        self.assertEqual(state.stage, 4)
        self.assertIsNone(state.on_close(observation(6, rsi="70")))
        exit_order = state.on_close(observation(7, rsi="70.01"))
        self.assertEqual(exit_order.requested_sell_quantity, state.quantity)
        execute(state, 8, "13")
        next_cycle_cash = state.cash
        state.on_close(observation(8, rsi="20"))
        next_order = state.on_close(observation(9, rsi="20"))
        self.assertEqual(next_order.gross_cash_budget, next_cycle_cash * D("0.1"))
        self.assertEqual(state.cycle, 2)

    def test_reject_does_not_advance_stage_or_turn_signal_price_into_fill(self):
        state = ScaleInState(policy(11))
        first = state.on_close(observation(0, rsi="15"))
        state.reject(first.order_id, date=ORIGIN + timedelta(days=1), reason="synthetic_reject")
        self.assertEqual((state.stage, state.cash, state.last_tranche_fill_price), (0, D("10000"), None))
        retry = state.on_close(observation(1, rsi="15"))
        self.assertEqual(retry.stage, 1)
        self.assertEqual(retry.gross_cash_budget, D("5000"))

    def test_pending_without_fill_never_advances_and_never_pyramids_on_signals(self):
        state = ScaleInState(policy(11))
        state.on_close(observation(0, rsi="1"))
        self.assertIsNone(state.on_close(observation(1, rsi="1")))
        self.assertIsNone(state.on_close(observation(2, rsi="1")))
        self.assertEqual((state.stage, state.quantity, state.cash), (0, 0, D("10000")))

    def test_unknown_or_false_permission_never_creates_fill(self):
        for permission in [None, False]:
            with self.subTest(permission=permission):
                state = ScaleInState(policy(11))
                state.on_close(observation(0, rsi="1"))
                self.assertIsNone(execute(state, 1, "10", permission=permission))
                self.assertEqual((state.stage, state.quantity), (0, 0))
                self.assertIsNotNone(state.pending)

    def test_fee_included_budget_and_quantity_unit(self):
        state = ScaleInState(policy(11, quantity_unit=100))
        state.on_close(observation(0, rsi="1"))
        receipt = execute(state, 1, "12")
        self.assertEqual(receipt.quantity % 100, 0)
        cost = receipt.actual_price * receipt.quantity + receipt.actual_fee
        self.assertLessEqual(cost, D("5000"))
        self.assertEqual(state.cash, D("10000") - cost)

    def test_oversized_actual_fee_fails_without_mutating_holdings(self):
        state = ScaleInState(policy(11))
        order = state.on_close(observation(0, rsi="1"))
        q = ExecutionQuote(ORIGIN + timedelta(days=1), D("10"), True, True, "synthetic", BASIS)
        sized = state.size_at_execution(q)
        before = state.snapshot()
        fill = ScaleInFill(order.order_id, q.date, sized.quantity, D("10"), D("1000"), True, "synthetic", BASIS)
        with self.assertRaisesRegex(ValueError, "exceed"):
            state.apply_fill(fill)
        self.assertEqual(before, state.snapshot())

    def test_inconsistent_basis_partial_fill_and_unfinished_data_fail_explicitly(self):
        state = ScaleInState(policy(11))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            state.on_close(observation(0, complete=False))
        order = state.on_close(observation(0, rsi="1"))
        q = ExecutionQuote(ORIGIN + timedelta(days=1), D("10"), True, True, "synthetic", BASIS)
        with self.assertRaisesRegex(ValueError, "basis"):
            state.size_at_execution(replace(q, price_basis="a_different_adjustment_basis"))
        sized = state.size_at_execution(q)
        fill = ScaleInFill(order.order_id, q.date, sized.quantity-1, D("10"), D("1"), True, "synthetic", BASIS)
        with self.assertRaisesRegex(ValueError, "complete fills"):
            state.apply_fill(fill)

    def test_same_day_and_late_backdated_execution_rejected(self):
        state = ScaleInState(policy(11))
        order = state.on_close(observation(0, rsi="1"))
        q = ExecutionQuote(ORIGIN, D("10"), True, True, "synthetic", BASIS)
        with self.assertRaisesRegex(ValueError, "Next-open"):
            state.size_at_execution(q)
        q = replace(q, date=ORIGIN + timedelta(days=1))
        sized = state.size_at_execution(q)
        state.on_close(observation(1, rsi="1"))
        fill = ScaleInFill(order.order_id, q.date, sized.quantity, D("10"), D("1"), True, "synthetic", BASIS)
        with self.assertRaisesRegex(ValueError, "backdated"):
            state.apply_fill(fill)

    def test_future_observations_and_execution_quotes_do_not_change_prior_states(self):
        def scenario(sid, rows):
            state = ScaleInState(policy(sid))
            snapshots = []
            for day, close, rsi, opening in rows:
                if state.pending:
                    execute(state, day, opening)
                state.on_close(observation(day, close=close, ma="6", rsi=rsi))
                snapshots.append(state.snapshot())
            return snapshots
        rows=[]
        for i in range(80):
            close = D("13.5") - D(i % 8) * D("0.6")
            rsi = ["22", "21", "20", "19", "18", "17", "76", "40"][i % 8]
            rows.append((i, str(close), rsi, str(D("14")-D(i%8)*D("0.6"))))
        for sid in [11,13]:
            original = scenario(sid, rows)
            self.assertTrue(any(x["stage"]>0 for x in original))
            for cut in [15,30,60]:
                changed = rows[:cut] + [(i, "1000", "5", "1200") for i,_,_,_ in rows[cut:]]
                with self.subTest(sid=sid,cut=cut):
                    self.assertEqual(original[:cut], scenario(sid,changed)[:cut])
                    self.assertEqual(original[:cut], scenario(sid,rows[:cut]))

    def test_policy_requires_explicit_non_substitutable_weights(self):
        with self.assertRaisesRegex(ValueError, "weights"):
            policy(13, weights=(D("0.25"),)*4)
        with self.assertRaisesRegex(ValueError, "cannot add"):
            policy(13, add_requires_above_ma=False)


if __name__ == "__main__":
    unittest.main()
