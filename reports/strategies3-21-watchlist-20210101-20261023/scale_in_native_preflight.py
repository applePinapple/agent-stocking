"""Read-only S11/S13 actual native-input preflight; produces no orders/returns."""
from __future__ import annotations
import argparse
import csv
from datetime import date,datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
REPO=BASE.parent.parent
sys.path.insert(0,str(BASE));sys.path.insert(0,str(REPO/"src"))
from agent_stocking.data import load_tdx_day
from native_execution_permissions import COLUMNS,load_permission_csv,read_explicit_json

SOURCE_SHA="f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e"
PRICE_BASIS="tdx_native_FQ1_CHECKALL_precision_v2_"+SOURCE_SHA
EXPECTED={"300308","300750","601899","603728","605488","688017","688322"}
START=date(2021,1,1);END=date(2026,10,23)


def digest(blob):return hashlib.sha256(blob).hexdigest()


def source_record(root,path,blob):
    return {"path":path.relative_to(root).as_posix(),"sha256":digest(blob),"bytes":len(blob)}


def json_default(value):
    if isinstance(value,(Decimal,date,datetime)):return str(value)
    raise TypeError(type(value).__name__)


def save_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=json_default)+"\n",encoding="utf-8")


def required_confirmations(config,strategy_ids):
    keys={"local_supplementary_modeled_backtest"}
    existing=config.get("confirmation",{})
    for sid in strategy_ids:
        if sid not in (11,13):raise ValueError("Only S11/S13 can be selected")
        own=f"S{sid}_scale_in_allocations_and_first_signal_frozen_cash"
        keys.add(own if own in existing else "scale_in_allocations_and_first_signal_frozen_cash")
        keys.add("S11_next_close_add_and_no_ma_add_filter" if sid==11 else "S13_one_stage_per_close_ma_pause_resume_previous_fill")
    return sorted(keys)


def selected_rules_confirmed(config,strategy_ids):
    statuses=config.get("strategy_status",{})
    permitted=config.get("formal_returns_permitted_by_strategy",{})
    return (all(statuses.get(str(sid),config.get("status"))=="confirmed" for sid in strategy_ids)
        and all(permitted.get(str(sid),config.get("formal_returns_permitted")) is True for sid in strategy_ids)
        and all(config.get("confirmation",{}).get(k) is True for k in required_confirmations(config,strategy_ids)))


def load_native_inputs(root=BASE):
    root=Path(root).resolve();spec=importlib.util.spec_from_file_location("scale_in_native_v2_parser",root/"audit_s21_precision_v2.py")
    parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
    input_manifest_path=root/"after-download/input-manifest.json";manifest_blob=input_manifest_path.read_bytes()
    manifest=json.loads(manifest_blob.decode("utf-8-sig"));specs={r["security_code"]:r for r in manifest["securities"]}
    if set(specs)!=EXPECTED:raise ValueError("actual seven-security inventory required")
    source=root/"native-evaluation/CHECKALL-precision-v2.txt";source_blob=source.read_bytes()
    if digest(source_blob)!=SOURCE_SHA:raise ValueError("approved immutable native v2 source hash mismatch")
    provenance=root/"native-inputs/export-provenance.jsonl";provenance_blob=provenance.read_bytes()
    records=[json.loads(line) for line in provenance_blob.decode("utf-8-sig").splitlines() if line.strip()]
    refs=[source_record(root,p,b) for p,b in ((input_manifest_path,manifest_blob),(source,source_blob),(provenance,provenance_blob),(root/"audit_s21_precision_v2.py",(root/"audit_s21_precision_v2.py").read_bytes()))]
    quotes={};binding=[]
    for code in sorted(EXPECTED):
        path=root/f"native-inputs/{code}-CHECKALL-v2.txt";blob,rows=parser.decode_export(path)
        if code not in blob.decode("gb18030").splitlines()[0]:raise ValueError("native input title security mismatch")
        match=[r for r in records if r.get("path")==path.relative_to(root).as_posix() and r.get("sha256")==digest(blob)]
        if not match:raise ValueError("no actual export/source binding for "+code)
        for r in match:
            if (r.get("source_sha256")!=SOURCE_SHA or r.get("source",r.get("native_indicator_source"))!="native-evaluation/CHECKALL-precision-v2.txt"
                    or r.get("code",r.get("security"))!=code or r.get("scale")!=1048576 or r.get("FQ")!=1):
                raise ValueError("native v2 source/security/scale/FQ binding mismatch")
        raw_path=root/"after-download"/specs[code]["snapshot_path"];raw_blob=raw_path.read_bytes()
        if digest(raw_blob)!=specs[code]["snapshot_sha256"]:raise ValueError("readonly raw date inventory hash mismatch")
        raw=load_tdx_day(raw_path)
        if [r["date"] for r in rows]!=[str(b.date) for b in raw]:raise ValueError("native input does not cover full security date inventory")
        for i,r in enumerate(rows):
            if any(r[k] is None or not r[k].is_finite() or r[k]<=0 for k in ("OX","HX","LX","CX")):
                raise ValueError("nonpositive/nonfinite native adjusted OHLC")
            for k in ("R2X","R4X","M200X"):
                if r[k] is not None and not r[k].is_finite():raise ValueError("nonfinite native indicator")
            r["date"]=date.fromisoformat(r["date"])
            for k in ("R2X","R4X"):
                if r[k]==-1:r[k]=None
            if i<199:r["M200X"]=None
        if rows[-1]["date"]!=END:raise ValueError("native security last date does not reach requested end")
        quotes[code]=rows
        binding.append({"symbol":code,"matching_source_record":match[-1],"rows":len(rows),"full_raw_date_coverage":True})
        refs.extend((source_record(root,path,blob),source_record(root,raw_path,raw_blob)))
    recheck(root,refs)
    return quotes,refs,binding


