"""Report validation uses small artificial fixtures, never real market results."""

from __future__ import annotations

import csv
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4


RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_strategy1_replay.py"
SPEC = importlib.util.spec_from_file_location("strategy1_replay_report_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Strategy1ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ("strategy1-report-test-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup_workspace)
        self.input_path = self.root / "artificial_test_observations.csv"
        self.output_path = self.root / "test_only_output"
        self.manifest = self.root / "artificial_test_evidence.json"
        self.manifest.write_text('{"purpose": "artificial unit test fixture only"}', encoding="utf-8")
        self.rows = [
            {"date": "2023-01-03", "open": "10", "high": "10", "low": "10", "close": "10",
             "profit_pct": "60", "rocket": "true", "close_limit_up": "false",
             "open_buy_allowed": "true", "sell_allowed": "true", "source": "artificial-test-only/first.png"},
            {"date": "2023-01-04", "open": "12", "high": "12", "low": "12", "close": "12",
             "profit_pct": "39", "rocket": "false", "close_limit_up": "false",
             "open_buy_allowed": "true", "sell_allowed": "true", "source": "artificial-test-only/second.png"},
        ]
        self.source_paths = [self.root / row["source"] for row in self.rows]
        for index, source in enumerate(self.source_paths):
            source.parent.mkdir(exist_ok=True)
            source.write_bytes(f"artificial evidence fixture {index}; not a screenshot".encode("utf-8"))

    def cleanup_workspace(self) -> None:
        if self.root.resolve().parent != Path(__file__).resolve().parent:
            raise RuntimeError("refusing cleanup outside the tests directory")
        shutil.rmtree(self.root)

    def write_input(self, rows: list[dict[str, str]] | None = None) -> None:
        with self.input_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=runner.INPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)

    def run_report(self, *, allow_sparse_profit: bool = False, cash_dividends_path: Path | None = None) -> dict[str, object]:
        return runner.run_replay(
            input_path=self.input_path, output_path=self.output_path,
            symbol="TEST", name="人工单元测试数据", start=date(2023, 1, 1),
            end=date(2023, 1, 7), evidence_manifest=self.manifest, price_adjustment="none",
            allow_sparse_profit=allow_sparse_profit,
            cash_dividends_path=cash_dividends_path,
        )

    def write_cash_dividends(self, **changes: object) -> Path:
        event = {
            "event_id": "synthetic-cash-event", "record_date": "2023-01-03",
            "ex_date": "2023-01-04", "payment_date": "2023-01-04", "cash_per_share": "0.10",
            "source": self.rows[0]["source"], "sha256": hashlib.sha256(self.source_paths[0].read_bytes()).hexdigest(),
        }
        event.update(changes)
        path = self.root / "artificial_cash_dividends.json"
        path.write_text(json.dumps({"schema_version": 1, "kind": "cash_dividend_events", "events": [event]}), encoding="utf-8")
        return path

    def test_cash_dividend_report_preserves_batch_cost_and_separate_cash_ledger(self) -> None:
        self.write_input()
        path = self.write_cash_dividends()
        summary = self.run_report(cash_dividends_path=path)
        self.assertEqual(summary["metrics"]["dividend_income"], Decimal("999.90"))
        self.assertEqual(summary["metrics"]["dividend_cash_received"], Decimal("999.90"))
        self.assertEqual(summary["metrics"]["ending_dividend_receivable"], Decimal("0.00"))
        self.assertEqual(summary["metrics"]["final_equity"], Decimal("1020975.90"))
        self.assertEqual(summary["metrics"]["realized_pnl"], Decimal("20975.90"))
        with (self.output_path / "cash_dividends.csv").open(encoding="utf-8-sig", newline="") as stream:
            ledger = list(csv.DictReader(stream))
        self.assertEqual([r["phase"] for r in ledger], ["record_date_after_close", "ex_date_receivable_before_open", "cash_payment_before_open"])
        self.assertTrue(all(r["buy_sequence"] == "1" for r in ledger))
        with (self.output_path / "closed_batches.csv").open(encoding="utf-8-sig", newline="") as stream:
            batch = list(csv.DictReader(stream))[0]
        self.assertEqual(Decimal(batch["entry_cost"]), Decimal("100000.00"))
        self.assertEqual(Decimal(batch["dividend_income"]), Decimal("999.90"))
        self.assertEqual(Decimal(batch["net_return"]), Decimal(batch["pnl"]) / Decimal(batch["entry_cost"]))
        stored = json.loads((self.output_path / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["provenance"]["cash_dividends"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(stored["source_verification"]["verified_cash_dividend_image_count"], 1)
        report = (self.output_path / "report.md").read_text(encoding="utf-8")
        self.assertIn("现金分红与应收", report)
        self.assertIn("999.90", report)
        self.assertIn(hashlib.sha256(self.source_paths[0].read_bytes()).hexdigest(), report)
        self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), report)

    def test_cash_dividend_evidence_rejects_bad_hash_escape_and_numeric_amount_without_outputs(self) -> None:
        self.write_input()
        for changes in ({"sha256": "0" * 64}, {"source": "../outside.png"},
                        {"cash_per_share": 0.1}, {"payment_date": "2023-01-03"},
                        {"record_date": None}):
            with self.subTest(changes=changes):
                path = self.write_cash_dividends(**changes)
                with self.assertRaises(ValueError):
                    self.run_report(cash_dividends_path=path)
                self.assertFalse(self.output_path.exists())

    def test_cash_dividend_additional_sources_are_verified_and_merged_by_path(self) -> None:
        self.write_input()
        extra = self.root / "artificial-test-only" / "cash-payment.png"
        extra.write_bytes(b"artificial payment-date evidence; not a screenshot")
        path = self.write_cash_dividends(additional_sources=[{
            "source": extra.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
        }])
        summary = self.run_report(cash_dividends_path=path)
        self.assertEqual(summary["source_verification"]["verified_cash_dividend_image_count"], 2)
        verification = json.loads((self.output_path / "source-verification.json").read_text(encoding="utf-8"))
        first = next(r for r in verification["files"] if r["source"] == self.rows[0]["source"])
        self.assertIn("cash_dividend", first["roles"])
        self.assertEqual(first["reference_count"], 2)

    def test_cash_dividend_json_and_source_mutation_abort_before_results_directory(self) -> None:
        self.write_input()
        path = self.write_cash_dividends()
        original_load = runner.load_cash_dividends
        def mutate_after_read(*args, **kwargs):
            result = original_load(*args, **kwargs)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return result
        with patch.object(runner, "load_cash_dividends", side_effect=mutate_after_read):
            with self.assertRaisesRegex(ValueError, "changed during read"):
                self.run_report(cash_dividends_path=path)
        self.assertFalse(self.output_path.exists())
        path = self.write_cash_dividends()
        original_replay = runner.replay_strategy1
        def mutate_evidence(*args, **kwargs):
            result = original_replay(*args, **kwargs)
            self.source_paths[0].write_bytes(b"changed original evidence fixture")
            return result
        with patch.object(runner, "replay_strategy1", side_effect=mutate_evidence):
            with self.assertRaises(ValueError):
                self.run_report(cash_dividends_path=path)
        self.assertFalse(self.output_path.exists())

    def test_cli_accepts_explicit_cash_dividends_without_changing_default_mode(self) -> None:
        self.write_input()
        path = self.write_cash_dividends()
        arguments = ["--input", str(self.input_path), "--output", str(self.output_path), "--symbol", "TEST",
                     "--name", "artificial fixture", "--start", "2023-01-01", "--end", "2023-01-07",
                     "--evidence-manifest", str(self.manifest), "--price-adjustment", "none", "--cash-dividends", str(path)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(arguments), 0)
        summary = json.loads((self.output_path / "summary.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["execution_assumptions"]["allow_sparse_profit"])
        self.assertEqual(summary["metrics"]["dividend_income"], "999.90")

    def write_daily_evidence(self, rows: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        rows = [dict(row) for row in (self.rows if rows is None else rows)]
        profit_image = self.root / "artificial-test-only" / "profit-only.png"
        profit_image.write_bytes(b"artificial profit role evidence; not a screenshot")
        roles = {"price": self.source_paths[0], "rocket": self.source_paths[1], "profit": profit_image}
        for row in rows:
            payload = {"schema_version": 1, "kind": "tdx_daily_evidence", "date": row["date"]}
            for role, image in roles.items():
                payload[role] = None if role == "profit" and not row["profit_pct"] else {
                    "source": image.relative_to(self.root).as_posix(),
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "date": row["date"], "locator": "Artificial fixture only; manually assigned date",
                }
            source = self.root / "daily-evidence" / f"{row['date']}.json"
            source.parent.mkdir(exist_ok=True)
            source.write_text(json.dumps(payload), encoding="utf-8")
            row["source"] = source.relative_to(self.root).as_posix()
        self.write_input(rows)
        return rows

    def test_round_trip_preserves_cash_fees_batches_and_provenance(self) -> None:
        self.write_input()
        summary = self.run_report()
        metrics = summary["metrics"]
        self.assertEqual(metrics["final_equity"], Decimal("1019976.00"))
        self.assertEqual(metrics["realized_pnl"], Decimal("19976.00"))
        self.assertEqual(metrics["total_fees"], Decimal("22.00"))
        self.assertEqual(metrics["ending_quantity"], 0)
        self.assertEqual(metrics["transaction_count"], 2)
        for filename in ("trades.csv", "orders.csv", "daily_equity.csv", "closed_batches.csv"):
            self.assertTrue((self.output_path / filename).read_bytes().startswith(b"\xef\xbb\xbf"))
        with (self.output_path / "trades.csv").open(encoding="utf-8-sig", newline="") as stream:
            trades = list(csv.DictReader(stream))
        self.assertEqual(trades[0]["quantity"], "9999")
        self.assertEqual(trades[0]["commission"], "10.00")
        self.assertEqual(trades[1]["cash_after"], "1019976.00")
        self.assertEqual(trades[1]["source"], "artificial-test-only/second.png")
        stored = json.loads((self.output_path / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["metrics"]["final_equity"], "1019976.00")
        self.assertEqual(stored["provenance"]["input"]["sha256"], hashlib.sha256(self.input_path.read_bytes()).hexdigest())
        self.assertEqual(stored["provenance"]["evidence_manifest"]["sha256"], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        verification_path = self.output_path / "source-verification.json"
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        self.assertEqual(verification["verified_source_count"], 2)
        self.assertEqual(verification["source_reference_count"], 2)
        self.assertEqual(verification["files"][0]["source"], self.rows[0]["source"])
        self.assertEqual(verification["files"][0]["path"], str(self.source_paths[0].resolve()))
        self.assertEqual(verification["files"][0]["sha256"], hashlib.sha256(self.source_paths[0].read_bytes()).hexdigest())
        self.assertEqual(stored["provenance"]["source_verification"]["sha256"], hashlib.sha256(verification_path.read_bytes()).hexdigest())
        self.assertTrue(stored["source_verification"]["all_sources_exist_and_hashed"])
        self.assertEqual(stored["execution_assumptions"]["price_adjustment"], "none")
        self.assertEqual(stored["execution_assumptions"]["price_adjustment_label"], "不复权")
        self.assertEqual(stored["execution_assumptions"]["other_fees"], "0.00")
        self.assertEqual(stored["coverage"]["required_observation_fields_scope"], "decision_required_only")
        report = (self.output_path / "report.md").read_text(encoding="utf-8")
        self.assertIn("人工单元测试数据（TEST）", report)
        self.assertIn("不复权（none）", report)
        self.assertIn("其他费用本次按0元计算", report)
        self.assertIn("决策实际读取的收盘封板、开盘买入许可使用原生确认记录", report)
        self.assertIn("这些占位并非市场事实", report)
        self.assertIn("卖出许可依据用户的理想收盘执行约定", report)

    def test_batch_return_uses_batch_cost_instead_of_account_initial_cash(self) -> None:
        self.write_input()
        summary = self.run_report()
        with (self.output_path / "closed_batches.csv").open(encoding="utf-8-sig", newline="") as stream:
            batches = list(csv.DictReader(stream))
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(Decimal(batch["entry_cost"]), Decimal("100000.00"))
        self.assertEqual(Decimal(batch["net_return"]), Decimal("0.19976"))
        self.assertEqual(Decimal(batch["net_return"]), Decimal(batch["pnl"]) / Decimal(batch["entry_cost"]))
        self.assertEqual(summary["metrics"]["total_return"], Decimal("0.019976"))
        self.assertEqual(summary["return_conventions"]["account_return_denominator"], Decimal("1000000.00"))
        report = (self.output_path / "report.md").read_text(encoding="utf-8")
        self.assertIn("19.9760%", report)
        self.assertIn("1.9976%", report)
        self.assertIn("不能直接将各批收益率相加作为账户收益率", report)

    def test_api_rejects_other_price_adjustment_before_creating_outputs(self) -> None:
        self.write_input()
        with self.assertRaisesRegex(ValueError, "不复权"):
            runner.run_replay(
                input_path=self.input_path, output_path=self.output_path, symbol="TEST",
                name="人工单元测试数据", start=date(2023, 1, 1), end=date(2023, 1, 7),
                evidence_manifest=self.manifest, price_adjustment="forward",
            )
        self.assertFalse(self.output_path.exists())

    def test_cli_requires_explicit_none_and_passes_confirmation_to_report(self) -> None:
        self.write_input()
        arguments = [
            "--input", str(self.input_path), "--output", str(self.output_path),
            "--symbol", "TEST", "--name", "人工单元测试数据",
            "--start", "2023-01-01", "--end", "2023-01-07",
            "--evidence-manifest", str(self.manifest),
        ]
        for extra in ([], ["--price-adjustment", "forward"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    runner.main(arguments + extra)
                self.assertEqual(failure.exception.code, 2)
                self.assertFalse(self.output_path.exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(arguments + ["--price-adjustment", "none"]), 0)
        summary = json.loads((self.output_path / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["execution_assumptions"]["price_adjustment"], "none")

    def test_sources_are_relative_to_jsonl_manifest_and_deduplicated(self) -> None:
        self.manifest = self.root / "daily-manifest.jsonl"
        self.manifest.write_text('{"date":"2023-01-03"}\n{"date":"2023-01-04"}\n', encoding="utf-8")
        rows = [dict(row) for row in self.rows]
        rows[1]["source"] = rows[0]["source"]
        self.write_input(rows)
        summary = self.run_report()
        self.assertEqual(summary["source_verification"]["source_reference_count"], 2)
        self.assertEqual(summary["source_verification"]["verified_source_count"], 1)
        verification = json.loads((self.output_path / "source-verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["base_directory"], str(self.manifest.parent.resolve()))
        self.assertEqual(len(verification["files"]), 1)
        self.assertIn("来源文件校验", (self.output_path / "report.md").read_text(encoding="utf-8"))

    def test_daily_json_verifies_all_three_roles_and_shared_image_references(self) -> None:
        rows = self.write_daily_evidence()
        summary = self.run_report()
        verification = json.loads((self.output_path / "source-verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["verified_source_count"], 2)
        self.assertEqual(verification["verified_evidence_json_count"], 2)
        self.assertEqual(verification["verified_role_image_count"], 3)
        self.assertEqual(verification["verified_file_count"], 5)
        for record in verification["files"]:
            self.assertEqual(record["sha256"], hashlib.sha256((self.root / record["source"]).read_bytes()).hexdigest())
        price_record = next(record for record in verification["files"] if record["roles"] == ["price"])
        self.assertEqual(price_record["dates"], [row["date"] for row in rows])
        self.assertEqual(price_record["reference_count"], 2)
        self.assertFalse(summary["execution_assumptions"]["allow_sparse_profit"])

    def test_sparse_idle_rows_preserve_null_p_and_every_price_bar(self) -> None:
        rows = [dict(row, profit_pct="", rocket="false") for row in self.rows]
        self.write_daily_evidence(rows)
        with self.assertRaisesRegex(ValueError, "profit_pct"):
            self.run_report()
        self.assertFalse(self.output_path.exists())
        summary = self.run_report(allow_sparse_profit=True)
        self.assertEqual(summary["metrics"]["bars"], 2)
        self.assertEqual(summary["metrics"]["transaction_count"], 0)
        self.assertEqual(summary["coverage"]["profit_omitted_rows"], 2)
        self.assertEqual(summary["coverage"]["required_profit_missing_rows"], 0)
        self.assertEqual(summary["source_verification"]["verified_role_image_count"], 2)
        with (self.output_path / "daily_equity.csv").open(encoding="utf-8-sig", newline="") as stream:
            daily = list(csv.DictReader(stream))
        self.assertEqual([row["profit_pct"] for row in daily], ["", ""])
        self.assertEqual([row["close"] for row in daily], ["10", "12"])

    def test_sparse_missing_needed_p_reports_first_date_without_output(self) -> None:
        rows = [dict(row) for row in self.rows]
        rows[0]["profit_pct"] = ""
        self.write_daily_evidence(rows)
        with self.assertRaisesRegex(ValueError, "2023-01-03.*missing required profit_pct") as failure:
            self.run_report(allow_sparse_profit=True)
        self.assertEqual(failure.exception.date, date(2023, 1, 3))
        self.assertEqual(failure.exception.observing_opportunity_ids, (1,))
        self.assertFalse(self.output_path.exists())

    def test_sparse_mode_rejects_direct_image_source_even_with_all_p_present(self) -> None:
        self.write_input()
        with self.assertRaisesRegex(ValueError, "稀疏P模式必须"):
            self.run_report(allow_sparse_profit=True)
        self.assertFalse(self.output_path.exists())

    def test_daily_json_rejects_bad_role_date_path_hash_kind_and_missing_p_evidence(self) -> None:
        mutations = [
            (lambda p: p.update(date="2023-01-02"), "JSON日期"),
            (lambda p: p["price"].update(date="2023-01-02"), "price角色日期"),
            (lambda p: p.update(rocket=None), "rocket角色"),
            (lambda p: p.update(profit=None), "profit角色"),
            (lambda p: p.update(kind="unknown"), "kind"),
            (lambda p: p.update(unrecognized_role={}), "未知字段"),
            (lambda p: p["price"].update(sha256="0" * 64), "SHA-256不符"),
            (lambda p: p["rocket"].update(source="../outside.jpg"), "路径逃逸"),
            (lambda p: p["profit"].update(source="missing.jpg"), "不存在"),
            (lambda p: p["price"].update(source=str(self.source_paths[0].resolve())), "必须是相对"),
            (lambda p: p["rocket"].update(sha256="wrong"), "SHA-256格式"),
            (lambda p: p["price"].update(locator=""), "locator"),
        ]
        for index, (mutation, expected) in enumerate(mutations):
            with self.subTest(index=index):
                rows = self.write_daily_evidence()
                evidence = self.root / rows[0]["source"]
                payload = json.loads(evidence.read_text(encoding="utf-8"))
                mutation(payload)
                evidence.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, expected):
                    self.run_report()
                self.assertFalse(self.output_path.exists())

    def test_daily_json_rejects_duplicate_keys_and_nested_json_role(self) -> None:
        rows = self.write_daily_evidence()
        evidence = self.root / rows[0]["source"]
        text = evidence.read_text(encoding="utf-8")
        evidence.write_text(text[:-1] + ',"date":"2023-01-03"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重复键"):
            self.run_report()
        self.assertFalse(self.output_path.exists())
        rows = self.write_daily_evidence()
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        payload["price"]["source"] = rows[0]["source"]
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "证据类型"):
            self.run_report()
        self.assertFalse(self.output_path.exists())

    def test_daily_json_and_dependency_mutation_during_replay_block_outputs(self) -> None:
        original_replay = runner.replay_strategy1
        for kind in ("mapping", "role_image"):
            with self.subTest(kind=kind):
                rows = self.write_daily_evidence()

                def changing_replay(observations, config, *, allow_sparse_profit: bool = False):
                    result = original_replay(observations, config, allow_sparse_profit=allow_sparse_profit)
                    changed = self.root / rows[0]["source"] if kind == "mapping" else self.source_paths[1]
                    with changed.open("ab") as stream:
                        stream.write(b"\nchanged-after-verification")
                    return result

                with patch.object(runner, "replay_strategy1", side_effect=changing_replay):
                    with self.assertRaisesRegex(ValueError, "发生变化"):
                        self.run_report()
                self.assertFalse(self.output_path.exists())

    def test_sparse_cli_flag_is_required_and_propagates_runtime_mode(self) -> None:
        self.write_daily_evidence([dict(row, profit_pct="", rocket="false") for row in self.rows])
        arguments = ["--input", str(self.input_path), "--output", str(self.output_path),
                     "--symbol", "TEST", "--name", "人工单元测试数据", "--start", "2023-01-01",
                     "--end", "2023-01-07", "--evidence-manifest", str(self.manifest), "--price-adjustment", "none"]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            runner.main(arguments)
        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(self.output_path.exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(arguments + ["--allow-sparse-profit"]), 0)
        summary = json.loads((self.output_path / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["execution_assumptions"]["allow_sparse_profit"])

    def test_missing_source_and_path_escape_never_generate_outputs(self) -> None:
        for source, message in (
            ("daily/missing.jpg", "source 文件不存在"),
            ("../outside.jpg", "路径逃逸"),
            (str(self.source_paths[0].resolve()), "必须是相对"),
        ):
            with self.subTest(source=source):
                rows = [dict(row) for row in self.rows]
                rows[0]["source"] = source
                self.write_input(rows)
                with self.assertRaisesRegex(ValueError, message):
                    self.run_report()
                self.assertFalse(self.output_path.exists())

    def test_input_change_during_read_is_rejected_before_outputs(self) -> None:
        self.write_input()
        original_load = runner.load_observations

        def changing_load(path: Path, *, allow_sparse_profit: bool = False):
            observations = original_load(path, allow_sparse_profit=allow_sparse_profit)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return observations

        with patch.object(runner, "load_observations", side_effect=changing_load):
            with self.assertRaisesRegex(ValueError, "读取期间发生变化"):
                self.run_report()
        self.assertFalse(self.output_path.exists())

    def test_input_manifest_and_source_changes_during_replay_are_rejected(self) -> None:
        original_replay = runner.replay_strategy1
        for kind in ("input", "manifest", "source"):
            with self.subTest(kind=kind):
                self.write_input()

                def changing_replay(observations, config, *, allow_sparse_profit: bool = False):
                    result = original_replay(observations, config, allow_sparse_profit=allow_sparse_profit)
                    changed = {"input": self.input_path, "manifest": self.manifest, "source": self.source_paths[0]}[kind]
                    with changed.open("ab") as stream:
                        stream.write(b"\nchanged-after-reading")
                    return result

                with patch.object(runner, "replay_strategy1", side_effect=changing_replay):
                    with self.assertRaisesRegex(ValueError, "发生变化"):
                        self.run_report()
                self.assertFalse(self.output_path.exists())

    def test_coverage_does_not_invent_calendar_completeness(self) -> None:
        self.write_input()
        summary = self.run_report()
        coverage = summary["coverage"]
        self.assertEqual(coverage["actual_first_date"], date(2023, 1, 3))
        self.assertEqual(coverage["actual_last_date"], date(2023, 1, 4))
        self.assertEqual(coverage["used_rows"], 2)
        self.assertEqual(coverage["first_date_offset_calendar_days"], 2)
        self.assertEqual(coverage["last_date_offset_calendar_days"], 3)
        self.assertFalse(coverage["full_trading_day_coverage_verified"])
        report = (self.output_path / "report.md").read_text(encoding="utf-8")
        self.assertIn("2023-01-01 至 2023-01-07", report)
        self.assertIn("幸存者偏差", report)
        self.assertIn("不能视为当时全部信号的完整历史回测", report)

    def test_incomplete_observations_never_generate_outputs(self) -> None:
        for field, value in (("profit_pct", ""), ("rocket", ""), ("source", ""),
                             ("open_buy_allowed", "maybe"), ("close_limit_up", "yes"),
                             ("profit_pct", "NaN")):
            with self.subTest(field=field, value=value):
                rows = [dict(row) for row in self.rows]
                rows[0][field] = value
                self.write_input(rows)
                with self.assertRaises(ValueError):
                    self.run_report()
                self.assertFalse(self.output_path.exists())

    def test_missing_header_empty_data_duplicate_dates_rejected(self) -> None:
        self.input_path.write_text("date,open\n2023-01-03,10\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            runner.load_observations(self.input_path)
        self.write_input([])
        with self.assertRaises(ValueError):
            runner.load_observations(self.input_path)
        duplicate = [dict(row) for row in self.rows]
        duplicate[1]["date"] = duplicate[0]["date"]
        self.write_input(duplicate)
        with self.assertRaises(ValueError):
            runner.load_observations(self.input_path)

    def test_nonempty_output_is_never_overwritten(self) -> None:
        self.write_input()
        self.output_path.mkdir()
        original = self.output_path / "keep.txt"
        original.write_text("preserve", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_report()
        self.assertEqual(original.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(list(self.output_path.iterdir()), [original])

    def test_marked_position_does_not_fabricate_exit_or_exit_fee(self) -> None:
        rows = [dict(row) for row in self.rows]
        rows[1]["profit_pct"] = "60"
        self.write_input(rows)
        summary = self.run_report()
        self.assertEqual(summary["metrics"]["sell_count"], 0)
        self.assertEqual(summary["metrics"]["ending_quantity"], 9999)
        self.assertEqual(summary["metrics"]["total_fees"], Decimal("10.00"))
        self.assertEqual(summary["metrics"]["unrealized_pnl"], Decimal("19988.00"))
        self.assertEqual(len(summary["ending_lots"]), 1)


if __name__ == "__main__":
    unittest.main()
