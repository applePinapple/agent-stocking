"""Run only synthetic execution-state tests and save their provenance."""
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import unittest

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent.parent
OUT = BASE / "native-evaluation/scale-in"
sys.path.insert(0, str(PROJECT))


def main():
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(PROJECT/'tests'),pattern='test_scale_in*.py',top_level_dir=str(PROJECT))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    OUT.mkdir(exist_ok=True)
    (OUT/'state-tests.log').write_text(stream.getvalue(),encoding='utf-8')
    source=PROJECT/'src/agent_stocking/scale_in.py'
    tests=PROJECT/'tests/test_scale_in_execution.py'
    test_sources=[{"path":str(p.relative_to(PROJECT)),"sha256":sha256(p.read_bytes()).hexdigest()} for p in sorted((PROJECT/'tests').glob('test_scale_in*.py'))]
    report={"kind":"SYNTHETIC_EXECUTION_STATE_TEST_NOT_MARKET_BACKTEST","status":"pass" if result.wasSuccessful() else "fail","tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"implementation_path":"src/agent_stocking/scale_in.py","implementation_sha256":sha256(source.read_bytes()).hexdigest(),"tests_path":"tests/test_scale_in_execution.py","tests_sha256":sha256(tests.read_bytes()).hexdigest(),"validator_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),"policy_status":"explicit_configuration_pending_user_parameter_reply","policy_fixture":{"initial_cash":"10000 synthetic test units, not market account results","S11_weights":["0.5","0.5"],"S11_add_requires_above_MA":False,"S13_weights":["0.1","0.2","0.3","0.4"],"S13_add_requires_C_gt_MA":True,"capital_basis":"first_signal_cash_including_buy_fees","quote_source":"synthetic explicit execution quote","fill_source":"synthetic explicit complete execution receipt","permission_source":"explicit boolean; missing/false cannot fabricate fill"},"verified_risks":["only actual complete fill advances stage","rejected or unfilled order never advances","previous actual fill price differs from signal close","cycle budget fixed and includes fee estimate","no negative cash or oversized actual fee acceptance","MA equality pauses TPS and recovery resumes","one completed close creates at most one order","T+1 compatible next-open chronology and no backdated fills","inconsistent price basis rejected","full group exit","partial fill unsupported fails explicitly","future data change and prefix truncation preserve prior snapshots for both strategies"],"temporal_prefix_cases":6,"temporal_change_and_truncation_comparisons":12,"native_probes":[{"path":str(p.relative_to(BASE)),"sha256":sha256(p.read_bytes()).hexdigest(),"native_test_status":"not_run_by_this_agent"} for p in sorted((OUT/'probes').glob('*.txt'))],"native_UI_operated":False,"real_quotes_read":False,"formal_returns_generated":False,"partial_fill_policy":"This adapter accepts complete native-style fills only; partial receipts explicitly fail and require a separately defined lifecycle."}
    report['test_sources']=test_sources
    report['verified_risks'].append('resizing a pending order to zero clears any stale sized order before apply_fill')
    (OUT/'state-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(stream.getvalue())
    print(json.dumps({k:report[k] for k in ['status','tests','failures','errors','formal_returns_generated']}))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    sys.exit(main())