def recheck(root,refs):
    for item in refs:
        if digest((root/item["path"]).read_bytes())!=item["sha256"]:raise ValueError("input changed during run: "+item["path"])


def preflight(*,root=BASE,config_path=None,permission_csv=None,permission_metadata=None,out=None,strategy_ids=(11,13)):
    root=Path(root).resolve();config_path=Path(config_path or root/"native-evaluation/scale-in/real-input/rules-pending.json").resolve()
    out=Path(out or root/"native-evaluation/scale-in/real-input/preflight").resolve()
    if not out.is_relative_to(root):raise ValueError("preflight outputs must stay within report directory")
    config_blob=config_path.read_bytes();config=read_explicit_json(config_blob)
    quotes,refs,bindings=load_native_inputs(root)
    refs.append(source_record(root,config_path,config_blob))
    for implementation in (root/"scale_in_native_preflight.py",root/"native_execution_permissions.py"):
        refs.append(source_record(root,implementation,implementation.read_bytes()))
    if config.get("native_configuration_binding"):
        bound=config["native_configuration_binding"];native_path=(root/bound["path"]).resolve()
        if not native_path.is_relative_to(root) or digest(native_path.read_bytes())!=bound["sha256"]:
            raise ValueError("Previously confirmed native configuration source binding mismatch")
        native=json.loads(native_path.read_text(encoding="utf-8-sig"))["client_configuration"]
        if (native["buy_fee_rate"]!="0.0005" or native["sell_fee_rate"]!="0.0015"
                or native["slippage_minimum_ticks"]!=0 or native["force_end_close_included"] is not True
                or native["start"]!=str(START) or native["end"]!=str(END)
                or native["price_adjustment"]!="前复权"
                or native["entry_price"]!="次周期开盘价" or native["exit_price"]!="次周期开盘价"):
            raise ValueError("Previously confirmed native configuration differs from requested batch scope")
        refs.append(source_record(root,native_path,native_path.read_bytes()))
    permissions=None;permission_error=None
    if bool(permission_csv)!=bool(permission_metadata):raise ValueError("permission CSV and metadata must be supplied together")
    if permission_csv:
        try:
            permissions=load_permission_csv(permission_csv,permission_metadata,allowed_symbols=EXPECTED,price_basis=PRICE_BASIS,evidence_root=root)
        except (ValueError,KeyError,OSError) as exc:permission_error=str(exc)
    securities=[];gaps=[]
    for code,rows in quotes.items():
        active=[r for r in rows if START<=r["date"]<=END]
        initial={"11":[],"13":[]}
        for i,r in enumerate(rows):
            if not START<=r["date"]<=END:continue
            for phase in ("open","close"):
                p=permissions.get(r["date"],code,phase) if permissions else None
                if p is None or p.buy_allowed is None or p.sell_allowed is None:
                    gaps.append({"date":str(r["date"]),"symbol":code,"phase":phase,"buy_permission":"unknown" if p is None or p.buy_allowed is None else str(p.buy_allowed).lower(),"sell_permission":"unknown" if p is None or p.sell_allowed is None else str(p.sell_allowed).lower(),"reason":"missing_explicit_permission_row" if p is None else "explicit_unknown_side","orders_generated":False})
            above=r["M200X"] is not None and r["CX"]>r["M200X"]
            if above and r["R4X"] is not None and r["R4X"]<25:initial["11"].append(str(r["date"]))
            prior=rows[i-1]["R2X"] if i else None
            if above and r["R2X"] is not None and prior is not None and r["R2X"]<25 and prior<25:initial["13"].append(str(r["date"]))
        securities.append({"symbol":code,"full_native_rows":len(rows),"active_rows":len(active),"first_stored_date":rows[0]["date"],"last_stored_date":rows[-1]["date"],"first_active_date":active[0]["date"],"pre_start_rows":sum(r["date"]<START for r in rows),"first_full_ma200_date":next((r["date"] for r in rows if r["M200X"] is not None),None),"active_invalid_RSI2_rows":sum(r["R2X"] is None for r in active),"active_invalid_RSI4_rows":sum(r["R4X"] is None for r in active),"active_incomplete_ma200_rows":sum(r["M200X"] is None for r in active),"technical_initial_condition_dates_not_position_orders":initial})
    pending=[k for k in required_confirmations(config,strategy_ids) if config.get("confirmation",{}).get(k) is not True]
    recheck(root,refs)
    if permissions:
        permissions.verify_immutable()
    out.mkdir(parents=True,exist_ok=True)
    gap_path=out/"explicit-permission-gaps.csv"
    with gap_path.open("w",newline="",encoding="utf-8-sig") as handle:
        writer=csv.DictWriter(handle,fieldnames=["date","symbol","phase","buy_permission","sell_permission","reason","orders_generated"]);writer.writeheader();writer.writerows(gaps)
    result={"schema_version":1,"kind":"actual_native_input_preflight_not_a_backtest","status":"inputs_verified_execution_blocked_pending_rules_or_permissions","created_at_local":datetime.now().astimezone().isoformat(timespec="seconds"),"requested_range":[str(START),str(END)],"security_count":len(quotes),"full_native_rows":sum(len(v) for v in quotes.values()),"active_rows":sum(r["active_rows"] for r in securities),"price_basis":PRICE_BASIS,"native_source_sha256":SOURCE_SHA,"native_source_bindings":bindings,"raw_OHLC_used_for_execution":False,"raw_snapshots_used_only_for_dates":True,"RSI_source":"actual_native_R4X_for_S11_and_R2X_for_S13; unscaled -1 is invalid", "MA200_full_window_records":200,"securities":securities,"configuration_status":config.get("status"),"pending_confirmation_fields":pending,"permission_file_supplied":bool(permission_csv),"permission_validation_error":permission_error,"permission_kind":permissions.kind if permissions else None,"permission_gap_rows":len(gaps),"permission_gaps_path":gap_path.relative_to(root).as_posix(),"permission_gaps_sha256":digest(gap_path.read_bytes()),"no_permission_inferred_from_OHLCV":True,"parameters_pending_prevent_formal_returns":config.get("status")!="confirmed" or bool(pending),"orders_generated":False,"fills_generated":False,"formal_returns":None,"sources":refs,"all_sources_rechecked_before_publication":True,"limits":["Current native front-adjustment vintage is retained; this is not a historical-vintage corporate-action certification.","Current-watchlist selection/survival and censored historical signals remain.","Technical initial-condition dates ignore position, scale-in stages, exits, funds and actual execution; they are not orders.","A missing/unknown permission blocks execution, not filled with a presumed permission."]}
    result["unobserved_native_behavior_fields"]=config.get("implementation_observation",{})
    result["previously_confirmed_native_configuration_binding"]=config.get("native_configuration_binding")
    result["python_version"]=platform.python_version()
    result["execution_adapter_version"]="scale_in_native_inputs_v1_pending_native_behavior_probe"
    result["strategy_ids"]=sorted(set(strategy_ids))
    result["quantity_scoped_native_execution_witness_count"]=len(permissions.observed_execution_witnesses) if permissions else 0
    result["permission_gap_inventory_is_not_all_required_for_execution"]=True
    result["parameters_pending_prevent_formal_returns"]=not selected_rules_confirmed(config,strategy_ids)
    save_json(out/"preflight.json",result)
    print(json.dumps({"status":result["status"],"securities":len(quotes),"native_rows":result["full_native_rows"],"permission_gap_rows":len(gaps),"pending_confirmation_fields":pending,"orders":0,"fills":0,"formal_returns":None},ensure_ascii=True))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path);parser.add_argument("--permission-csv",type=Path);parser.add_argument("--permission-metadata",type=Path);parser.add_argument("--out",type=Path)
    args=parser.parse_args();preflight(config_path=args.config,permission_csv=args.permission_csv,permission_metadata=args.permission_metadata,out=args.out)


if __name__=="__main__":main()
