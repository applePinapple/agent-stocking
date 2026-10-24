"""Adapter risks only; synthetic witnesses never authorize real replay."""
import importlib.util
import json
from dataclasses import dataclass
import shutil
from types import SimpleNamespace
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('portfolio_runner_under_test',BASE/'portfolio_native_runner.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
from agent_stocking.portfolio import PortfolioOrderScope


class Witness:
    kind='native_model_observed_execution'
    def __init__(self):self.calls=[]
    def authorize(self,*a,**kw):self.calls.append((a,kw));return True


class AdapterTests(unittest.TestCase):
    def test_selected_strategy_confirmation_is_independent(self):
        j={'strategy_id':14,'status':'confirmed','formal_returns_permitted':True,
           'confirmation':{'supplementary_modeled_backtest':True,'S14_local_portfolio_rules':True}}
        self.assertTrue(R.is_confirmed(j,14));self.assertFalse(R.is_confirmed(j,20))
        j['confirmation']['S14_local_portfolio_rules']=False
        self.assertFalse(R.is_confirmed(j,14))

    def test_pending_rejects_before_funds_permissions_or_replay(self):
        with patch.object(R,'run_shared_portfolio',side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(ValueError,'remain pending'):
                R.validate_configuration(BASE,{'strategy_id':14,'status':'pending'},BASE/'none',14,[])

    def test_confirmed_config_rejects_binary_float_minimum_fee(self):
        j=json.loads((BASE/'portfolio14-20/native-runner/S14-rules-pending.json').read_text(encoding='utf-8'))
        j.update(status='confirmed',formal_returns_permitted=True,approval_evidence={'path':'synthetic-only-approval','sha256':R.digest(b'synthetic')})
        j['confirmation']={k:True for k in j['confirmation']}
        v=j['confirmed_values']=j['proposed_values_for_review_only']
        v.update(lot_sizes={c:100 for c in R.EXPECTED},minimum_buy_fee=1.005,minimum_sell_fee='0',
            settlement_market_sessions=1,insufficient_cash='resize',failed_order_policy='cancel',
            corporate_action_policy='explicitly_accepted_adjusted_price_model',fee_rounding='cent_half_up',
            terminal_respects_settlement=False,liquidity_lookback_boundary='start_exclusive',
            share_fact_semantics='actual_effective_with_audited_coverage')
        with patch.object(R,'read_bound',return_value=b'synthetic'),patch.object(R,'approved_native_scope',return_value={}):
            for bad in (1.005,True,False,'NaN','Infinity','-Infinity'):
                with self.subTest(bad=repr(bad)):
                    v['minimum_buy_fee']=bad
                    with self.assertRaisesRegex(ValueError,'minimum_buy_fee must be'):
                        R.validate_configuration(BASE,j,BASE/'synthetic-confirmed-test-only.json',14,[])

    def test_native_witness_has_no_implicit_share_unit_conversion(self):
        p=Witness()
        for values in ({},{'native_ledger_quantity_share_multipliers':{'688322':100}}):
            with self.assertRaisesRegex(ValueError,'explicit positive'):
                R.scoped_authorizer(p,values,'configuration',[])
        self.assertFalse(p.calls)

    def test_native_scope_preserves_config_price_side_date_and_exact_quantity(self):
        p=Witness();gaps=[];values={'native_ledger_quantity_share_multipliers':{c:100 for c in R.EXPECTED}}
        f=R.scoped_authorizer(p,values,'exact-config-sha',gaps)
        s=PortfolioOrderScope(date(2025,2,3),'688322','open','buy',200,'shares',Decimal('12.3456789'),R.PRICE_BASIS)
        self.assertTrue(f(s));a,k=p.calls[0]
        self.assertEqual(a,(s.execution_date,'688322','open','buy',2))
        self.assertEqual(k,{'quantity_unit':'native_ledger_quantity','native_configuration_canonical_sha256':'exact-config-sha','execution_price':s.price})
        self.assertEqual(gaps,[])

    def test_nonintegral_native_quantity_does_not_round_or_authorize(self):
        p=Witness();gaps=[];f=R.scoped_authorizer(p,{'native_ledger_quantity_share_multipliers':{c:100 for c in R.EXPECTED}},'x',gaps)
        s=PortfolioOrderScope(date(2025,2,3),'688322','close','sell',101,'shares',Decimal(12),R.PRICE_BASIS)
        self.assertIsNone(f(s));self.assertFalse(p.calls);self.assertEqual(gaps[0]['quantity'],101)

    def test_actual_permission_preserves_shares_without_native_mapping(self):
        p=Witness();p.kind='actual_historical_permission';f=R.scoped_authorizer(p,{},'x',[])
        s=PortfolioOrderScope(date(2025,2,3),'688322','open','buy',201,'shares',Decimal(12),R.PRICE_BASIS)
        self.assertTrue(f(s));self.assertEqual(p.calls[0][0][-1],201);self.assertEqual(p.calls[0][1]['quantity_unit'],'shares')

    def test_real_derived_row_hash_tamper_rejected(self):
        original=R.read_bound
        def changed(root,path,refs):
            blob=original(root,path,refs)
            return blob+b' ' if str(path).endswith('688322-CHECKALL-v2-rows.json') else blob
        with patch.object(R,'read_bound',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'Parsed native rows hash mismatch'):R.assemble_inputs()

    def test_real_calendar_is_preannounced_not_truncated_last_bar(self):
        d=R.assemble_inputs()
        self.assertEqual(len(d['sessions']),1648)
        self.assertFalse(d['weekly'][date(2026,10,22)])
        self.assertTrue(d['weekly'][date(2026,10,23)])
        self.assertTrue(d['weekly'][date(2021,9,30)]) # Friday national holiday
        self.assertEqual(sum(len(v) for v in d['native_rows'].values()),10443)
        for day,pub in d['calendar_publication'].items():self.assertLess(pub,R.stamp(day))
        for code in R.EXPECTED:
            r=d['histories'][code][0]
            self.assertEqual(r.available_at,R.stamp(r.bar.date))
            self.assertIsNone(r.native_features['ma200'])

    def test_actual_context_filters_future_and_open_quote_is_not_permission(self):
        data=R.assemble_inputs();p=Witness();p.kind='actual_historical_permission';p.get=lambda *a:None
        captured=[]
        def engine(config,sessions,opens,marks,strategy,factory,**kw):
            day=date(2021,1,4);ctx=factory(day,Decimal('1000000'),())
            for rows in ctx.histories.values():self.assertTrue(all(r.bar.date<=day for r in rows))
            self.assertTrue(all(d<=day for d in ctx.market_sessions))
            self.assertFalse(ctx.is_week_end)
            for q in opens[day].values():self.assertIsNone(q.buy_allowed);self.assertIsNone(q.sell_allowed)
            self.assertEqual(kw['terminal_policy'],'force_close');self.assertFalse(kw['terminal_respects_settlement'])
            captured.append('no replay/no orders/synthetic engine stub only');return 'intercepted'
        with patch.object(R,'run_shared_portfolio',side_effect=engine):
            result=R.execute(data,object(),object(),p,R.PointInTimeTable(()),{'terminal_policy':'force_close','terminal_respects_settlement':False},'x',[])
        self.assertEqual(result,'intercepted');self.assertEqual(len(captured),1);self.assertFalse(p.calls)

    def test_source_changed_after_engine_retains_invalid_committed_audit_without_returns(self):
        parent=BASE/'synthetic-portfolio-runner-fixtures';parent.mkdir(exist_ok=True)
        fixture=parent/uuid.uuid4().hex;fixture.mkdir()
        def clean():
            if not fixture.resolve().is_relative_to(parent.resolve()):raise ValueError('Fixture escaped workspace')
            shutil.rmtree(fixture)
        self.addCleanup(clean)
        config_path=fixture/'SYNTHETIC-ONLY-config.json'
        config_path.write_text(json.dumps({'strategy_id':20,'status':'confirmed','formal_returns_permitted':True,
            'confirmed_values':{},'confirmation':{'supplementary_modeled_backtest':True,'S20_local_portfolio_rules':True}}),encoding='utf-8')
        permission_file=fixture/'SYNTHETIC-NO-PERMISSIONS.txt';permission_file.write_text('SYNTHETIC TEST ONLY',encoding='ascii')
        p=SimpleNamespace(kind='SYNTHETIC_TEST_ONLY',csv_path=permission_file,csv_sha256=R.digest(permission_file.read_bytes()),
            metadata_path=permission_file,metadata_sha256=R.digest(permission_file.read_bytes()),evidence=(),
            get=lambda *a:None,verify_immutable=lambda:None)
        @dataclass
        class Curve:
            equity:Decimal
        @dataclass
        class Committed:
            fills:tuple
            orders:tuple
            equity_curve:tuple
        def engine(*a,**kw):
            config_path.write_text(config_path.read_text(encoding='utf-8')+' ',encoding='utf-8')
            return Committed(('SYNTHETIC committed fill',),('SYNTHETIC committed order',),(Curve(Decimal('1000010')),))
        with patch.object(R,'approved_native_scope',return_value={}),patch.object(R,'validate_configuration',return_value=(SimpleNamespace(initial_cash=Decimal('1000000')),object(),'synthetic-cfg-sha')),patch.object(R,'load_permission_csv',return_value=p),patch.object(R,'execute',side_effect=engine):
            result=R.run(sid=20,config_path=config_path,permission_csv=permission_file,permission_metadata=permission_file,out=fixture/'out',mode='modeled-backtest')
        self.assertEqual(result['status'],'invalid_source_no_formal_financial_result');self.assertIsNone(result['formal_returns'])
        self.assertFalse(result['all_sources_rechecked_before_publication'])
        self.assertTrue(result['orders_generated']);self.assertTrue(result['fills_generated'])
        self.assertTrue(any(e.get('path','').endswith(config_path.name) and e['observed_sha256']!=e['expected_sha256'] for e in result['source_revalidation_errors']))
        saved=json.loads((fixture/'out/interrupted-committed-state-not-a-financial-result.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['committed_state']['fills'],['SYNTHETIC committed fill'])
        self.assertFalse((fixture/'out/modeled-replay.json').exists())

if __name__=='__main__':unittest.main()
