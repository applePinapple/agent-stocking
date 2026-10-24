"""SYNTHETIC runner/permission tests. No reported market returns are generated."""
from datetime import date,timedelta
from decimal import Decimal as D
import csv
import json
from pathlib import Path
import shutil
import os
from types import SimpleNamespace
import unittest
import uuid
import sys
import io
from contextlib import redirect_stdout
from unittest.mock import patch

BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
import native_execution_permissions as PERM
import scale_in_native_runner as RUN


class Fixture(unittest.TestCase):
    def setUp(self):
        parent=(BASE/"synthetic-scale-in-runner-fixtures").resolve();parent.mkdir(exist_ok=True)
        self.root=parent/uuid.uuid4().hex;self.root.mkdir()
        def cleanup():
            if not self.root.resolve().is_relative_to(parent):raise ValueError("fixture cleanup left workspace")
            shutil.rmtree(self.root)
        self.addCleanup(cleanup)
        self.source=self.root/"synthetic-only-evidence.txt";self.source.write_text("SYNTHETIC TEST EVIDENCE NOT MARKET PERMISSIONS",encoding="ascii")

    def permission(self,*,kind="actual_historical_permission",allowed="true",available="2021-01-04T09:00:00+08:00",duplicate=False):
        path=self.root/"synthetic.csv"
        row=dict(zip(PERM.COLUMNS,["2021-01-04","SYNTH","open",allowed,"false",available,"test-evidence","SYNTHETIC"]))
        with path.open("w",newline="",encoding="utf-8") as handle:
            writer=csv.DictWriter(handle,fieldnames=PERM.COLUMNS);writer.writeheader();writer.writerow(row)
            if duplicate:writer.writerow(row)
        meta={"schema_version":1,"kind":kind,"status":"verified" if kind=="actual_historical_permission" else "user_confirmed","csv_sha256":PERM.digest(path.read_bytes()),"price_basis":"SYNTHETIC","security_codes":["SYNTH"],"start":"2021-01-04","end":"2021-01-04","source_evidence":[{"id":"test-evidence","path":self.source.name,"sha256":PERM.digest(self.source.read_bytes())}]}
        if kind!="actual_historical_permission":meta.update(model_approved_at=available,approval_source_id="test-evidence")
        m=self.root/"synthetic-meta.json";m.write_text(json.dumps(meta),encoding="utf-8")
        return path,m

    def load(self,*args,**kwargs):
        path,meta=self.permission(*args,**kwargs)
        return PERM.load_permission_csv(path,meta,allowed_symbols={"SYNTH"},price_basis="SYNTHETIC",evidence_root=self.root)

    def test_missing_is_unknown_and_mapping_is_immutable(self):
        p=self.load();self.assertIsNone(p.get(date(2021,1,5),"SYNTH","open"))
        with self.assertRaises(TypeError):p.by_key[(date(2021,1,5),"SYNTH","open")]=True

    def test_unknown_is_retained_not_coerced_to_permission(self):
        p=self.load(allowed="unknown");self.assertIsNone(p.get(date(2021,1,4),"SYNTH","open").buy_allowed)

    def test_future_historical_permission_and_loose_boolean_rejected(self):
        with self.assertRaises(ValueError):self.load(available="2021-01-04T09:31:00+08:00")
        with self.assertRaises(ValueError):self.load(allowed="1")
        with self.assertRaises(ValueError):self.load(duplicate=True)

    def test_current_model_approval_remains_labeled_scenario_not_historical_fact(self):
        p=self.load(kind="user_approved_historical_execution_scenario",available="2026-10-25T12:00:00+08:00")
        record=p.get(date(2021,1,4),"SYNTH","open")
        self.assertEqual(record.kind,"user_approved_historical_execution_scenario")
        self.assertEqual(record.available_at.year,2026)

    def test_source_changed_after_load_fails_publication_guard(self):
        p=self.load();self.source.write_text("CHANGED SYNTHETIC EVIDENCE",encoding="ascii")
        with self.assertRaises(ValueError):p.verify_immutable()

    def test_duplicate_json_authorization_fields_rejected(self):
        with self.assertRaises(ValueError):PERM.read_explicit_json(b'{"status":"pending","status":"user_confirmed"}')

    def test_single_sid_confirmation_does_not_wait_for_other_sid(self):
        config={"confirmation":{"local_supplementary_modeled_backtest":True,"S11_scale_in_allocations_and_first_signal_frozen_cash":True,"S11_next_close_add_and_no_ma_add_filter":True,"S13_scale_in_allocations_and_first_signal_frozen_cash":False,"S13_one_stage_per_close_ma_pause_resume_previous_fill":False}}
        keys=RUN.required_confirmations(config,(11,))
        self.assertTrue(all(config["confirmation"][k] for k in keys))
        self.assertFalse(any("S13" in k for k in keys))
        self.assertTrue(any(config["confirmation"][k] is False for k in RUN.required_confirmations(config,(13,))))
        config.update(status="pending",formal_returns_permitted=False,strategy_status={"11":"confirmed","13":"pending"},formal_returns_permitted_by_strategy={"11":True,"13":False})
        self.assertTrue(RUN.selected_rules_confirmed(config,(11,)))
        self.assertFalse(RUN.selected_rules_confirmed(config,(13,)))
        self.assertFalse(RUN.selected_rules_confirmed(config,(11,13)))

    def test_single_sid_complete_cli_uses_only_selected_policy_with_synthetic_quotes(self):
        config=json.loads((BASE/"native-evaluation/scale-in/real-input/rules-pending.json").read_text(encoding="utf-8"))
        config.update(status="pending",formal_returns_permitted=False,strategy_status={"11":"confirmed","13":"pending"},formal_returns_permitted_by_strategy={"11":True,"13":False})
        for key in RUN.required_confirmations(config,(11,)):config["confirmation"][key]=True
        values=dict(config.pop("proposed_values_for_review_only"))
        values.update(quantity_units_per_security={s:1 for s in RUN.EXPECTED},fee_rounding="exact_decimal",minimum_buy_fee="0",minimum_sell_fee="0",execution_price_precision="native_export_decimal",failed_order_policy="cancel",terminal_position_policy="mark_to_market_no_order",terminal_allow_same_day_acquired_sale=False)
        values.pop("S13_weights");values.pop("S13_add_requires_above_ma")
        config.update(confirmed_values=values,approval_evidence={"path":self.source.name,"sha256":PERM.digest(self.source.read_bytes())},explicit_model_departures=["SYNTHETIC TEST ONLY: fake quotes, fake permissions; output writer intercepted, no market results"])
        file=self.root/"SYNTHETIC-CLI-policy.json";file.write_text(json.dumps(config),encoding="utf-8")
        rows=SyntheticReplayTests().rows();rows[-1]["date"]=RUN.END
        quotes={s:[dict(r) for r in rows] for s in RUN.EXPECTED}
        permissions=SyntheticReplayTests().permissions();permissions.csv_sha256="SYNTHETIC";permissions.metadata_sha256="SYNTHETIC";permissions.kind="SYNTHETIC_TEST_ONLY";permissions.verify_immutable=lambda:None
        saved=[]
        argv=["scale_in_native_runner.py","--mode","modeled-backtest","--sid","11","--config",str(file),"--permission-csv","SYNTHETIC_UNUSED.csv","--permission-metadata","SYNTHETIC_UNUSED.json","--out",str(self.root/"intercepted-output")]
        with patch.object(sys,"argv",argv),patch.object(RUN,"load_permission_csv",return_value=permissions),patch.object(RUN,"load_native_inputs",return_value=(quotes,[],[{"kind":"SYNTHETIC TEST QUOTES ONLY"}])),patch.object(RUN,"save_json",side_effect=lambda path,result:saved.append(result)),redirect_stdout(io.StringIO()):
            self.assertEqual(RUN.main(),0)
        self.assertEqual(len(saved),1);self.assertEqual(len(saved[0]["results"]),7)
        self.assertEqual(set(saved[0]["strategy_summary"]),{"11"})
        self.assertTrue(all(r["sid"]==11 for r in saved[0]["results"]))
        self.assertFalse((self.root/"intercepted-output/modeled-results.json").exists())

    def observed_fixture(self,*,quantity=46230,execution_date="2021-05-19",side="buy",config_hash=None,symbol="300308",corrupt_source_hash=False):
        """One isolated test mask references a real saved native row, never runs it."""
        cfg=BASE/"report-history/before-S21-full-validation-20261025-015628/run-manifest.json"
        files={"native-config":cfg,"native-ledger":BASE/"native-results/S21/native-trades-all.xls","native-summary":BASE/"native-results/S21/native-summary.xls","formula":BASE/"native-evaluation/per-strategy/S21-expert.txt","run-log":BASE/"native-run-source-log.jsonl","input":BASE/"native-inputs/300308-CHECKALL-v2.txt","indicator":BASE/"native-evaluation/CHECKALL-precision-v2.txt","provenance":BASE/"native-inputs/export-provenance.jsonl"}
        canonical=PERM.canonical_hash(json.loads(cfg.read_text(encoding="utf-8"))["client_configuration"])
        available="2026-10-25T12:00:00+08:00"
        path=self.root/"ISOLATED_ONE_NATIVE_OBSERVATION_TEST.csv"
        with path.open("w",newline="",encoding="utf-8") as handle:
            w=csv.DictWriter(handle,fieldnames=PERM.COLUMNS);w.writeheader();w.writerow(dict(zip(PERM.COLUMNS,[execution_date,symbol,"open","true" if side=="buy" else "unknown","true" if side=="sell" else "unknown",available,"native-ledger",RUN.PRICE_BASIS])))
        meta={"schema_version":1,"kind":"native_model_observed_execution","status":"verified_native_model_observation","csv_sha256":PERM.digest(path.read_bytes()),"price_basis":RUN.PRICE_BASIS,"security_codes":["300308"],"start":"2021-01-01","end":"2026-10-23","observed_at":available,"source_evidence":[{"id":identity,"path":os.path.relpath(p,self.root),"sha256":PERM.digest(p.read_bytes())} for identity,p in files.items()],"native_model":{"kind":"tdx_program_trade_evaluation","client_version":"V7.71","model_id":"ISOLATED_TEST_NATIVE_S21_OBSERVATION_NOT_GENERAL_MARKET_PERMISSION","strategy_id":21,"configuration_source_id":"native-config","configuration_canonical_sha256":config_hash or canonical,"ledger_source_id":"native-ledger","summary_source_id":"native-summary","formula_source_id":"formula","run_source_log_id":"run-log","indicator_source_id":"indicator","input_provenance_source_id":"provenance"},"native_execution_observations":[{"native_ledger_row_1based":2,"symbol":"300308","execution_date":execution_date,"phase":"open","side":side,"quantity":quantity,"quantity_unit":"native_ledger_quantity","native_input_source_id":"input"}]}
        meta["security_codes"]=[symbol];meta["native_execution_observations"][0]["symbol"]=symbol
        if corrupt_source_hash:meta["source_evidence"][0]["sha256"]="0"*64
        metadata=self.root/"ISOLATED_ONE_NATIVE_OBSERVATION_TEST.json";metadata.write_text(json.dumps(meta),encoding="utf-8")
        return PERM.load_permission_csv(path,metadata,allowed_symbols={symbol},price_basis=RUN.PRICE_BASIS,evidence_root=BASE),canonical

    def test_native_observation_is_exact_scope_not_generic_actual_permission(self):
        p,cfg=self.observed_fixture();day=date(2021,5,19)
        self.assertIsNone(p.get(day,"300308","open").buy_allowed)
        self.assertTrue(p.authorize(day,"300308","open","buy",46230,quantity_unit="native_ledger_quantity",native_configuration_canonical_sha256=cfg,execution_price=D("21.62")))
        self.assertIsNone(p.authorize(day,"300308","open","buy",46200,quantity_unit="native_ledger_quantity",native_configuration_canonical_sha256=cfg,execution_price=D("21.62")))
        self.assertIsNone(p.authorize(day,"300308","open","sell",46230,quantity_unit="native_ledger_quantity",native_configuration_canonical_sha256=cfg,execution_price=D("21.62")))
        self.assertIsNone(p.authorize(day,"300308","open","buy",46230,quantity_unit="native_ledger_quantity",native_configuration_canonical_sha256=cfg,execution_price=D("21.63")))
        with self.assertRaises(ValueError):p.authorize(day,"300308","open","buy",46230,quantity_unit="native_ledger_quantity",native_configuration_canonical_sha256="0"*64,execution_price=D("21.62"))
        self.assertEqual(p.kind,"native_model_observed_execution")

    def test_native_observation_rejects_edited_quantity_date_side_and_configuration(self):
        for change in ({"quantity":46200},{"execution_date":"2021-05-20"},{"side":"sell"},{"config_hash":"0"*64},{"symbol":"688322"},{"corrupt_source_hash":True}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):self.observed_fixture(**change)


