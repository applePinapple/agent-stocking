"""S11/S13 supplementary modeled replay using actual native indicator inputs.

Default is read-only preflight. Pending rules, unknown permissions or unbound
sources cannot produce a formal financial result. Modeled fills never claim to
be native, exchange or broker executions.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date,datetime
from decimal import Decimal,ROUND_HALF_UP,ROUND_FLOOR
import json
import platform
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE));sys.path.insert(0,str(BASE.parent.parent/"src"))
from agent_stocking.scale_in import (ExecutionQuote,ScaleInFill,ScaleInObservation,ScaleInPolicy,ScaleInState,ScaleInTerminalFill)
from native_execution_permissions import load_permission_csv,read_explicit_json,canonical_hash,explicit_decimal
from scale_in_native_preflight import (EXPECTED,START,END,PRICE_BASIS,digest,load_native_inputs,preflight,recheck,save_json,source_record,required_confirmations,selected_rules_confirmed)

DZERO=Decimal(0)


class ReplayBlocked(ValueError):
    def __init__(self,reason,*,events=(),snapshot=None,gaps=()):
        super().__init__(reason);self.events=list(events);self.snapshot=snapshot;self.gaps=list(gaps)


def confirmed_configuration(config,*,root,config_path,strategy_ids=(11,13)):
    if not selected_rules_confirmed(config,strategy_ids):
        raise ReplayBlocked("Parameters/local supplementary model remain pending; no modeled replay or formal returns")
    approval=config.get("approval_evidence")
    if not isinstance(approval,dict):raise ReplayBlocked("Bound overall approval evidence required")
    path=(config_path.parent/approval["path"]).resolve()
    if not path.is_relative_to(root) or digest(path.read_bytes())!=approval["sha256"]:
        raise ReplayBlocked("Overall approval evidence path/hash mismatch")
    bound=config["native_configuration_binding"];native_path=(root/bound["path"]).resolve()
    if not native_path.is_relative_to(root) or digest(native_path.read_bytes())!=bound["sha256"]:
        raise ReplayBlocked("Previously confirmed native configuration binding mismatch")
    native=json.loads(native_path.read_text(encoding="utf-8-sig"))["client_configuration"]
    if (native["buy_fee_rate"]!="0.0005" or native["sell_fee_rate"]!="0.0015"
            or native["slippage_minimum_ticks"]!=0 or native["force_end_close_included"] is not True
            or native["start"]!=str(START) or native["end"]!=str(END)
            or native["price_adjustment"]!="前复权"
            or native["entry_price"]!="次周期开盘价" or native["exit_price"]!="次周期开盘价"):
        raise ReplayBlocked("Previously confirmed native date/fee/slippage/terminal scope differs")
    values=dict(config["confirmed_values"])
    for key in ('independent_initial_cash_per_security','buy_fee_rate','sell_fee_rate','minimum_buy_fee','minimum_sell_fee','slippage'):
        explicit_decimal(values[key],key)
    if (config.get("start")!=str(START) or config.get("end")!=str(END)
            or values["price_basis"]!=PRICE_BASIS
            or set(values["quantity_units_per_security"])!=EXPECTED):
        raise ReplayBlocked("Explicit requested date, price or seven-security scope mismatch")
    if (Decimal(values["independent_initial_cash_per_security"])!=Decimal("1000000")
            or Decimal(values["buy_fee_rate"])!=Decimal("0.0005")
            or Decimal(values["sell_fee_rate"])!=Decimal("0.0015")):
        raise ReplayBlocked("Initial cash/fee rates differ from this batch's recorded native policy")
    # Fees and their rounding remain explicit, never inferred from a price bar.
    if (values["fee_rounding"] not in {"exact_decimal","cent_half_up"}
            or any(not Decimal(values[k]).is_finite() or Decimal(values[k])<0 for k in ("minimum_buy_fee","minimum_sell_fee"))
            or Decimal(values["slippage"])!=0
            or values["execution_price_precision"]!="native_export_decimal"):
        raise ReplayBlocked("Unsupported fee/rounding/minimum/slippage/price model; needs an explicit implementation")
    if (values["failed_order_policy"] not in {"cancel","retry_next_observed_security_open"}
            or values["terminal_position_policy"] not in {"mark_to_market_no_order","force_close_with_explicit_close_permission"}
            or type(values["terminal_allow_same_day_acquired_sale"]) is not bool
            or values["terminal_rejection_valuation"]!="mark_to_market"
            or values["next_open_definition"]!="next_observed_security_record"
            or values["corporate_action_policy"]!="explicitly_accepted_native_adjusted_price_model"):
        raise ReplayBlocked("Explicit supported rejection, terminal, session and adjusted-price policies required")
    if ((11 in strategy_ids and values["S11_add_requires_above_ma"] is not False)
            or (13 in strategy_ids and values["S13_add_requires_above_ma"] is not True)):
        raise ReplayBlocked("Scale-in MA filters do not match this proposed adapter")
    if values["terminal_position_policy"]!="force_close_with_explicit_close_permission" and not config.get("explicit_model_departures"):
        raise ReplayBlocked("A terminal policy departure needs an explicit supplementary model description")
    for sid in strategy_ids:
        for symbol in sorted(EXPECTED):make_policy(sid,symbol,values)
    values["_native_configuration_canonical_sha256"]=canonical_hash(native)
    return values,source_record(root,path,path.read_bytes())


def make_policy(sid,symbol,values):
    return ScaleInPolicy(sid=sid,initial_cash=explicit_decimal(values["independent_initial_cash_per_security"],'independent_initial_cash_per_security'),buy_fee_rate=explicit_decimal(values["buy_fee_rate"],'buy_fee_rate'),sell_fee_rate=explicit_decimal(values["sell_fee_rate"],'sell_fee_rate'),quantity_unit=values["quantity_units_per_security"][symbol],weights=tuple(explicit_decimal(v,f'S{sid}_weights') for v in values[f"S{sid}_weights"]),add_requires_above_ma=values[f"S{sid}_add_requires_above_ma"],price_basis=values["price_basis"],capital_basis=values["capital_basis"])


def fee(policy,side,price,quantity,values):
    key='minimum_buy_fee' if side=='buy' else 'minimum_sell_fee'
    value=max(price*quantity*(policy.buy_fee_rate if side=="buy" else policy.sell_fee_rate),explicit_decimal(values[key],key))
    return value if values["fee_rounding"]=="exact_decimal" else value.quantize(Decimal(".01"),rounding=ROUND_HALF_UP)


def affordable_buy_quantity(state,price,values):
    budget=min(state.pending.gross_cash_budget,state.cash);unit=state.policy.quantity_unit
    hi=int((budget/(price*(1+state.policy.buy_fee_rate)*unit)).to_integral_value(rounding=ROUND_FLOOR));lo=0
    while lo<hi:
        mid=(lo+hi+1)//2;quantity=mid*unit
        if price*quantity+fee(state.policy,"buy",price,quantity,values)<=budget:lo=mid
        else:hi=mid-1
    return lo*unit


def authorize(permissions,day,symbol,phase,side,quantity,price,values):
    if hasattr(permissions,"authorize"):
        return permissions.authorize(day,symbol,phase,side,quantity,
            quantity_unit=values.get("quantity_unit_basis"),
            native_configuration_canonical_sha256=values.get("_native_configuration_canonical_sha256"),execution_price=price)
    # Small explicit synthetic test stubs do not claim native observations.
    p=permissions.get(day,symbol,phase)
    return None if p is None else (p.buy_allowed if side=="buy" else p.sell_allowed)


def run_security(rows,*,symbol,policy,permissions,values,start=START,end=END):
    state=ScaleInState(policy);events=[];daily=[]
    def log(kind,**fields):events.append({"kind":kind,"sid":policy.sid,"symbol":symbol,"execution_model":"explicit_supplementary_modeled_replay_not_native_or_broker_fills",**fields})
    for row in rows:
        day=row["date"]
        if day>end:break
        if day<start:
            # Prior RSI is known at the first requested close, but no warm-up
            # position or order may be created outside the requested interval.
            state.on_close(ScaleInObservation(day,row["CX"],None,row["R4X" if policy.sid==11 else "R2X"],"native_v2_warmup_completed_close_no_entry",policy.price_basis))
            continue
        if state.pending is not None:
            order=state.pending;p=permissions.get(day,symbol,"open")
            quantity=affordable_buy_quantity(state,row["OX"],values) if order.side=="buy" else state.quantity
            if quantity<=0:
                log("modeled_order_rejected_zero_fee_inclusive_quantity",date=day,order_id=order.order_id,stage_unchanged=state.stage,permission_not_required_for_nonexecuting_order=True)
                if values["failed_order_policy"]=="cancel":state.reject(order.order_id,date=day,reason="explicit_fee_model_has_zero_affordable_quantity")
                else:state.sized=None
            else:
                allowed=authorize(permissions,day,symbol,"open",order.side,quantity,row["OX"],values)
                if allowed is None:
                    gap={"date":day,"symbol":symbol,"phase":"open","side":order.side,"quantity":quantity,"order_id":order.order_id,"reason":"missing_or_unknown_exact_execution_permission_or_native_witness"}
                    log("execution_blocked_unknown_permission",**gap)
                    raise ReplayBlocked("Unknown explicit permission/exact native witness blocks modeled replay; no fill invented",events=events,snapshot=state.snapshot(),gaps=[gap])
                quote=ExecutionQuote(day,row["OX"],allowed if order.side=="buy" else p.buy_allowed,allowed if order.side=="sell" else p.sell_allowed,"actual_native_v2_open_with_explicit_permission_"+p.kind,policy.price_basis)
                sized=state.size_at_execution(quote)
                if sized is None:
                    log("modeled_order_rejected_or_deferred",date=day,side=order.side,order_id=order.order_id,permission=allowed,stage_unchanged=state.stage)
                    if values["failed_order_policy"]=="cancel":state.reject(order.order_id,date=day,reason="explicit_permission_denied")
                elif order.side=="buy" and quantity<sized.quantity:
                    sized=state.reduce_sized_buy_quantity(quantity,reason="explicit_minimum_fee_or_rounding_budget")
                if sized is not None:
                    fill=ScaleInFill(order.order_id,day,sized.quantity,row["OX"],fee(policy,order.side,row["OX"],sized.quantity,values),True,"MODELED_next_native_open_not_an_external_execution_receipt",policy.price_basis)
                    state.apply_fill(fill)
                    log("modeled_next_open_fill",date=day,signal_date=order.signal_date,side=order.side,stage=order.stage,cycle=order.cycle,quantity=fill.quantity,modeled_price=fill.actual_price,modeled_fee=fill.actual_fee,reference_previous_modeled_tranche_price=order.reference_actual_fill_price,cash_after=state.cash,quantity_after=state.quantity,permission_source=p.source,permission_kind=p.kind,permission_available_at=p.available_at)
        request=state.on_close(ScaleInObservation(day,row["CX"],row["M200X"],row["R4X" if policy.sid==11 else "R2X"],"actual_native_v2_completed_daily_indicators",policy.price_basis))
        if request is not None:log("close_order_requested_for_future_open",date=day,request=asdict(request))
        daily.append({"date":day,"cash":state.cash,"quantity":state.quantity,"stage":state.stage,"cycle":state.cycle,"marked_equity":state.cash+state.quantity*row["CX"],"pending_order_id":state.pending.order_id if state.pending else None})
    if not daily or daily[-1]["date"]!=end:raise ReplayBlocked("Missing final requested security record",events=events,snapshot=state.snapshot())
    if state.pending:
        order=state.pending
        state.reject(order.order_id,date=end,reason="requested_period_ended_no_next_open_created")
        log("pending_order_expired_at_end",date=end,order_id=order.order_id,no_fabricated_next_day_fill=True)
    terminal_status="no_terminal_order_mark_to_market"
    final=next(r for r in reversed(rows) if r["date"]<=end)
    if state.quantity and values["terminal_position_policy"]=="force_close_with_explicit_close_permission":
        p=permissions.get(end,symbol,"close")
        settlement_blocked=not values["terminal_allow_same_day_acquired_sale"] and state.last_fill_date==end
        allowed=False if settlement_blocked else authorize(permissions,end,symbol,"close","sell",state.quantity,final["CX"],values)
        if allowed is None:
            gap={"date":end,"symbol":symbol,"phase":"close","side":"sell","reason":"unknown_explicit_terminal_permission"}
            log("terminal_execution_blocked_unknown_permission",**gap)
            raise ReplayBlocked("Terminal close permission unknown; no terminal fill invented",events=events,snapshot=state.snapshot(),gaps=[gap])
        if allowed is False:
            terminal_status="terminal_rejected_position_retained_at_explicit_mark_to_market"
            log("modeled_terminal_sale_rejected",date=end,permission=None if settlement_blocked else allowed,same_day_acquired=state.last_fill_date==end,position_retained=state.quantity)
        else:
            quantity=state.quantity;price=final["CX"];cost=fee(policy,"sell",price,quantity,values)
            state.apply_terminal_fill(ScaleInTerminalFill(end,quantity,price,cost,True,"MODELED_final_native_close_not_native_or_broker_receipt",policy.price_basis),allow_same_day_acquired_sale=values["terminal_allow_same_day_acquired_sale"])
            terminal_status="explicit_modeled_terminal_close_applied"
            log("modeled_terminal_close_fill",date=end,side="sell",quantity=quantity,modeled_price=price,modeled_fee=cost,permission_source=p.source,permission_kind=p.kind)
    equity=state.cash+state.quantity*final["CX"]
    daily[-1].update(cash=state.cash,quantity=state.quantity,stage=state.stage,marked_equity=equity,pending_order_id=None)
    return {"symbol":symbol,"sid":policy.sid,"kind":"supplementary_local_modeled_result_not_native_result","events":events,"daily":daily,"final_state":state.snapshot(),"terminal_status":terminal_status,"initial_cash":policy.initial_cash,"end_marked_equity":equity,"net_change":equity-policy.initial_cash,"return_fraction":equity/policy.initial_cash-1,"state_audit_event_count":len(state.audit)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=("preflight","modeled-backtest"),default="preflight")
    parser.add_argument("--sid",type=int,choices=(11,13),action="append",help="Run only selected SID(s); omitted means both.")
    parser.add_argument("--config",type=Path,default=BASE/"native-evaluation/scale-in/real-input/rules-pending.json")
    parser.add_argument("--permission-csv",type=Path);parser.add_argument("--permission-metadata",type=Path)
    parser.add_argument("--out",type=Path,default=BASE/"native-evaluation/scale-in/real-input/runner-output")
    args=parser.parse_args();out=args.out.resolve()
    selected=tuple(sorted(set(args.sid or (11,13))))
    if not out.is_relative_to(BASE):raise ValueError("Outputs must stay within report directory")
    if args.mode=="preflight":
        preflight(config_path=args.config,permission_csv=args.permission_csv,permission_metadata=args.permission_metadata,out=out,strategy_ids=selected)
        return 0
    if (out/"modeled-results.json").exists() or (out/"blocked.json").exists():
        raise ValueError("Preserve prior evidence: choose a new --out directory for each modeled replay attempt")
    config_path=args.config.resolve();config_blob=config_path.read_bytes();config=read_explicit_json(config_blob)
    implementation_paths=[Path(__file__).resolve(),BASE/"native_execution_permissions.py",BASE/"scale_in_native_preflight.py",BASE.parent.parent/"src/agent_stocking/scale_in.py"]
    implementation_blobs={p:p.read_bytes() for p in implementation_paths}
    captured=[];refs=[];bindings=[]
    try:
        values,approval=confirmed_configuration(config,root=BASE,config_path=config_path,strategy_ids=selected)
        if not args.permission_csv or not args.permission_metadata:raise ReplayBlocked("Explicit permission CSV and bound metadata required")
        permissions=load_permission_csv(args.permission_csv,args.permission_metadata,allowed_symbols=EXPECTED,price_basis=PRICE_BASIS,evidence_root=BASE)
        quotes,refs,bindings=load_native_inputs()
        refs.extend((source_record(BASE,config_path,config_blob),approval))
        native_config_path=(BASE/config["native_configuration_binding"]["path"]).resolve()
        if digest(native_config_path.read_bytes())!=config["native_configuration_binding"]["sha256"]:raise ValueError("Bound native configuration changed during preparation")
        refs.append(source_record(BASE,native_config_path,native_config_path.read_bytes()))
        for sid in selected:
            for symbol,rows in quotes.items():captured.append(run_security(rows,symbol=symbol,policy=make_policy(sid,symbol,values),permissions=permissions,values=values))
        recheck(BASE,refs);permissions.verify_immutable()
        if any(p.read_bytes()!=blob for p,blob in implementation_blobs.items()):raise ValueError("Implementation source changed during modeled replay")
        implementations=[{"project_path":p.relative_to(BASE.parent.parent).as_posix(),"sha256":digest(blob)} for p,blob in implementation_blobs.items()]
        result={"status":"supplementary_modeled_replay_completed_not_native_results","kind":"user_confirmed_local_supplementary_modeled_backtest","native_financial_exports_modified":False,"source_bindings":bindings,"sources":refs,"implementation_sources":implementations,"permission_csv_sha256":permissions.csv_sha256,"permission_metadata_sha256":permissions.metadata_sha256,"permission_kind":permissions.kind,"configuration":config,"results":captured,"all_sources_rechecked_before_publication":True,"historical_vintage_and_current_watchlist_censoring_certified":False}
        result["created_at_local"]=datetime.now().astimezone().isoformat(timespec="seconds")
        result["python_version"]=platform.python_version()
        result["strategy_summary"]={}
        for sid in selected:
            accounts=[r for r in captured if r["sid"]==sid]
            initial=sum((r["initial_cash"] for r in accounts),DZERO)
            ending=sum((r["end_marked_equity"] for r in accounts),DZERO)
            result["strategy_summary"][str(sid)]={"independent_accounts":len(accounts),"initial_total_cash":initial,"ending_total_marked_equity":ending,"net_change":ending-initial,"return_fraction":ending/initial-1,"kind":"supplementary_modeled_independent_accounts_not_native_summary"}
        out.mkdir(parents=True,exist_ok=True);save_json(out/"modeled-results.json",result)
        print(json.dumps({"status":result["status"],"security_strategy_accounts":len(captured)},ensure_ascii=True))
        return 0
    except (ReplayBlocked,ValueError,KeyError,OSError) as exc:
        # A failed/pending run deliberately publishes no financial fragment.
        result={"status":"blocked_no_formal_returns","kind":"supplementary_execution_preparation_failure_not_a_result","reason":str(exc),"completed_account_count_not_published_as_returns":len(captured),"blocked_state":getattr(exc,"snapshot",None),"blocked_order_events":getattr(exc,"events",[]),"permission_gaps":getattr(exc,"gaps",[]),"formal_returns":None,"native_financial_exports_modified":False}
        result.update(created_at_local=datetime.now().astimezone().isoformat(timespec="seconds"),python_version=platform.python_version(),configuration_sha256=digest(config_blob),configuration_path=str(config_path),configuration=config,sources=refs,source_bindings=bindings,implementation_sources=[{"project_path":p.relative_to(BASE.parent.parent).as_posix(),"sha256":digest(blob)} for p,blob in implementation_blobs.items()])
        out.mkdir(parents=True,exist_ok=True);save_json(out/"blocked.json",result)
        print(json.dumps({"status":result["status"],"reason":str(exc),"formal_returns":None},ensure_ascii=True));return 2


if __name__=="__main__":sys.exit(main())
