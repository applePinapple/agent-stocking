import json
import shutil
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

from agent_stocking.config import load_config
from agent_stocking.engine import run_backtest
from agent_stocking.indicators import cross, ma, ref
from agent_stocking.models import Action, BacktestResult, Bar, EquityPoint, Signal, Trade
from agent_stocking.reporting import calculate_metrics
from agent_stocking.strategies import DemoMovingAverageCross, SignalFileStrategy, load_signals


ROOT = Path(__file__).resolve().parents[1]


class IndicatorTests(unittest.TestCase):
    def test_warmup_and_ref_never_use_future_values(self):
        values = list(map(D, [1, 2, 3, 8]))
        self.assertEqual(ma(values, 3), [None, None, D(2), D(13) / 3])
        self.assertEqual(ref(values), [None, D(1), D(2), D(3)])
        self.assertEqual(ref(values, 0), values)
        self.assertEqual(ref(values, 10), [None] * 4)
        self.assertEqual(ma(values[:3], 3), ma(values, 3)[:3])

    def test_cross_equality_and_missing_edges_are_explicit(self):
        self.assertEqual(cross([None, D(1), D(2), D(3)], [D(1), D(1), D(1), D(2)]),
                         [False, False, True, False])
        with self.assertRaises(ValueError):
            cross([D(1)], [])
        for period in (0, -1, True, 1.5):
            with self.subTest(period=period), self.assertRaises(ValueError):
                ma([D(1)], period)
        with self.assertRaises(ValueError):
            ref([D(1)], -1)

    def test_demo_requires_completed_cross_not_merely_above_average(self):
        def bars(values):
            return [Bar(date(2025, 1, i + 1), D(v), D(v), D(v), D(v), 1000)
                    for i, v in enumerate(values)]
        from agent_stocking.models import Portfolio
        strategy = DemoMovingAverageCross(2, 3)
        portfolio = Portfolio(D(1000), 0)
        self.assertEqual(strategy.on_close(bars([1, 2, 3]), portfolio).action, Action.HOLD)
        self.assertEqual(strategy.on_close(bars([1, 2, 3, 4]), portfolio).action, Action.HOLD)
        self.assertEqual(strategy.on_close(bars([3, 2, 1, 4]), portfolio).action, Action.BUY)


class MetricsTests(unittest.TestCase):
    def points(self, equities):
        return [EquityPoint(date(2025, 1, i + 1), value, 0, D(10), value)
                for i, value in enumerate(map(D, equities))]

    def test_drawdown_includes_initial_capital_and_single_day_not_annualized(self):
        metrics = calculate_metrics(BacktestResult([], [], self.points([90]), D(100)))
        self.assertAlmostEqual(metrics["total_return"], -0.1)
        self.assertAlmostEqual(metrics["max_drawdown"], 0.1)
        self.assertIsNone(metrics["annualized_return"])
        self.assertIsNone(metrics["win_rate"])

    def test_drawdown_uses_running_peak(self):
        metrics = calculate_metrics(BacktestResult([], [], self.points([100, 120, 90, 110]), D(100)))
        self.assertAlmostEqual(metrics["max_drawdown"], 0.25)
        self.assertAlmostEqual(metrics["total_return"], 0.1)

    def test_closed_and_open_positions_reconcile_to_final_asset_pnl(self):
        config = load_config(ROOT / "configs/demo.json").execution
        config = replace(config, initial_cash=D(10000), lot_size=100,
                         commission_rate=D(0), min_commission=D(5),
                         sell_tax_rate=D("0.001"), transfer_fee_rate=D(0), slippage_bps=D(0))
        closes = [D(10), D(10), D(12), D(11), D(13)]
        bars = [Bar(date(2025, 1, i + 1), c, c + 1, c - 1, c, 100000, None, True, True)
                for i, c in enumerate(closes)]
        strategy = SignalFileStrategy({bars[0].date: Signal(Action.BUY),
                                       bars[1].date: Signal(Action.SELL),
                                       bars[2].date: Signal(Action.BUY)})
        result = run_backtest(bars, strategy, config)
        m = calculate_metrics(result)
        self.assertEqual(m["round_trip_count"], 1)
        self.assertEqual(m["transaction_count"], 3)
        self.assertEqual(m["win_rate"], 1)
        self.assertGreater(m["ending_quantity"], 0)
        self.assertEqual(m["realized_pnl"] + m["unrealized_pnl"], m["final_equity"] - m["initial_cash"])

    def test_fractional_cent_mark_uses_same_rounding_as_equity(self):
        trade = Trade(date(2025, 1, 1), date(2025, 1, 2), Action.BUY, 1000,
                      D(1), D(1000), D(0), D(0), D(0), D(0), "")
        point = EquityPoint(date(2025, 1, 2), D(0), 1000, D("1.000001"), D("1000.00"))
        m = calculate_metrics(BacktestResult([trade], [], [point], D(1000)))
        self.assertEqual(m["unrealized_pnl"], D(0))


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "tests" / ("core-test-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.raw = json.loads((ROOT / "configs/demo.json").read_text(encoding="utf-8"))

    def config(self, changes):
        for section, key, value in changes:
            self.raw[section][key] = value
        path = self.root / "config.json"
        path.write_text(json.dumps(self.raw, ensure_ascii=False), encoding="utf-8")
        return path

    def test_real_data_requires_permissions_and_confirmed_unadjusted_prices(self):
        for changes in (
            [("data", "price_adjustment", "front")],
            [("data", "corporate_actions", "unconfirmed")],
            [("data", "source", "user_supplied")],
        ):
            self.raw = json.loads((ROOT / "configs/demo.json").read_text(encoding="utf-8"))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load_config(self.config(changes))
        self.raw = json.loads((ROOT / "configs/demo.json").read_text(encoding="utf-8"))
        self.raw["strategy"] = {"kind": "signals", "path": "signals.csv"}
        with self.assertRaises(ValueError):
            load_config(self.config([("data", "source", "user_supplied"),
                                     ("execution", "require_trade_permissions", False)]))

    def test_no_unspecified_or_invalid_execution_parameters(self):
        for field, invalid in (("initial_cash", None), ("initial_cash", "0.001"),
                               ("lot_size", True), ("commission_rate", "NaN"),
                               ("sell_tax_rate", "1"), ("slippage_bps", "-1")):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                load_config(self.config([("execution", field, invalid)]))
            self.raw = json.loads((ROOT / "configs/demo.json").read_text(encoding="utf-8"))

    def test_path_resolution_is_relative_to_config_not_process_directory(self):
        config = load_config(self.config([]))
        self.assertEqual(config.data_path, (self.root / "../data/sample/demo.csv").resolve())

    def test_signal_duplicates_conflicts_or_invalid_actions_fail_closed(self):
        path = self.root / "signals.csv"
        for body in (
            "date,action\n2025-01-01,buy\n2025-01-01,sell\n",
            "date,action\n2025-01-01,buy_and_sell\n",
            "date,action\n2025-01-01,buy,unexpected\n",
            "date,action\n",
        ):
            with self.subTest(body=body):
                path.write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_signals(path)


if __name__ == "__main__":
    unittest.main()