class SyntheticReplayTests(unittest.TestCase):
    def rows(self):
        closes=[D(10),D(10),D(11),D(10),D("9.5"),D(10),D(11),D(11)]
        opens=[D(10),D(10),D(12),D(11),D(10),D("9.5"),D(11),D(11)]
        rsi=[D(20),D(20),D(20),D(20),D(20),D(80),D(80),D(80)]
        return [{"date":date(2021,1,4)+timedelta(days=i),"CX":c,"OX":opens[i],"M200X":D(8),"R2X":rsi[i],"R4X":rsi[i]} for i,c in enumerate(closes)]

    def values(self):return {"failed_order_policy":"cancel","terminal_position_policy":"mark_to_market_no_order","terminal_allow_same_day_acquired_sale":False,"fee_rounding":"exact_decimal","minimum_buy_fee":"0","minimum_sell_fee":"0"}

    def policy(self,sid):
        return RUN.ScaleInPolicy(sid,D(10000),D(".0005"),D(".0015"),1,(D(".5"),D(".5")) if sid==11 else tuple(map(D,[".1",".2",".3",".4"])),sid==13,"SYNTHETIC","first_signal_cash_including_buy_fees")

    def permissions(self,allowed=True):
        return SimpleNamespace(get=lambda day,symbol,phase:SimpleNamespace(buy_allowed=allowed,sell_allowed=allowed,kind="SYNTHETIC_TEST_ONLY",source="synthetic",available_at="synthetic"))

    def run_rows(self,rows,sid=13,allowed=True,**kwargs):
        return RUN.run_security(rows,symbol="SYNTH",policy=self.policy(sid),permissions=self.permissions(allowed),values=self.values(),start=rows[0]["date"],end=rows[-1]["date"],**kwargs)

    def test_tps_four_fills_use_previous_modeled_execution_price(self):
        r=self.run_rows(self.rows());fills=[e for e in r["events"] if e["kind"]=="modeled_next_open_fill"]
        self.assertEqual([e["stage"] for e in fills if e["side"]=="buy"],[1,2,3,4])
        self.assertEqual(fills[1]["reference_previous_modeled_tranche_price"],D(12))
        self.assertEqual(fills[-1]["side"],"sell");self.assertEqual(r["final_state"]["quantity"],0)
        self.assertTrue(all("not_native_or_broker" in e["execution_model"] for e in fills))

    def test_false_permission_rejects_without_a_fill_or_stage_advance(self):
        r=self.run_rows(self.rows(),allowed=False)
        self.assertEqual(r["final_state"]["stage"],0)
        self.assertFalse(any(e["kind"]=="modeled_next_open_fill" for e in r["events"]))

    def test_unknown_permission_stops_before_creating_a_fill(self):
        with self.assertRaises(RUN.ReplayBlocked) as caught:self.run_rows(self.rows(),allowed=None)
        self.assertEqual(caught.exception.snapshot["stage"],0)
        self.assertEqual(len(caught.exception.gaps),1)
        self.assertFalse(any(e["kind"]=="modeled_next_open_fill" for e in caught.exception.events))

    def test_prefix_and_future_changes_do_not_change_historical_orders_or_cash(self):
        rows=self.rows();baseline=self.run_rows(rows);cut=4
        altered=[dict(r) for r in rows]
        for r in altered[cut+1:]:r.update(CX=D(1000),OX=D(999),R2X=D(0),R4X=D(0))
        modified=self.run_rows(altered);prefix=self.run_rows(rows[:cut+1])
        def historical(result):
            return [e for e in result["events"] if e.get("date",date.min)<=rows[cut]["date"] and e["kind"]!="pending_order_expired_at_end"]
        self.assertEqual(historical(baseline),historical(modified));self.assertEqual(historical(baseline),historical(prefix))
        for i in range(cut):self.assertEqual(baseline["daily"][i],prefix["daily"][i])

    def test_pending_configuration_rejects_before_any_replay(self):
        config=RUN.json.loads((BASE/"native-evaluation/scale-in/real-input/rules-pending.json").read_text(encoding="utf-8"))
        with self.assertRaises(RUN.ReplayBlocked):RUN.confirmed_configuration(config,root=BASE,config_path=BASE/"native-evaluation/scale-in/real-input/rules-pending.json")

    def test_explicit_minimum_fee_reduces_quantity_inside_frozen_stage_budget(self):
        rows=self.rows();values=self.values();values.update(minimum_buy_fee="50",fee_rounding="cent_half_up")
        r=RUN.run_security(rows,symbol="SYNTH",policy=self.policy(13),permissions=self.permissions(),values=values,start=rows[0]["date"],end=rows[-1]["date"])
        first=next(e for e in r["events"] if e["kind"]=="modeled_next_open_fill")
        self.assertEqual((first["quantity"],first["modeled_fee"]),(79,D("50.00")))
        self.assertLessEqual(first["quantity"]*first["modeled_price"]+first["modeled_fee"],D(1000))

    def test_minimum_fee_1_005_requires_finite_string_and_rounds_exactly(self):
        values=self.values();values.update(minimum_buy_fee='1.005',fee_rounding='cent_half_up')
        self.assertEqual(RUN.fee(self.policy(13),'buy',D(10),1,values),D('1.01'))
        for bad in (1.005,True,False,'NaN','Infinity','-Infinity'):
            with self.subTest(bad=repr(bad)):
                values['minimum_buy_fee']=bad
                with self.assertRaises(ValueError):RUN.fee(self.policy(13),'buy',D(10),1,values)

    def test_policy_weights_cannot_coerce_json_floats_or_booleans(self):
        config=RUN.json.loads((BASE/'native-evaluation/scale-in/real-input/rules-pending.json').read_text(encoding='utf-8'))
        values=config['proposed_values_for_review_only'];values['quantity_units_per_security']={s:1 for s in RUN.EXPECTED}
        for bad in ([0.5,0.5],[True,False],['NaN','0.5'],['Infinity','0.5']):
            with self.subTest(bad=bad):
                values['S11_weights']=bad
                with self.assertRaises(ValueError):RUN.make_policy(11,'688322',values)

    def test_zero_affordable_order_does_not_require_an_execution_permission(self):
        rows=self.rows();values=self.values();values["minimum_buy_fee"]="100000"
        result=RUN.run_security(rows,symbol="SYNTH",policy=self.policy(13),permissions=self.permissions(None),values=values,start=rows[0]["date"],end=rows[-1]["date"])
        self.assertEqual(result["final_state"]["quantity"],0)
        self.assertFalse(any(e["kind"]=="modeled_next_open_fill" for e in result["events"]))


if __name__=="__main__":unittest.main(verbosity=2)
