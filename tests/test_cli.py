"""End-to-end CLI checks using isolated, explicitly synthetic inputs."""

import copy
import csv
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ("cli-test-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup_workspace)
        self.data = self.root / "daily.csv"
        self.data.write_text(
            "date,open,high,low,close,volume,buy_allowed,sell_allowed\n"
            "2024-01-02,10,10,10,10,100000,true,true\n"
            "2024-01-03,10,12,9,11,100000,true,true\n"
            "2024-01-04,12,12,10,12,100000,true,true\n"
            "2024-01-05,12,13,11,13,100000,true,true\n",
            encoding="utf-8",
        )
        self.signals = self.root / "signals.csv"
        self.signals.write_text(
            "date,action,reason\n2024-01-02,buy,合成买入\n2024-01-03,sell,合成卖出\n",
            encoding="utf-8",
        )
        self.config = {
            "schema_version": 1,
            "name": "CLI 合成行情测试",
            "data": {
                "path": "daily.csv", "format": "csv", "symbol": "SYNTHETIC",
                "volume_unit": "shares", "source": "synthetic",
                "price_adjustment": "none", "corporate_actions": "none",
            },
            "strategy": {"kind": "signals", "path": "signals.csv"},
            "execution": {
                "initial_cash": "1000", "lot_size": 100, "commission_rate": "0",
                "min_commission": "0", "sell_tax_rate": "0", "transfer_fee_rate": "0",
                "slippage_bps": "0", "settlement_days": 1, "require_trade_permissions": True,
            },
        }
        self.config_path = self.root / "config.json"
        self.write_config(self.config)
        self.output = self.root / "result"

    def cleanup_workspace(self):
        # Only remove the unique folder this test created inside this project.
        if self.root.resolve().parent != Path(__file__).resolve().parent:
            raise RuntimeError("refusing cleanup outside the tests directory")
        shutil.rmtree(self.root)

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *arguments):
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, str(PROJECT / "run.py"), *map(str, arguments)],
            cwd=PROJECT, env=environment, capture_output=True,
            encoding="utf-8", timeout=30, check=False,
        )

    def backtest(self, output=None):
        return self.run_cli("backtest", "--config", self.config_path,
                            "--output", self.output if output is None else output)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def read_csv(self, path):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_help_lists_available_commands(self):
        result = self.run_cli("--help")
        self.assert_success(result)
        for command in ("backtest", "demo", "import-day", "validate-data"):
            self.assertIn(command, result.stdout)

    def test_backtest_records_exact_return_and_next_session_trades(self):
        result = self.backtest()
        self.assert_success(result)
        self.assertIn("20.00%", result.stdout)
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {"summary.json", "trades.csv", "orders.csv", "equity.csv", "report.md"})
        summary = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
        metrics = summary["metrics"]
        self.assertTrue(summary["is_synthetic"])
        self.assertEqual(Decimal(metrics["final_equity"]), Decimal("1200.00"))
        self.assertEqual(metrics["total_return"], 0.2)
        self.assertEqual(metrics["transaction_count"], 2)
        self.assertEqual(metrics["round_trip_count"], 1)
        self.assertEqual(metrics["ending_quantity"], 0)
        trades = self.read_csv(self.output / "trades.csv")
        self.assertEqual([(t["signal_date"], t["date"], t["side"], t["quantity"]) for t in trades],
                         [("2024-01-02", "2024-01-03", "buy", "100"),
                          ("2024-01-03", "2024-01-04", "sell", "100")])
        self.assertEqual([t["reason"] for t in trades], ["合成买入", "合成卖出"])
        self.assertEqual([row["status"] for row in self.read_csv(self.output / "orders.csv")],
                         ["filled", "filled"])
        equity = self.read_csv(self.output / "equity.csv")
        self.assertEqual(len(equity), 4)
        self.assertEqual(Decimal(equity[-1]["equity"]), Decimal("1200.00"))
        hashes = summary["provenance"]["input_sha256"]
        for name, path in (("config", self.config_path), ("data", self.data), ("signals", self.signals)):
            self.assertEqual(hashes[name], hashlib.sha256(path.read_bytes()).hexdigest())
        report = (self.output / "report.md").read_text(encoding="utf-8")
        self.assertIn("合成数据演示", report)
        self.assertIn("20.00%", report)

    def test_same_inputs_produce_identical_summaries_and_ledgers(self):
        another = self.root / "second-result"
        self.assert_success(self.backtest())
        self.assert_success(self.backtest(another))
        first_summary = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
        second_summary = json.loads((another / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(first_summary, second_summary)
        for name in ("trades.csv", "orders.csv", "equity.csv"):
            self.assertEqual((self.output / name).read_bytes(), (another / name).read_bytes())

    def test_nonempty_output_is_preserved(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("previous result", encoding="utf-8")
        result = self.backtest()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("避免覆盖", result.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "previous result")
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_pending_strategy_fails_without_report(self):
        self.config["strategy"] = {"kind": "pending"}
        self.write_config(self.config)
        result = self.backtest()
        self.assertEqual(result.returncode, 2)
        self.assertIn("策略尚未定义", result.stderr)
        self.assertFalse(self.output.exists())

    def test_unknown_and_duplicate_json_keys_fail_without_report(self):
        unknown = copy.deepcopy(self.config)
        unknown["execution"]["mystery_fee"] = "0"
        duplicate = json.dumps(self.config, ensure_ascii=False).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1,
        )
        for config_text, error in ((json.dumps(unknown), "unknown"), (duplicate, "duplicate JSON key")):
            with self.subTest(error=error):
                self.config_path.write_text(config_text, encoding="utf-8")
                result = self.backtest()
                self.assertEqual(result.returncode, 2)
                self.assertIn(error, result.stderr)
                self.assertFalse(self.output.exists())

    def test_signal_outside_data_dates_fails_without_report(self):
        self.signals.write_text("date,action\n2024-01-06,buy\n", encoding="utf-8")
        result = self.backtest()
        self.assertEqual(result.returncode, 2)
        self.assertIn("信号日期不在行情中", result.stderr)
        self.assertIn("2024-01-06", result.stderr)
        self.assertFalse(self.output.exists())

    def test_import_day_converts_units_and_preserves_existing_output(self):
        source = self.root / "sz000001.day"
        source.write_bytes(struct.pack("<IIIIIfII", 20240102, 1010, 1200, 900, 1105, 11050.5, 1000, 0))
        destination = self.root / "converted.csv"
        result = self.run_cli("import-day", "--input", source, "--output", destination)
        self.assert_success(result)
        rows = self.read_csv(destination)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2024-01-02")
        self.assertEqual(Decimal(rows[0]["open"]), Decimal("10.10"))
        self.assertEqual(rows[0]["volume"], "1000")
        self.assertEqual(rows[0]["buy_allowed"], "")
        self.assertEqual(rows[0]["sell_allowed"], "")
        original = destination.read_bytes()
        result = self.run_cli("import-day", "--input", source, "--output", destination)
        self.assertEqual(result.returncode, 2)
        self.assertIn("拒绝覆盖", result.stderr)
        self.assertEqual(destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
