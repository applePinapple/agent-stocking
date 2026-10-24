"""Artificial assembly fixtures only; no real market performance is calculated."""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent_stocking.strategy1 import DailyObservation
from scripts import assemble_strategy1_local_replay as assembler
from scripts import run_strategy1_replay as runner


class Strategy1AssemblerTests(unittest.TestCase):
    def setUp(self):
        self.base = (Path(__file__).parent / ("assembler-artificial-" + uuid4().hex)).resolve()
        self.assertTrue(self.base.is_relative_to(Path(__file__).resolve().parent))
        self.base.mkdir()
        self.addCleanup(self.cleanup_workspace)
        (self.base / "evidence").mkdir()
        (self.base / "derived").mkdir()
        self.dates = ["2023-01-03", "2023-01-04"]
        self.images, self.metadata = [], []
        for index, day in enumerate(self.dates):
            image = f"evidence/artificial-{day}.png"
            (self.base / image).write_bytes(f"artificial evidence fixture {index}, not a screenshot".encode())
            meta = f"evidence/artificial-{day}.json"
            self.write_json(meta, {"date": day, "source": image, "close": str(10 + index * 2),
                                   "profit_pct": "50" if index == 0 else "39", "rocket": index == 0,
                                   "native_confirmation": True})
            self.images.append(image)
            self.metadata.append(meta)
        self.write_json("derived/root-approval.json", {"approved_scope": {
            "symbol": "688017", "first_observed_date": self.dates[0], "last_observed_date": self.dates[-1], "observed_dates": 2,
        }})
        transcriptions = [{"date": day, "open": str(10 + i * 2), "high": str(10 + i * 2),
                           "low": str(10 + i * 2), "close": str(10 + i * 2), "source": self.images[i], "row_locator": f"artificial row{i + 1}"}
                          for i, day in enumerate(self.dates)]
        self.csv_write("derived/prices-artificial.csv", transcriptions)
        prices = []
        for i, original in enumerate(transcriptions):
            sources = [{"source": self.images[i], "source_sha256": self.digest(self.images[i]),
                        "input_csv": "derived/prices-artificial.csv", "input_csv_sha256": self.digest("derived/prices-artificial.csv"),
                        "input_csv_line": i + 2, "row_locator": original["row_locator"]}]
            prices.append({**{k: original[k] for k in ("date", "open", "high", "low", "close")}, "sources_json": json.dumps(sources)})
        self.csv_write("derived/price-union-20261024/prices.csv", prices)
        daily = []
        for i, day in enumerate(self.dates):
            daily.append({"date": day, "rocket": i == 0, "status": "confirmed", "primary_source_id": str(i),
                          "primary_source": self.images[i], "primary_source_sha256": self.digest(self.images[i]),
                          "verification_method": "native_daily_hover_white_rocket" if i == 0 else "native_daily_hover_no_rocket_at_right_boundary",
                          "sources": [{"source": self.images[i], "sha256": self.digest(self.images[i]),
                                       "metadata": self.metadata[i], "metadata_sha256": self.digest(self.metadata[i])}],
                          "overview_mapping": []})
        self.write_json("derived/rocket-daily-confirmed.json", {
            "status": "confirmed", "symbol": "688017", "row_count": 2, "true_count": 1, "false_count": 1,
            "price_calendar": {"sha256": self.digest("derived/price-union-20261024/prices.csv")},
            "root_approval": {"path": "derived/root-approval.json", "sha256": self.digest("derived/root-approval.json")},
            "source_epoch": {"local_day_num": 1489, "used_data_num": 1489, "price_adjustment_flag": 0},
            "source_catalog": {str(i): {"image": self.images[i], "image_sha256": self.digest(self.images[i])} for i in range(2)}, "daily": daily,
        })
        self.profits = [{"date": day, "source": self.images[i], "sha256": self.digest(self.images[i]),
                         "metadata_source": self.metadata[i], "metadata_sha256": self.digest(self.metadata[i]),
                         "close": str(10 + 2 * i), "profit_pct": "50" if i == 0 else "39", "rocket": i == 0,
                         "native_confirmation": True, "independent_visual_sample_verified": True,
                         "independent_visual_review_status": "confirmed", "independently_verified_fields": ["date", "profit_pct", "close"],
                         "visual_review_source": "derived/artificial-visual-review.jsonl"}
                        for i, day in enumerate(self.dates)]
        self.write_jsonl("derived/native-daily-confirmed.jsonl", self.profits)
        self.write_jsonl("derived/artificial-visual-review.jsonl", [dict(row, popup_and_chip_cost_dates_match=True) for row in self.profits])
        self.write_jsonl("derived/native-limit-confirmed.jsonl", [{
            "date": self.dates[0], "status": "confirmed", "close_limit_up": False,
            "source": self.images[0], "sha256": self.digest(self.images[0]), "metadata_source": self.metadata[0],
            "metadata_sha256": self.digest(self.metadata[0]),
        }])
        self.cash = self.base / "derived/artificial-cash.json"
        self.write_json("derived/artificial-cash.json", {"schema_version": 1, "kind": "cash_dividend_events", "events": []})
        self.coverage = self.base / "derived/artificial-coverage.json"
        self.write_json("derived/artificial-coverage.json", {
            "schema_version": 1, "kind": "corporate_action_coverage", "status": "confirmed", "symbol": "688017",
            "start_date": self.dates[0], "end_date": self.dates[-1], "cash_dividends_sha256": self.digest("derived/artificial-cash.json"),
            "cash_dividend_events_complete": True, "cash_per_share_and_event_dates_verified": True, "other_actions_status": "none_confirmed",
            "sources": [{"source": self.images[0], "sha256": self.digest(self.images[0])}], "review_note": "Artificial test only, no real corporate-action claim",
        })

    def cleanup_workspace(self):
        if self.base.resolve().parent != Path(__file__).resolve().parent:
            raise ValueError("Refusing cleanup outside tests")
        shutil.rmtree(self.base)

    def digest(self, relative):
        return hashlib.sha256((self.base / relative).read_bytes()).hexdigest()

    def write_json(self, relative, value):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_jsonl(self, relative, rows):
        (self.base / relative).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def csv_write(self, relative, rows):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def finalize(self, **changes):
        return assembler.assemble(self.base, finalize=True, cash_dividends_path=self.cash,
                                  corporate_actions_coverage_path=self.coverage, **changes)

    def assert_no_formal(self):
        self.assertFalse((self.base / "strategy1-observations.csv").exists())
        self.assertFalse((self.base / "assembled-evidence-manifest.json").exists())

    def test_complete_market_data_without_corporate_declaration_stays_draft(self):
        status = assembler.assemble(self.base, finalize=True)
        self.assertTrue(status["safe_trace_completed_all_bars"])
        self.assertFalse(status["ready_for_formal_replay"])
        self.assert_no_formal()
        mapping = json.loads((self.base / f"derived/daily-evidence-draft/{self.dates[0]}.json").read_text())
        self.assertEqual(mapping["kind"], "tdx_daily_evidence_draft")
        observations = runner.load_observations(self.base / "strategy1-observations-draft.csv", allow_sparse_profit=True)
        with self.assertRaises(ValueError):
            runner._verify_sources(observations, self.base / "assembled-evidence-manifest-draft.json", allow_sparse_profit=True)

    def test_missing_or_unreviewed_required_p_never_promotes(self):
        for rows in (self.profits[:1], [self.profits[0], dict(self.profits[1], independent_visual_sample_verified=False)]):
            with self.subTest(rows=len(rows)):
                self.write_jsonl("derived/native-daily-confirmed.jsonl", rows)
                status = self.finalize()
                self.assertFalse(status["ready_for_formal_replay"])
                self.assertEqual(status["next_required_profit"]["date"], self.dates[1])
                self.assertEqual(status["completed_close_only_prefix_through"], self.dates[0])
                self.assert_no_formal()

    def test_guard_stops_before_false_limit_sentinel_can_create_a_buy(self):
        self.write_jsonl("derived/native-limit-confirmed.jsonl", [])
        status = self.finalize()
        self.assertEqual(status["execution_evidence_gaps"][0]["field"], "close_limit_up")
        self.assertEqual(status["draft_fills_so_far"], [])
        self.assertFalse(status["ready_for_formal_replay"])
        self.assert_no_formal()

    def test_open_permission_is_queried_before_the_missing_close_p(self):
        bars = [DailyObservation(date(2023, 1, 3), *[Decimal(10)] * 4, Decimal(50), True, True),
                DailyObservation(date(2023, 1, 4), *[Decimal(12)] * 4, None, False)]
        result, reads, missing, execution, _ = assembler.safe_trace(bars, {(self.dates[0], "close_limit_up")})
        self.assertIsNone(missing)
        self.assertEqual(execution["field"], "open_buy_allowed")
        self.assertIn((self.dates[1], "open_buy_allowed"), reads)
        self.assertEqual(result.fills, [])

    def test_complete_safe_trace_and_verified_coverage_promote_normal_kind(self):
        status = self.finalize()
        self.assertTrue(status["ready_for_formal_replay"])
        self.assertFalse(status["formal_returns_produced"])
        self.assertNotIn("pnl", status["draft_closed_batches_so_far"][0])
        proof = status["execution_field_proofs"][0]["fields"]["open_buy_allowed"]
        self.assertEqual(proof["status"], "inactive_sentinel_proven_not_read")
        self.assertFalse(proof["sentinel_is_market_fact"])
        manifest = self.base / "assembled-evidence-manifest.json"
        observations = runner.load_observations(self.base / "strategy1-observations.csv", allow_sparse_profit=True)
        runner._verify_sources(observations, manifest, allow_sparse_profit=True)
        self.assertEqual(json.loads(manifest.read_text())["status"], "confirmed")

    def test_unconfirmed_coverage_or_mismatched_metadata_prevents_promotion(self):
        coverage = json.loads(self.coverage.read_text())
        coverage["status"] = "pending"
        self.write_json("derived/artificial-coverage.json", coverage)
        with self.assertRaises(ValueError):
            self.finalize()
        self.assert_no_formal()
        self.profits[0]["metadata_source"] = self.metadata[1]
        self.profits[0]["metadata_sha256"] = self.digest(self.metadata[1])
        self.write_jsonl("derived/native-daily-confirmed.jsonl", self.profits)
        with self.assertRaisesRegex(ValueError, "date/image mismatch"):
            assembler.assemble(self.base)
        self.assert_no_formal()

    def test_source_mutation_during_trace_blocks_all_output_publication(self):
        original = assembler.safe_trace
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.base / self.images[0]).write_bytes(b"changed artificial evidence")
            return result
        with patch.object(assembler, "safe_trace", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed before assembly output"):
                self.finalize()
        self.assert_no_formal()
        self.assertFalse((self.base / "strategy1-observations-draft.csv").exists())

    def test_formal_binding_requires_exact_cash_csv_engine_and_trace_calendar(self):
        self.finalize()
        manifest = self.base / "assembled-evidence-manifest.json"
        input_path = self.base / "strategy1-observations.csv"
        observations = runner.load_observations(input_path, allow_sparse_profit=True)
        digest = runner._sha256(input_path)
        with self.assertRaisesRegex(ValueError, "cash-dividend"):
            runner._verify_assembly_binding(manifest, digest, observations, None)
        self.assertTrue(runner._verify_assembly_binding(manifest, digest, observations, self.cash))
        with self.assertRaisesRegex(ValueError, "CSV differs"):
            runner._verify_assembly_binding(manifest, "0" * 64, observations, self.cash)
        with self.assertRaisesRegex(ValueError, "calendar"):
            runner._verify_assembly_binding(manifest, digest, observations[:1], self.cash)
        other = self.base / "derived/identical-but-unbound.json"
        other.write_bytes(self.cash.read_bytes())
        with self.assertRaisesRegex(ValueError, "differs"):
            runner._verify_assembly_binding(manifest, digest, observations, other)
        payload = json.loads(manifest.read_text())
        payload["safe_trace_engine_sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Engine changed"):
            runner._verify_assembly_binding(manifest, digest, observations, self.cash)

    def test_formal_binding_detects_review_dependency_or_daily_mapping_change(self):
        self.finalize()
        manifest = self.base / "assembled-evidence-manifest.json"
        input_path = self.base / "strategy1-observations.csv"
        observations = runner.load_observations(input_path, allow_sparse_profit=True)
        review = self.base / "derived/artificial-visual-review.jsonl"
        review.write_text(review.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source changed"):
            runner._verify_assembly_binding(manifest, runner._sha256(input_path), observations, self.cash)


if __name__ == "__main__":
    unittest.main()
