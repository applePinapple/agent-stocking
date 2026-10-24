"""Verify a public-report refresh without recomputing trades or native finances."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
HISTORY=ROOT/"report-history/before-S21-full-validation-20261025-015628"
OUT=ROOT/"report-validation/S21-full-precision-integration"


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    before=json.loads((HISTORY/"financial-snapshot.json").read_text(encoding="utf-8"))
    status=json.loads((ROOT/"strategy-status.json").read_text(encoding="utf-8"))
    manifest=json.loads((ROOT/"run-manifest.json").read_text(encoding="utf-8"))
    after={str(e["strategy_id"]):{k:e[k] for k in ("returns","native_summary","per_security")} for e in status["strategies"]}
    assert after==before["financial_by_strategy"],"Native financial values changed; do not publish verification."
    for relative,expected in before["native_export_hashes"].items():
        assert digest(ROOT/relative)==expected,("Original native export changed",relative)
    by_sid={e["strategy_id"]:e for e in status["strategies"]}
    s21=by_sid[21]["additional_native_validation"]
    assert by_sid[21]["status"]=="native_files_saved"
    assert all(s21[k] for k in ("signal_dates_verified","adjusted_execution_prices_verified","terminal_lifecycle_verified","indicator_numeric_comparison_completed"))
    assert not s21["indicator_values_exactly_equal"] and not s21["indicator_bitwise_identity_verified"]
    assert s21["validated_native_trade_rows"]==128 and s21["native_input_rows"]==10443
    assert by_sid[8]["status"]=="native_files_saved"
    assert not by_sid[8]["additional_native_validation"]["signal_dates_verified"]
    assert by_sid[8]["additional_native_validation"]["full_signal_validation_status"]=="pending_605488_actual_native_boolean_boundaries"
    assert by_sid[17]["status"]=="user_accepted_with_incomplete_ledger"
    assert not manifest["formula_native_execution_identity_verified"]
    assert not manifest["client_configuration"]["native_adjusted_execution_price_identity_verified"]
    assert not status["status"]=="final"
    OUT.mkdir(parents=True,exist_ok=True)
    tests=[]
    for filename,count in (("test_native_report_binding.py",7),("test_native_export_audit.py",5),("test_native_precision_report_binding.py",9)):
        process=subprocess.run([sys.executable,"-X","utf8",str(ROOT/filename)],capture_output=True,text=True,encoding="utf-8")
        log=OUT/(Path(filename).stem+".log")
        log.write_text(process.stdout+process.stderr,encoding="utf-8")
        tests.append({"path":filename,"sha256":digest(ROOT/filename),"exit_code":process.returncode,"test_count":count,"log_path":log.relative_to(ROOT).as_posix(),"log_sha256":digest(log)})
        assert process.returncode==0,filename
    result={"kind":"public_report_validation_integration_not_a_backtest","created_at_local":datetime.now().astimezone().isoformat(timespec="seconds"),"status":"passed",
        "old_native_financial_values_unchanged_for_strategy_count":len(after),"old_native_export_bytes_unchanged_file_count":len(before["native_export_hashes"]),
        "baseline_path":(HISTORY/"financial-snapshot.json").relative_to(ROOT).as_posix(),"baseline_sha256":digest(HISTORY/"financial-snapshot.json"),
        "S21_scope":{"securities":7,"actual_native_input_rows":10443,"native_trade_dates_and_display_prices_verified":128,"terminal_lifecycle_verified":True,"numeric_comparison_completed":True,"numeric_identity_claimed":False,"numeric_exceptions_retained":s21["numeric_exceptions"]},
        "S08_scope":{"native_display_prices_verified":1126,"complete_native_signal_validation":"pending_605488_native_boolean_boundaries"},
        "S17_user_acceptance_and_incomplete_ledger_preserved":True,"global_formula_identity_claimed":False,"final_report_claimed":False,
        "tests":tests,"test_count":sum(t["test_count"] for t in tests),
        "sources":[{"path":p,"sha256":digest(ROOT/p)} for p in ("build_native_report.py","verify_s21_public_report_integration.py","strategy-status.json","run-manifest.json","\u8bc4\u6d4b\u7ed3\u679c.md","native-evaluation/S21-precision-v2/index.json")]}
    (OUT/"evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"passed","native_financial_strategy_count":len(after),"native_files_unchanged":len(before["native_export_hashes"]),"tests":result["test_count"],"evidence_path":(OUT/"evidence.json").relative_to(ROOT).as_posix()},ensure_ascii=True))


if __name__=="__main__":main()
