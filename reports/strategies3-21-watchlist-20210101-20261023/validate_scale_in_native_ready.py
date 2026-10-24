"""Save real-input preflight and SYNTHETIC runner/state regression evidence."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

BASE=Path(__file__).resolve().parent;PROJECT=BASE.parent.parent
OUT=BASE/"native-evaluation/scale-in/real-input/validation"


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if (OUT/'evidence.json').exists():
        history=BASE/'native-evaluation/scale-in/real-input/validation-history'/datetime.now().strftime('before-%Y%m%d-%H%M%S-%f')
        history.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(OUT,history)
    OUT.mkdir(parents=True,exist_ok=True)
    commands=[
        ("runner-and-permission-regressions",[sys.executable,"-X","utf8",str(BASE/"test_scale_in_native_runner.py")],0),
        ("state-synthetic-tests",[sys.executable,"-X","utf8",str(BASE/"validate_scale_in_state.py")],0),
        ("project-unittest",[sys.executable,"-X","utf8","-m","unittest","discover","-s","tests","-t",".","-v"],0),
        ("actual-input-preflight",[sys.executable,"-X","utf8",str(BASE/"scale_in_native_runner.py"),"--mode","preflight","--out",str(BASE/"native-evaluation/scale-in/real-input/preflight")],0),
        ("pending-modeled-guard",[sys.executable,"-X","utf8",str(BASE/"scale_in_native_runner.py"),"--mode","modeled-backtest","--out",str(OUT/("pending-guard-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")))],2),
    ]
    results=[]
    for identity,command,expected in commands:
        run=subprocess.run(command,cwd=PROJECT,capture_output=True,text=True,encoding="utf-8")
        text=run.stdout+run.stderr;log=OUT/(identity+".log");log.write_text(text,encoding="utf-8")
        count=re.findall(r"Ran (\d+) tests?",text)
        results.append({"name":identity,"command":command,"exit_code":run.returncode,"test_count":int(count[-1]) if count else None,"log":log.relative_to(BASE).as_posix(),"log_sha256":sha(log)})
        if run.returncode!=expected:raise SystemExit("Check failed: "+identity)
    pre=json.loads((BASE/"native-evaluation/scale-in/real-input/preflight/preflight.json").read_text(encoding="utf-8"))
    assert pre["security_count"]==7 and pre["full_native_rows"]==10443
    assert pre["orders_generated"] is False and pre["fills_generated"] is False and pre["formal_returns"] is None
    paths=[BASE/"native_execution_permissions.py",BASE/"scale_in_native_preflight.py",BASE/"scale_in_native_runner.py",BASE/"test_scale_in_native_runner.py",Path(__file__).resolve(),PROJECT/"src/agent_stocking/scale_in.py",PROJECT/"tests/test_scale_in_terminal.py",BASE/"native-evaluation/scale-in/real-input/rules-pending.json",BASE/"native-evaluation/scale-in/real-input/preflight/preflight.json"]
    report={"kind":"actual_input_preparation_plus_SYNTHETIC_execution_validation_not_market_returns","status":"passed","created_at_local":datetime.now().astimezone().isoformat(timespec="seconds"),"commands":results,"actual_native_input_rows":pre["full_native_rows"],"permission_gap_rows":pre["permission_gap_rows"],"actual_orders_generated":False,"actual_fills_generated":False,"formal_returns":None,"native_UI_operated":False,"pending_parameters_remain_pending":True,"source_hashes":[{"project_path":p.relative_to(PROJECT).as_posix(),"sha256":sha(p)} for p in paths]}
    report["native_execution_witness_check"]="One isolated actual saved S21 ledger row verifies source/config/date/side/quantity/display-price scope; no actual order or replay is generated. Synthetic CLI success uses fake quotes/permissions and intercepts the result writer."
    report["production_permission_true_flags_generated"]=0
    (OUT/"evidence.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"passed","tests":{r["name"]:r["test_count"] for r in results if r["test_count"] is not None},"real_native_rows":10443,"permission_gap_rows":pre["permission_gap_rows"],"formal_returns":None},ensure_ascii=True))


if __name__=="__main__":main()
