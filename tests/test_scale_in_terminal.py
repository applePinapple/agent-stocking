"""SYNTHETIC terminal execution receipt checks, never market-return evidence."""
from datetime import date
from decimal import Decimal as D
import unittest
from agent_stocking.scale_in import ExecutionQuote,ScaleInFill,ScaleInObservation,ScaleInPolicy,ScaleInState,ScaleInTerminalFill


class ScaleInTerminalTests(unittest.TestCase):
    def held(self):
        state=ScaleInState(ScaleInPolicy(11,D("10000"),D("0.0005"),D("0.0015"),1,(D(".5"),D(".5")),False,"SYNTHETIC","first_signal_cash_including_buy_fees"))
        state.on_close(ScaleInObservation(date(2021,1,4),D(10),D(9),D(24),"synthetic","SYNTHETIC"))
        sized=state.size_at_execution(ExecutionQuote(date(2021,1,5),D(10),True,True,"synthetic","SYNTHETIC"))
        state.apply_fill(ScaleInFill(1,date(2021,1,5),sized.quantity,D(10),D(10)*sized.quantity*D(".0005"),True,"synthetic","SYNTHETIC"))
        state.on_close(ScaleInObservation(date(2021,1,5),D(11),D(9),D(30),"synthetic","SYNTHETIC"))
        fill=ScaleInTerminalFill(date(2021,1,5),state.quantity,D(11),D(11)*state.quantity*D(".0015"),True,"SYNTHETIC_terminal_receipt","SYNTHETIC")
        return state,fill

    def test_same_day_settlement_rejected_without_mutating_position(self):
        state,fill=self.held();before=state.snapshot()
        with self.assertRaisesRegex(ValueError,"settlement"):
            state.apply_terminal_fill(fill,allow_same_day_acquired_sale=False)
        self.assertEqual(before,state.snapshot())

    def test_explicit_terminal_receipt_closes_whole_position_once(self):
        state,fill=self.held();cash=state.cash
        state.apply_terminal_fill(fill,allow_same_day_acquired_sale=True)
        self.assertEqual(state.cash,cash+fill.quantity*fill.actual_price-fill.actual_fee)
        self.assertEqual(state.quantity,0);self.assertTrue(state.terminated)
        with self.assertRaises(ValueError):state.apply_terminal_fill(fill,allow_same_day_acquired_sale=True)
        with self.assertRaises(ValueError):
            state.on_close(ScaleInObservation(date(2021,1,6),D(11),D(9),D(20),"synthetic","SYNTHETIC"))

    def test_terminal_cannot_be_backdated_or_applied_with_pending_order(self):
        state,fill=self.held()
        state.on_close(ScaleInObservation(date(2021,1,6),D(10),D(9),D(19),"synthetic","SYNTHETIC"))
        self.assertIsNotNone(state.pending)
        with self.assertRaises(ValueError):state.apply_terminal_fill(fill,allow_same_day_acquired_sale=True)

    def test_permission_unknown_and_partial_terminal_receipts_rejected(self):
        from dataclasses import replace
        state,fill=self.held()
        for invalid in (replace(fill,permission=False),replace(fill,permission=None),replace(fill,quantity=1),replace(fill,actual_fee=D("-1"))):
            with self.assertRaises(ValueError):state.apply_terminal_fill(invalid,allow_same_day_acquired_sale=True)
        self.assertGreater(state.quantity,0)

    def test_fee_sizing_reduction_cannot_increase_quantity_or_advance_stage(self):
        state,fill=self.held()
        state.on_close(ScaleInObservation(date(2021,1,6),D(10),D(9),D(19),"synthetic","SYNTHETIC"))
        sized=state.size_at_execution(ExecutionQuote(date(2021,1,7),D(10),True,True,"synthetic","SYNTHETIC"))
        with self.assertRaises(ValueError):state.reduce_sized_buy_quantity(sized.quantity+1,reason="synthetic")
        with self.assertRaises(ValueError):state.reduce_sized_buy_quantity(0,reason="synthetic")
        stage=state.stage
        reduced=state.reduce_sized_buy_quantity(sized.quantity-1,reason="synthetic explicit fee sizing")
        self.assertEqual(reduced.quantity,sized.quantity-1);self.assertEqual(state.stage,stage)


if __name__=="__main__":unittest.main()
