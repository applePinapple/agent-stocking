"""Synthetic regression cases for execution state invalidation, not returns."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import unittest

from agent_stocking.scale_in import ExecutionQuote, ScaleInFill, ScaleInState
from tests.test_scale_in_execution import BASIS, ORIGIN, observation, policy


class ScaleInReviewTests(unittest.TestCase):
    def test_unaffordable_requote_invalidates_previous_sizing(self):
        state = ScaleInState(policy(11))
        order = state.on_close(observation(0, rsi="1"))
        quote = ExecutionQuote(ORIGIN + timedelta(days=1), Decimal("10"),
                               True, True, "synthetic_quote", BASIS)
        sized = state.size_at_execution(quote)
        self.assertIsNotNone(sized)

        # The latest actual quote cannot afford even one share. An earlier
        # positive sizing must no longer authorize a fill at the old quote.
        self.assertIsNone(state.size_at_execution(replace(quote, price=Decimal("10000"))))
        stale_fill = ScaleInFill(order.order_id, quote.date, sized.quantity,
                                 quote.price, Decimal("1"), True,
                                 "synthetic_stale_receipt", BASIS)
        with self.assertRaises(ValueError):
            state.apply_fill(stale_fill)
        self.assertEqual((state.cash, state.quantity, state.stage),
                         (Decimal("10000"), 0, 0))
        self.assertIsNotNone(state.pending)


if __name__ == "__main__":
    unittest.main()
