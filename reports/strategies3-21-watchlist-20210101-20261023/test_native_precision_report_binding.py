"""Read-only report QA regressions; mutations below are isolated in-memory tests.

No test creates trades or returns. Real saved input/audit/export bytes are never
modified. The financial-preservation case uses the saved native summary as-is.
"""
import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location("precision_report_binding_under_test",BASE/"build_native_report.py")
REPORT=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class PrecisionReportBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=REPORT.read_json(BASE/"native-evaluation/manifest.json")
        cls.codes={r["security_code"] for r in REPORT.read_json(BASE/"after-download/input-manifest.json")["securities"]}
        cls.logs=REPORT.read_run_source_log(BASE)
        cls.reader=REPORT.load_auditor(BASE)
        cls.supported=REPORT.current_native_candidates(BASE,cls.plan,cls.logs)

    def actual(self,sid):
        ledger=BASE/f"native-results/S{sid:02d}/native-trades-all.xls"
        summary=ledger.with_name("native-summary.xls")
        return ledger,REPORT.select_actual_run_source(BASE,sid,summary,ledger,self.logs)

    def validate_s21(self):
        ledger,source=self.actual(21)
        return REPORT.s21_full_precision_validation(BASE,self.codes,source,ledger,128)

    def test_full_seven_actual_graph_passes_without_claiming_exact_numeric_identity(self):
        r=self.validate_s21()
        self.assertEqual(r["validated_security_codes"],sorted(self.codes))
        self.assertEqual((r["native_input_rows"],r["validated_native_trade_rows"]),(10443,128))
        self.assertTrue(r["signal_dates_verified"] and r["terminal_lifecycle_verified"])
        self.assertTrue(r["indicator_numeric_comparison_completed"])
        self.assertFalse(r["indicator_values_exactly_equal"])
        self.assertFalse(r["indicator_bitwise_identity_verified"])
        self.assertFalse(r["execution_permission_verified"])
        self.assertEqual(len(r["numeric_exceptions"]),3)

    def test_missing_security_cannot_promote(self):
        original=REPORT.read_json
        def changed(path):
            value=original(path)
            if Path(path).name=="index.json" and "S21-precision-v2" in str(path):
                value=copy.deepcopy(value);value["securities"].pop()
            return value
        with patch.object(REPORT,"read_json",side_effect=changed):
            with self.assertRaises(ValueError):self.validate_s21()

    def test_changed_values_csv_cannot_promote(self):
        original=REPORT.sha
        with patch.object(REPORT,"sha",side_effect=lambda p:"0"*64 if Path(p).name=="values.csv" else original(p)):
            with self.assertRaises(ValueError):self.validate_s21()

    def test_actual_formula_mismatch_cannot_promote(self):
        ledger,source=self.actual(21)
        source={**source,"source_sha256":"0"*64}
        with self.assertRaises(ValueError):
            REPORT.s21_full_precision_validation(BASE,self.codes,source,ledger,128)

    def test_ledger_count_mismatch_cannot_promote(self):
        ledger,source=self.actual(21)
        with self.assertRaises(ValueError):
            REPORT.s21_full_precision_validation(BASE,self.codes,source,ledger,130)

    def test_stale_optional_evidence_preserves_original_native_financial_fields(self):
        valid=REPORT.read_strategy(BASE,21,self.codes,self.supported,self.plan,self.reader,self.logs)
        self.assertEqual(valid["status"],"native_files_saved")
        with patch.object(REPORT,"s21_full_precision_validation",side_effect=ValueError("SYNTHETIC stale QA evidence")):
            stale=REPORT.read_strategy(BASE,21,self.codes,self.supported,self.plan,self.reader,self.logs)
        self.assertEqual(stale["status"],"native_files_saved")
        for key in ("returns","native_summary","per_security","native_run_source"):
            self.assertEqual(stale[key],valid[key])
        self.assertFalse(stale["additional_native_validation"]["indicator_numeric_values_verified"])
        self.assertFalse(stale["audit"]["front_adjusted_execution_price_verified"])
        self.assertIn("SYNTHETIC",stale["additional_native_validation"]["validation_evidence_error"])

    def test_s08_prices_do_not_promote_pending_native_boolean_signals(self):
        ledger,source=self.actual(8)
        r=REPORT.s08_price_validation_only(BASE,self.codes,source,ledger,1126)
        self.assertTrue(r["adjusted_execution_prices_verified"])
        self.assertFalse(r["signal_dates_verified"])
        self.assertFalse(r["indicator_numeric_values_verified"])
        self.assertEqual(r["pending_native_boolean_security_codes"],["605488"])
        self.assertEqual(len(r["pending_native_boolean_dates"]),3)

    def test_saved_count_does_not_hide_s08_remaining_work_when_narrow_pending_list_empty(self):
        saved=[3,4,5,8,9,10,12,15,16,18,19,21]
        entries=[{"strategy_id":sid,"status":"native_files_saved","additional_native_validation":{}} for sid in saved]
        entries[-1]["additional_native_validation"]={k:True for k in ("signal_dates_verified","adjusted_execution_prices_verified","terminal_lifecycle_verified")}
        entries.append({"strategy_id":17,"status":"user_accepted_with_incomplete_ledger"})
        remaining=[6,7,11,13,14,20]
        r=REPORT.completion_accounting(entries,set(saved+[17]),remaining)
        self.assertEqual(r["accepted_or_saved_strategy_count"],13)
        self.assertEqual(r["admitted_sources_waiting_native_files_strategy_ids"],[])
        self.assertEqual(r["remaining_work_strategy_ids"],[6,7,8,11,13,14,20])
        self.assertTrue(r["original_request_still_open"])

    def test_s08_is_still_remaining_even_if_all_other_implementations_finish(self):
        entries=[{"strategy_id":8,"status":"native_files_saved","additional_native_validation":{"adjusted_execution_prices_verified":True}}]
        r=REPORT.completion_accounting(entries,{8},[])
        self.assertEqual(r["remaining_work_strategy_ids"],[8]);self.assertTrue(r["original_request_still_open"])
        entries[0]["additional_native_validation"].update(signal_dates_verified=True,terminal_lifecycle_verified=True)
        self.assertFalse(REPORT.completion_accounting(entries,{8},[])["original_request_still_open"])


if __name__=="__main__":unittest.main(verbosity=2)
