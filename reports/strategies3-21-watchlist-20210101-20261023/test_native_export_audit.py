"""Targeted tests for native export audits; fixtures are synthetic, not market results."""
import importlib.util
from pathlib import Path
import unittest
from decimal import Decimal

SPEC = importlib.util.spec_from_file_location("audit_native_exports", Path(__file__).with_name("audit_native_exports.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeExportAuditTests(unittest.TestCase):
    def test_missing_money_is_not_zero(self):
        self.assertIsNone(MODULE.decimal_value("--", nullable=True))
        with self.assertRaises(ValueError):
            MODULE.decimal_value("")
        with self.assertRaises(ValueError):
            MODULE.decimal_value("NaN")

    def test_money_retains_decimal_cents(self):
        values = [MODULE.decimal_value("0.10"), MODULE.decimal_value("0.20")]
        self.assertEqual(sum(values), Decimal("0.30"))

    def test_gb18030_schema_and_text_preserved(self):
        original = "品种代码\t品种名称\r\n000001\t测试证券\r\n".encode("gb18030")

        class ReadOnlyFixture:
            def read_bytes(self):
                return original

        path = ReadOnlyFixture()
        columns = ("品种代码", "品种名称")
        rows, metadata = MODULE.read_native_tsv(path, columns)
        self.assertEqual(rows, [{"品种代码": "000001", "品种名称": "测试证券"}])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(metadata["records"], 1)
        with self.assertRaises(ValueError):
            MODULE.read_native_tsv(path, ("wrong",))

    def test_forced_exit_is_one_roundtrip_not_two_trades(self):
        rows = [{"source_line": 2, "signal_date": "2026-09-30", "signal": "买开", "quantity": 100, "forced_end_close": False},
                {"source_line": 3, "signal_date": "2026-10-23", "signal": "平盘", "quantity": 100, "forced_end_close": True}]
        audit = MODULE.pairing_audit(rows)
        self.assertEqual(audit["completed_roundtrips"], 1)
        self.assertTrue(audit["roundtrip_pairs"][0]["forced_exit"])
        self.assertEqual(audit["sequence_errors"], [])

    def test_duplicate_entry_and_missing_entry_are_exposed(self):
        rows = [{"source_line": 2, "signal_date": "2026-09-30", "signal": "卖平", "quantity": 100, "forced_end_close": False}]
        self.assertEqual(MODULE.pairing_audit(rows)["sequence_errors"][0]["issue"], "close_without_long")
        rows = [{"source_line": i, "signal_date": "2026-09-30", "signal": "买开", "quantity": 100, "forced_end_close": False} for i in (2, 3)]
        self.assertEqual(MODULE.pairing_audit(rows)["sequence_errors"][0]["issue"], "entry_while_already_long")


if __name__ == "__main__":
    unittest.main(verbosity=2)
