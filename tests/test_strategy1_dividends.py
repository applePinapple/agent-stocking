"""Synthetic cash-dividend timing/accounting risks; never market-return evidence."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from agent_stocking.strategy1 import (
    CashDividend,
    DailyObservation,
    Strategy1Config,
    Strategy1RuleError,
    calculate_strategy1_metrics,
    replay_strategy1,
)


D = Decimal
START = date(2026, 1, 1)
CONFIG = replace(Strategy1Config(), initial_cash=D("100000"), buy_budget=D("20105"))
SOURCE = "synthetic dividend timing and accounting test; not a real distribution"


def day(offset: int) -> date:
    return START + timedelta(days=offset)


def bar(offset: int, price: str = "100", profit: str = "50", **changes: object) -> DailyObservation:
    value = D(price)
    return replace(DailyObservation(
        day(offset), value, value + D("1"), value - D("1"), value,
        D(profit), False, source=SOURCE,
    ), **changes)


def dividend(record: int = 0, ex: int = 1, payment: int = 3,
             per_share: str = "1", event_id: str = "synthetic-dividend") -> CashDividend:
    return CashDividend(event_id, day(record), day(ex), day(payment), D(per_share), SOURCE)


class Strategy1DividendTests(unittest.TestCase):
    def assert_reconciles(self, result) -> None:
        for point in result.equity:
            self.assertEqual(point.market_value, point.quantity * point.close)
            self.assertEqual(point.equity, point.cash + point.market_value + point.dividend_receivable)
        metrics = calculate_strategy1_metrics(result)
        self.assertEqual(metrics["final_equity"] - metrics["initial_cash"],
                         metrics["realized_pnl"] + metrics["unrealized_pnl"])
        self.assertEqual(metrics["ending_market_value"], result.equity[-1].market_value)

    def test_record_close_purchase_is_eligible_after_same_close_fill(self):
        result = replay_strategy1([bar(0, rocket=True), bar(1)], CONFIG,
                                  cash_dividends=[dividend(payment=2)])
        entitlement, = result.dividend_entitlements
        self.assertEqual((entitlement.buy_sequence, entitlement.quantity, entitlement.amount),
                         (1, 201, D("201.00")))
        record, = [r for r in result.dividend_ledger if r.phase == "record_date_after_close"]
        self.assertEqual(record.cash_after, D("79895.00"))
        self.assertEqual(record.receivable_after, D("0.00"))
        self.assert_reconciles(result)

    def test_registration_excludes_old_shares_sold_but_keeps_same_day_deferred_buy(self):
        result = replay_strategy1([
            bar(0, rocket=True), bar(1, rocket=True, close_limit_up=True),
            bar(2, profit="39"), bar(3), bar(4),
        ], CONFIG, cash_dividends=[dividend(record=2, ex=3, payment=4)])
        self.assertEqual([(f.side, f.session, f.quantity) for f in result.fills],
                         [("buy", "close", 201), ("buy", "open", 201), ("sell", "close", 201)])
        entitlement, = result.dividend_entitlements
        self.assertEqual((entitlement.buy_sequence, entitlement.quantity), (2, 201))
        self.assertEqual(result.closed_batches[0].dividend_income, D("0.00"))
        self.assertEqual(result.ending_lots[0].dividend_income, D("201.00"))
        self.assert_reconciles(result)

    def test_ex_receivable_offsets_ex_price_and_payment_only_transfers_assets(self):
        bars = [bar(0, rocket=True), bar(1), bar(2, "99"), bar(3, "99"), bar(4, "99")]
        event = dividend(ex=2, payment=4)
        result = replay_strategy1(bars, CONFIG, cash_dividends=[event])
        self.assertEqual([p.dividend_receivable for p in result.equity],
                         [D("0"), D("0"), D("201"), D("201"), D("0")])
        self.assertEqual([p.cash for p in result.equity], [D("79895")] * 4 + [D("80096")])
        self.assertEqual([p.equity for p in result.equity], [D("99995")] * 5)
        self.assertEqual([(r.phase, r.date) for r in result.dividend_ledger], [
            ("record_date_after_close", day(0)),
            ("ex_date_receivable_before_open", day(2)),
            ("cash_payment_before_open", day(4)),
        ])
        before_ex = replay_strategy1(bars[:2], CONFIG, cash_dividends=[event])
        self.assertFalse(before_ex.dividend_entitlements[0].recognized)
        self.assertFalse(before_ex.dividend_entitlements[0].paid)
        self.assertEqual(before_ex.ending_lots[0].dividend_income, D("0"))
        self.assertEqual(calculate_strategy1_metrics(result)["max_drawdown"], D("0.00005"))
        self.assert_reconciles(result)

    def test_unpaid_receivable_cannot_fund_buy_payment_before_open_can(self):
        cash_limited = replace(CONFIG, initial_cash=D("40209"))
        bars = [bar(0, rocket=True), bar(1, rocket=True, close_limit_up=True), bar(2)]
        unpaid = dividend(ex=1, payment=3)
        prefix = replay_strategy1(bars[:2], cash_limited, cash_dividends=[unpaid])
        self.assertEqual((prefix.equity[-1].cash, prefix.equity[-1].dividend_receivable),
                         (D("20104"), D("201")))
        with self.assertRaisesRegex(Strategy1RuleError, "cash-shortfall"):
            replay_strategy1(bars, cash_limited, cash_dividends=[unpaid])
        paid = replay_strategy1(bars, cash_limited,
                                cash_dividends=[replace(unpaid, payment_date=day(2))])
        self.assertEqual((paid.fills[-1].session, paid.fills[-1].cash_after), ("open", D("200")))
        payment, = [r for r in paid.dividend_ledger if r.phase == "cash_payment_before_open"]
        self.assertEqual((payment.date, payment.processed_on, payment.cash_after),
                         (day(2), day(2), D("20305")))
        self.assert_reconciles(paid)

    def test_entitlement_survives_sale_before_ex_or_before_payment(self):
        for sale_day in (1, 3):
            with self.subTest(sale_day=sale_day):
                bars = [bar(i, profit="39" if i == sale_day else "50", rocket=i == 0)
                        for i in range(5)]
                result = replay_strategy1(bars, CONFIG,
                                          cash_dividends=[dividend(ex=2, payment=4)])
                self.assertEqual(result.equity[-1].quantity, 0)
                self.assertEqual(result.equity[-1].cash, D("100191"))
                old, = result.closed_batches
                self.assertEqual((old.buy_sequence, old.dividend_income, old.dividend_cash_received),
                                 (1, D("201"), D("201")))
                self.assertEqual(old.pnl, D("191"))
                self.assertEqual(len([r for r in result.dividend_ledger
                                      if r.phase == "cash_payment_before_open"]), 1)
                self.assert_reconciles(result)

    def test_ex_and_payment_date_new_purchases_do_not_inherit_old_entitlement(self):
        for payment_session in ("close", "open"):
            with self.subTest(payment_session=payment_session):
                bars = [bar(0, rocket=True), bar(1, profit="39"), bar(2, rocket=True),
                        bar(3, rocket=payment_session == "open", close_limit_up=payment_session == "open"),
                        bar(4, rocket=payment_session == "close")]
                result = replay_strategy1(bars, CONFIG,
                                          cash_dividends=[dividend(ex=2, payment=4)])
                self.assertEqual(len(result.dividend_entitlements), 1)
                self.assertEqual(result.dividend_entitlements[0].buy_sequence, 1)
                self.assertEqual(result.closed_batches[0].dividend_cash_received, D("201"))
                self.assertEqual(len(result.ending_lots), 2)
                self.assertTrue(all(lot.dividend_income == lot.dividend_cash_received == 0
                                    for lot in result.ending_lots))
                self.assertEqual(result.fills[-1].session, payment_session)
                self.assert_reconciles(result)

    def test_no_registration_holdings_cannot_gain_dividend_by_buying_on_ex_date(self):
        result = replay_strategy1([bar(0, profit_pct=None), bar(1, rocket=True), bar(2)], CONFIG,
                                  allow_sparse_profit=True, cash_dividends=[dividend(payment=2)])
        self.assertEqual(result.dividend_entitlements, [])
        self.assertTrue(all(row.amount == 0 for row in result.dividend_ledger))
        self.assertEqual(result.ending_lots[0].dividend_income, D("0"))
        self.assertEqual(result.equity[-1].cash, D("79895"))
        self.assert_reconciles(result)

    def test_rounding_by_batch_and_combined_sell_commission_preserve_every_cent(self):
        bars = [bar(i, rocket=i < 3, profit="39" if i == 4 else "50") for i in range(6)]
        result = replay_strategy1(bars, CONFIG,
                                  cash_dividends=[dividend(record=2, ex=3, payment=5, per_share="0.015")])
        self.assertEqual([e.quantity for e in result.dividend_entitlements], [201, 201, 201])
        self.assertEqual([e.amount for e in result.dividend_entitlements],
                         [D("3.02"), D("3.01"), D("3.02")])
        self.assertEqual(sum((e.amount for e in result.dividend_entitlements), D(0)), D("9.05"))
        sale, = [f for f in result.fills if f.side == "sell"]
        self.assertEqual((sale.quantity, sale.notional, sale.commission), (603, D("60300"), D("6.03")))
        self.assertEqual(sum((b.exit_commission for b in result.closed_batches), D(0)), sale.commission)
        self.assertEqual(sum((b.exit_notional for b in result.closed_batches), D(0)), sale.notional)
        self.assertEqual([b.entry_cost for b in result.closed_batches], [D("20105")] * 3)
        self.assertEqual([b.pnl for b in result.closed_batches], [D("-3.99"), D("-4.00"), D("-3.99")])
        self.assertEqual(result.equity[-1].cash, D("99988.02"))
        self.assertEqual(calculate_strategy1_metrics(result)["total_fees"], D("21.03"))
        self.assert_reconciles(result)

    def test_ending_unpaid_receivable_is_valued_after_exit_without_fabricated_payment(self):
        bars = [bar(0, rocket=True), bar(1, "99"), bar(2, "99", "39"), bar(3, "99")]
        event = dividend(ex=1, payment=5)
        unpaid = replay_strategy1(bars, CONFIG, cash_dividends=[event])
        ending = unpaid.equity[-1]
        self.assertEqual((ending.cash, ending.market_value, ending.dividend_receivable, ending.equity),
                         (D("99789"), D("0"), D("201"), D("99990")))
        metrics = calculate_strategy1_metrics(unpaid)
        self.assertEqual((metrics["realized_pnl"], metrics["unrealized_pnl"]), (D("-10"), D("0")))
        self.assertEqual(metrics["dividend_cash_received"], D("0"))
        self.assertEqual(metrics["ending_dividend_receivable"], D("201"))
        self.assertFalse(unpaid.dividend_entitlements[0].paid)
        self.assertTrue(unpaid.dividend_entitlements[0].recognized)
        self.assertFalse(any(r.phase == "cash_payment_before_open" for r in unpaid.dividend_ledger))
        paid = replay_strategy1(bars + [bar(5, "99")], CONFIG, cash_dividends=[event])
        self.assertEqual(paid.equity[-1].equity, ending.equity)
        self.assertEqual((paid.equity[-1].cash, paid.equity[-1].dividend_receivable), (D("99990"), D("0")))
        self.assertEqual(paid.closed_batches[0].pnl, unpaid.closed_batches[0].pnl)
        self.assert_reconciles(unpaid)
        self.assert_reconciles(paid)

    def test_gap_events_use_calendar_order_and_retain_original_event_dates(self):
        later = dividend(record=0, ex=4, payment=5, per_share="1", event_id="later")
        earlier = dividend(record=1, ex=2, payment=3, per_share="2", event_id="earlier")
        bars = [bar(0, rocket=True), bar(1), bar(6)]
        for events in ([later, earlier], [earlier, later]):
            with self.subTest(input_order=[e.event_id for e in events]):
                result = replay_strategy1(bars, CONFIG, cash_dividends=events)
                processed = [r for r in result.dividend_ledger if r.phase != "record_date_after_close"]
                self.assertEqual([(r.event_id, r.date, r.processed_on, r.phase) for r in processed], [
                    ("earlier", day(2), day(6), "ex_date_receivable_before_open"),
                    ("earlier", day(3), day(6), "cash_payment_before_open"),
                    ("later", day(4), day(6), "ex_date_receivable_before_open"),
                    ("later", day(5), day(6), "cash_payment_before_open"),
                ])
                self.assertEqual(result.equity[-1].cash, D("80498"))
                self.assert_reconciles(result)

    def test_same_day_ex_and_payment_recognize_before_single_cash_credit(self):
        result = replay_strategy1([bar(0, rocket=True), bar(1), bar(2)], CONFIG,
                                  cash_dividends=[dividend(ex=1, payment=1)])
        events = [r for r in result.dividend_ledger if r.date == day(1)]
        self.assertEqual([r.phase for r in events],
                         ["ex_date_receivable_before_open", "cash_payment_before_open"])
        self.assertEqual([(r.cash_after, r.receivable_after) for r in events],
                         [(D("79895"), D("201")), (D("80096"), D("0"))])
        self.assertEqual(result.equity[1].cash, result.equity[2].cash)
        self.assert_reconciles(result)

    def test_future_market_changes_do_not_rewrite_past_registration_or_equity(self):
        prefix_bars = [bar(0, rocket=True), bar(1, "99"), bar(2, "99")]
        event = dividend(ex=1, payment=3)
        prefix = replay_strategy1(prefix_bars, CONFIG, cash_dividends=[event])
        for future in (bar(3, "50", "39", rocket=True), bar(3, "200", "70", rocket=True)):
            with self.subTest(future_price=future.close):
                full = replay_strategy1(prefix_bars + [future], CONFIG, cash_dividends=[event])
                self.assertEqual(full.equity[:3], prefix.equity)
                self.assertEqual([f for f in full.fills if f.date <= day(2)], prefix.fills)
                self.assertEqual([r for r in full.dividend_ledger if r.date <= day(2)], prefix.dividend_ledger)
                self.assertEqual([(e.buy_sequence, e.quantity, e.amount) for e in full.dividend_entitlements],
                                 [(e.buy_sequence, e.quantity, e.amount) for e in prefix.dividend_entitlements])
                self.assert_reconciles(full)

    def test_default_and_explicit_empty_events_preserve_no_dividend_trade_result(self):
        bars = [bar(0, rocket=True), bar(1, profit="39")]
        default = replay_strategy1(bars, CONFIG)
        explicit = replay_strategy1(bars, CONFIG, cash_dividends=())
        self.assertEqual(default, explicit)
        self.assertEqual(default.dividend_entitlements, [])
        self.assertEqual(default.dividend_ledger, [])
        self.assertEqual(default.equity[-1].equity, D("99990"))
        metrics = calculate_strategy1_metrics(default)
        self.assertEqual((metrics["dividend_income"], metrics["dividend_cash_received"]), (D("0"), D("0")))
        self.assertEqual(metrics["total_fees"], D("10"))
        self.assert_reconciles(default)

    def test_invalid_events_and_duplicate_entitlements_are_rejected(self):
        valid = dividend()
        for changes in (
            {"event_id": ""}, {"source": " "}, {"record_date": datetime(2026, 1, 1)},
            {"ex_date": day(0)}, {"payment_date": day(0)}, {"cash_per_share": 1.0},
            {"cash_per_share": D("0")}, {"cash_per_share": D("-1")},
            {"cash_per_share": D("NaN")}, {"cash_per_share": D("Infinity")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(valid, **changes)
        bars = [bar(0, rocket=True), bar(1), bar(2), bar(3)]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            replay_strategy1(bars, CONFIG, cash_dividends=[valid, replace(valid, event_id="other")])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            replay_strategy1(bars, CONFIG, cash_dividends=[valid, replace(valid, record_date=day(1), ex_date=day(2))])
        with self.assertRaisesRegex(ValueError, "explicit CashDividend"):
            replay_strategy1(bars, CONFIG, cash_dividends=[object()])
        with self.assertRaisesRegex(Strategy1RuleError, "predates"):
            replay_strategy1(bars, CONFIG, cash_dividends=[replace(valid, record_date=day(-1))])
        with self.assertRaisesRegex(Strategy1RuleError, "actual daily observation"):
            replay_strategy1([bar(0), bar(2), bar(3)], CONFIG,
                             cash_dividends=[replace(valid, record_date=day(1), ex_date=day(2))])


if __name__ == "__main__":
    unittest.main()
