"""S14/S20 native-input adapter; pending preflight is never a financial replay.

Uses the existing shared-cash engine. Outputs describe supplementary modeled
fills, never a TDX export, exchange receipt or broker execution.
"""
from __future__ import annotations
import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import platform
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE));sys.path.insert(0,str(BASE.parent.parent/'src'))
from agent_stocking.data import load_tdx_day
from agent_stocking.models import Bar
from agent_stocking.point_in_time import PointInTimeTable,load_verified_fact_bundle
from agent_stocking.portfolio import PortfolioExecutionConfig,OpenQuote,run_shared_portfolio
from agent_stocking.portfolio_strategies import (PortfolioRules,PortfolioCloseContext,PublishedDailyBar,
    CumulativeRsiPortfolio,WeeklyRsiRotation)
from native_execution_permissions import load_permission_csv,read_explicit_json,canonical_hash,explicit_decimal
from scale_in_native_preflight import (EXPECTED,START,END,PRICE_BASIS,SOURCE_SHA,digest,
    load_native_inputs,recheck,save_json,source_record)

TZ=timezone(timedelta(hours=8))
BENCHMARK='000300.CSI'


def stamp(day,phase='close'):
    return datetime.combine(day,time(9,30) if phase=='open' else time(15),TZ)


def read_bound(root,path,refs):
    path=Path(path).resolve()
    if not path.is_relative_to(root):raise ValueError('Source must remain inside this report evidence root')
    blob=path.read_bytes();refs.append(source_record(root,path,blob));return blob


def assemble_inputs(root=BASE):
    """Bind original native exports, parsed derivatives, raw closes and calendars."""
    root=Path(root).resolve();native,refs,bindings=load_native_inputs(root)
    manifest=read_explicit_json((root/'after-download/input-manifest.json').read_bytes())
    raw_specs={r['security_code']:r for r in manifest['securities']}
    histories={};native_rows={}
    for code in sorted(EXPECTED):
        prefix=root/f'portfolio14-20/native-input-audit/{code}-CHECKALL-v2'
        audit=read_explicit_json(read_bound(root,Path(str(prefix)+'-audit.json'),refs))
        rows_blob=read_bound(root,Path(str(prefix)+'-rows.json'),refs)
        if digest(rows_blob)!=audit['parsed_rows_sha256']:raise ValueError('Parsed native rows hash mismatch')
        parsed=read_explicit_json(rows_blob)
        original_sha=next(r['sha256'] for r in refs if r['path']==f'native-inputs/{code}-CHECKALL-v2.txt')
        if parsed['symbol']!=code or audit['symbol']!=code or parsed['original_sha256']!=original_sha or audit['original_sha256']!=original_sha:
            raise ValueError('Parsed native rows are not bound to the actual security export')
        raw_path=root/'after-download'/raw_specs[code]['snapshot_path'];raw=load_tdx_day(raw_path)
        if len(parsed['rows'])!=len(native[code]) or len(raw)!=len(native[code]):raise ValueError('Full native history coverage mismatch')
        out=[]
        for r,n,b in zip(parsed['rows'],native[code],raw):
            if r['date']!=str(n['date']) or n['date']!=b.date or r['FQ']!='1.00':raise ValueError('Native/raw date or adjustment mismatch')
            for field,key in [('open','OX'),('high','HX'),('low','LX'),('close','CX')]:
                if Decimal(r[field])!=n[key]:raise ValueError('Parsed adjusted price differs from bound native value')
            rsi=None if r['RSI2'] is None or Decimal(r['RSI2'])==-1 else Decimal(r['RSI2'])
            ma=None if r['MA200'] is None else Decimal(r['MA200'])
            if rsi!=n['R2X'] or ma!=n['M200X']:raise ValueError('Parsed indicator differs from bound native value')
            if Decimal(r['volume_as_native_base_column'])!=b.volume or Decimal(r['amount_cny'])!=n['AMT']:
                raise ValueError('Volume shares or CNY amount mismatch')
            bar=Bar(b.date,n['OX'],n['HX'],n['LX'],n['CX'],b.volume,n['AMT'])
            out.append(PublishedDailyBar(code,bar,stamp(b.date),stamp(b.date),b.close,
                f"native-CHECKALL-v2:{original_sha};raw-close:{raw_specs[code]['snapshot_sha256']}",{'rsi2':rsi,'ma200':ma}))
        histories[code]=tuple(out);native_rows[code]={r['date']:r for r in native[code]}
    coverage=read_explicit_json(read_bound(root,root/'after-download/calendar-coverage.json',refs))
    annual_blob=read_bound(root,root/'calendar.json',refs);annual=read_explicit_json(annual_blob)
    if coverage['base_calendar_sha256']!=digest(annual_blob):raise ValueError('Market-calendar source hash mismatch')
    sessions=tuple(date.fromisoformat(d) for d in coverage['actual_market_days'])
    if sessions!=tuple(sorted(set(sessions))) or sessions[0]!=date(2020,1,2) or sessions[-1]!=END:
        raise ValueError('Explicit completed-history market calendar range mismatch')
    weekly={};calendar_publication={}
    for year in range(START.year,END.year+1):
        sources=[s for s in annual['sources'] if s['year']==year]
        if {s['exchange'] for s in sources}!={'SSE','SZSE'} or len(sources)!=2:raise ValueError('Both exchange annual notices required')
        calendars=[]
        for s in sources:
            closed=set()
            for first,last in s['closed_ranges']:
                d=date.fromisoformat(first)
                while d<=date.fromisoformat(last):closed.add(d);d+=timedelta(days=1)
            d=date(year,1,1);days=[]
            while d.year==year:
                if d.weekday()<5 and d not in closed:days.append(d)
                d+=timedelta(days=1)
            calendars.append(days)
        if calendars[0]!=calendars[1]:raise ValueError('Annual exchange calendars disagree')
        days=calendars[0]
        if tuple(d for d in days if d<=END)!=tuple(d for d in sessions if d.year==year):raise ValueError('Completed date inventory and preannounced calendar disagree')
        pub=max(datetime.combine(date.fromisoformat(s['published_date']),time(23,59,59),TZ) for s in sources)
        for d in days:
            if d>END:continue
            if pub>=stamp(d):raise ValueError('Annual notice was not available at the signal close')
            same_week=[x for x in days if x.isocalendar()[:2]==d.isocalendar()[:2]]
            # Near New Year, include the next/previous year when it belongs to
            # the same ISO week. A Friday in the old year remains a Friday;
            # a December holiday week needs the next-year notice to be known.
            monday=d-timedelta(days=d.weekday());sunday=monday+timedelta(days=6)
            if monday.year!=year or sunday.year!=year:
                for other in annual['sources']:
                    if other['exchange']!='SSE' or other['year'] not in {monday.year,sunday.year} or other['year']==year:continue
                    if not any((monday+timedelta(days=i)).year==other['year'] and (monday+timedelta(days=i)).weekday()<5 for i in range(7)):continue
                    other_pub=datetime.combine(date.fromisoformat(other['published_date']),time(23,59,59),TZ)
                    if other_pub>stamp(d):raise ValueError('Cross-year week needs an announcement not yet public')
                    for offset in range(7):
                        x=monday+timedelta(days=offset)
                        if x.year==other['year'] and x.weekday()<5 and not any(date.fromisoformat(a)<=x<=date.fromisoformat(b) for a,b in other['closed_ranges']):same_week.append(x)
                    pub=max(pub,other_pub)
            weekly[d]=d==max(same_week);calendar_publication[d]=pub
    benchmark_path=root/'portfolio14-20/hs300-official-market-days-20200102-20261023.csv'
    benchmark_blob=read_bound(root,benchmark_path,refs)
    benchmark_audit=read_explicit_json(read_bound(root,root/'portfolio14-20/official-benchmark-audit.json',refs))
    if digest(benchmark_blob)!=benchmark_audit['normalized_sha256']:raise ValueError('Official benchmark normalized hash mismatch')
    for s in benchmark_audit['sources']:
        if digest(read_bound(root,root/'portfolio14-20'/s['path'],refs))!=s['sha256']:raise ValueError('Official benchmark raw hash mismatch')
    benchmark=[]
    for r in csv.DictReader(io.StringIO(benchmark_blob.decode('utf-8-sig'))):
        d=date.fromisoformat(r['date']);prices=[Decimal(r[k]) for k in ('open','high','low','close')]
        if any(not p.is_finite() or p<=0 for p in prices):raise ValueError('Invalid benchmark OHLC')
        benchmark.append(PublishedDailyBar(BENCHMARK,Bar(d,*prices,0),stamp(d),stamp(d),prices[-1],f"CSI normalized:{digest(benchmark_blob)}"))
    if tuple(r.bar.date for r in benchmark)!=sessions:raise ValueError('Official benchmark does not cover every market session')
    histories[BENCHMARK]=tuple(benchmark)
    recheck(root,refs)
    return {'histories':histories,'native_rows':native_rows,'sessions':sessions,'weekly':weekly,
        'calendar_publication':calendar_publication,'source_refs':refs,'native_bindings':bindings,
        'benchmark_mode_available':'mathematical_mean','calendar_scope':'2020 completed-history inventory; 2021+ annual preannounced weekly calendar'}


def fact_tables(root,config,refs):
    tables=[];errors=[]
    for item in config.get('fact_bundles',[]):
        path=(root/item['path']).resolve();blob=read_bound(root,path,refs)
        if digest(blob)!=item['sha256']:raise ValueError('Explicit fact-bundle hash mismatch')
        raw=read_explicit_json(blob)
        # Retain every underlying source, even when the production loader
        # correctly rejects a pending acquired-publication-scope bundle.
        for source in raw.get('sources',[]):
            p=(path.parent/source['path']).resolve()
            if not p.is_relative_to(path.parent) or digest(read_bound(root,p,refs))!=source['sha256']:
                raise ValueError('Fact-bundle underlying source hash/path mismatch')
        try:tables.append(load_verified_fact_bundle(path))
        except ValueError as exc:errors.append({'path':item['path'],'reason':str(exc)})
    return PointInTimeTable((f for t in tables for f in t._facts),(c for t in tables for c in t._coverage)),errors


def is_confirmed(config,sid):
    return (config.get('strategy_id')==sid and config.get('status')=='confirmed'
        and config.get('formal_returns_permitted') is True
        and config.get('confirmation',{}).get('supplementary_modeled_backtest') is True
        and config.get('confirmation',{}).get(f'S{sid}_local_portfolio_rules') is True)


def configuration_decimal(values,name):
    return explicit_decimal(values[name],name)


def approved_native_scope(root,config,refs):
    bound=config['native_configuration_binding'];p=root/bound['path'];blob=read_bound(root,p,refs)
    if digest(blob)!=bound['sha256']:raise ValueError('Previously confirmed native configuration hash mismatch')
    native=read_explicit_json(blob)['client_configuration']
    if (native['start']!=str(START) or native['end']!=str(END) or native['buy_fee_rate']!='0.0005'
        or native['sell_fee_rate']!='0.0015' or native['slippage_minimum_ticks']!=0 or native['force_end_close_included'] is not True
        or native['price_adjustment']!='\u524d\u590d\u6743' or native['entry_price']!='\u6b21\u5468\u671f\u5f00\u76d8\u4ef7' or native['exit_price']!='\u6b21\u5468\u671f\u5f00\u76d8\u4ef7'):
        raise ValueError('Approved batch native dates, price, fees or terminal convention differ')
    return native


def validate_configuration(root,config,config_path,sid,refs):
    if not is_confirmed(config,sid):raise ValueError('Selected strategy parameters/local supplementary model remain pending')
    approval=config['approval_evidence'];p=(config_path.parent/approval['path']).resolve()
    if digest(read_bound(root,p,refs))!=approval['sha256']:raise ValueError('Approval evidence hash mismatch')
    native=approved_native_scope(root,config,refs)
    v=config['confirmed_values']
    if config.get('start')!=str(START) or config.get('end')!=str(END) or set(v['universe'])!=EXPECTED or len(v['universe'])!=len(EXPECTED):raise ValueError('Requested range or exact watchlist mismatch')
    if (configuration_decimal(v,'initial_cash')!=Decimal('1000000') or configuration_decimal(v,'buy_fee_rate')!=Decimal('.0005')
        or configuration_decimal(v,'sell_fee_rate')!=Decimal('.0015') or configuration_decimal(v,'slippage_bps')!=0 or v['price_basis']!=PRICE_BASIS):
        raise ValueError('Selected supported shared-cash proposal or approved fee/slippage/price basis mismatch')
    if (v['fee_rounding']!='cent_half_up' or v['execution_price_precision']!='native_export_decimal'
        or v['daily_bar_availability']!='completed_close_model_current_retrieval_vintage'
        or v['missing_reliable_inputs']!='exclude_candidate_no_current_value_fallback'
        or v['next_open_definition']!='next_market_session_with_explicit_quote'
        or v['terminal_policy'] not in ('mark','force_close') or type(v['terminal_respects_settlement']) is not bool):
        raise ValueError('Explicit implemented supplementary execution/data conventions required')
    if v['terminal_policy']!='force_close' and not config.get('explicit_model_departures'):raise ValueError('Mark-only departure from native force-end policy must be documented')
    if set(v['lot_sizes'])!=EXPECTED:raise ValueError('Per-security share lot sizes required; no default unit')
    fields={k:v[k] for k in ('max_positions','lot_sizes','settlement_market_sessions','insufficient_cash','failed_order_policy','price_basis','corporate_action_policy')}
    for k in ('initial_cash','buy_fee_rate','sell_fee_rate','minimum_buy_fee','minimum_sell_fee','slippage_bps'):fields[k]=configuration_decimal(v,k)
    execution=PortfolioExecutionConfig(**fields,data_kind='verified_historical')
    if execution.max_positions!={14:3,20:5}[sid]:raise ValueError('Strategy maximum position count mismatch')
    rules=PortfolioRules(tuple(sorted(EXPECTED)),'confirmed',f"sha256:{approval['sha256']}",'native_export')
    if sid==14:
        strategy=CumulativeRsiPortfolio(rules,minimum_market_cap_cny=configuration_decimal(v,'minimum_market_cap_cny'),lookback_boundary=v['liquidity_lookback_boundary'],share_fact_semantics=v['share_fact_semantics'])
    else:
        if v['benchmark_symbol']!=BENCHMARK or v['benchmark_indicator_mode']!='mathematical_mean':raise ValueError('Available CSI input supports explicit mathematical_mean only; no native fallback')
        strategy=WeeklyRsiRotation(rules,benchmark_symbol=BENCHMARK,benchmark_indicator_mode=v['benchmark_indicator_mode'])
    return execution,strategy,canonical_hash(native)


def scoped_authorizer(permissions,values,native_configuration_sha,gaps):
    mapping=values.get('native_ledger_quantity_share_multipliers')
    if permissions.kind=='native_model_observed_execution':
        if not isinstance(mapping,dict) or set(mapping)!=EXPECTED or any(type(n) is not int or n<=0 for n in mapping.values()):
            raise ValueError('Native execution witness requires explicit positive shares per native-ledger quantity for every stock')
    def check(scope):
        quantity=scope.quantity;unit='shares'
        if permissions.kind=='native_model_observed_execution':
            if quantity%mapping[scope.symbol]:
                gaps.append({**asdict(scope),'reason':'share_quantity_not_integral_in_explicit_native_ledger_unit'});return None
            quantity//=mapping[scope.symbol];unit='native_ledger_quantity'
        allowed=permissions.authorize(scope.execution_date,scope.symbol,scope.phase,scope.side,quantity,
            quantity_unit=unit,native_configuration_canonical_sha256=native_configuration_sha,execution_price=scope.price)
        if allowed is None:gaps.append({**asdict(scope),'reason':'unknown_exact_execution_permission_or_native_model_witness'})
        return allowed
    return check


def execute(data,config,strategy,permissions,facts,values,native_configuration_sha,gaps):
    sessions=tuple(d for d in data['sessions'] if START<=d<=END);opens={};marks={}
    for day in sessions:
        opens[day]={};marks[day]={}
        for code in sorted(EXPECTED):
            row=data['native_rows'][code].get(day)
            if row is None:continue
            p=permissions.get(day,code,'open')
            opens[day][code]=OpenQuote(row['OX'],p.buy_allowed if p else None,p.sell_allowed if p else None)
            marks[day][code]=row['CX']
    def context(day,nav,held):
        return PortfolioCloseContext(day,stamp(day),data['histories'],data['sessions'],data['sessions'][0],
            data['calendar_publication'][day],'calendar.json annual notices; 2020 completed-session inventory',
            data['weekly'][day],nav,held,facts,PRICE_BASIS,'CNY')
    terminal={}
    for code in sorted(EXPECTED):
        row=data['native_rows'][code].get(END)
        if row is None:continue
        p=permissions.get(END,code,'close')
        terminal[code]=OpenQuote(row['CX'],p.buy_allowed if p else None,p.sell_allowed if p else None)
    return run_shared_portfolio(config,sessions,opens,marks,strategy,context,
        source_validation_status='verified_historical_ready',terminal_policy=values['terminal_policy'],
        terminal_quotes=terminal,terminal_respects_settlement=values['terminal_respects_settlement'],
        order_authorizer=scoped_authorizer(permissions,values,native_configuration_sha,gaps))


def run(*,sid,config_path,permission_csv=None,permission_metadata=None,out,mode='preflight',root=BASE):
    root=Path(root).resolve();config_path=Path(config_path).resolve();out=Path(out).resolve()
    if sid not in (14,20) or mode not in ('preflight','modeled-backtest'):raise ValueError('Only explicit S14/S20 adapter modes supported')
    if not out.is_relative_to(root) or out.exists():raise ValueError('New immutable-attempt output directory required inside report root')
    implementation_refs=[]
    for name in ('portfolio_native_runner.py','native_execution_permissions.py','scale_in_native_preflight.py'):
        p=root/name;read_bound(root,p,implementation_refs)
    for name in ('portfolio.py','portfolio_strategies.py','point_in_time.py','models.py','data.py'):
        p=root.parent.parent/'src/agent_stocking'/name;b=p.read_bytes()
        implementation_refs.append({'path':str(p.relative_to(root.parent.parent)),'sha256':digest(b),'bytes':len(b),'repository_relative':True})
    data=assemble_inputs(root);refs=data['source_refs']+implementation_refs;blob=read_bound(root,config_path,refs);config=read_explicit_json(blob)
    if config.get('strategy_id')!=sid:raise ValueError('Config is not for the selected strategy')
    approved_native_scope(root,config,refs)
    facts,fact_errors=fact_tables(root,config,refs) if sid==14 else (PointInTimeTable(()),[])
    if bool(permission_csv)!=bool(permission_metadata):raise ValueError('Permission CSV and metadata must be supplied together')
    permissions=None;permission_error=None
    if permission_csv:
        try:permissions=load_permission_csv(permission_csv,permission_metadata,allowed_symbols=EXPECTED,price_basis=PRICE_BASIS,evidence_root=root)
        except (ValueError,KeyError,OSError) as exc:permission_error=str(exc)
    if permissions:
        for p,expected in [(permissions.csv_path,permissions.csv_sha256),(permissions.metadata_path,permissions.metadata_sha256)]+[(Path(s['resolved_path']),s['sha256']) for s in permissions.evidence]:
            if digest(read_bound(root,p,refs))!=expected:raise ValueError('Permission evidence changed after loader validation')
    gaps=[];counts={};unknown=[]
    for code,rows in data['native_rows'].items():
        active=[d for d in rows if START<=d<=END];counts[code]={'full_rows':len(rows),'active_rows':len(active)}
        for d in active:
            for phase in ('open','close'):
                p=permissions.get(d,code,phase) if permissions else None
                if p is None or p.buy_allowed is None or p.sell_allowed is None:unknown.append({'date':str(d),'symbol':code,'phase':phase,'reason':'unknown_inventory_not_a_required_order'})
    result={'schema_version':1,'kind':'actual_native_inputs_supplementary_portfolio_preflight' if mode=='preflight' else 'supplementary_modeled_portfolio_attempt_not_native_export',
        'created_at':datetime.now(TZ).isoformat(timespec='seconds'),
        'strategy_id':sid,'mode':mode,'status':'inputs_verified_no_replay','configuration_status':config.get('status'),
        'selected_rules_confirmed':is_confirmed(config,sid),'native_source_sha256':SOURCE_SHA,'price_basis':PRICE_BASIS,
        'requested_range':[str(START),str(END)],'securities':counts,'full_native_rows':sum(v['full_rows'] for v in counts.values()),
        'active_rows':sum(v['active_rows'] for v in counts.values()),'market_sessions':len(data['sessions']),
        'evaluation_market_sessions':sum(START<=d<=END for d in data['sessions']),
        'preannounced_week_end_count':sum(data['weekly'].values()),'calendar_scope':data['calendar_scope'],
        'benchmark_symbol':BENCHMARK,'benchmark_available_mode':'mathematical_mean','benchmark_rows':len(data['histories'][BENCHMARK]),
        'fact_bundle_count':len(config.get('fact_bundles',[])) if sid==14 else 0,'fact_loader_errors':fact_errors,
        'permission_kind':permissions.kind if permissions else None,'permission_validation_error':permission_error,
        'permission_unknown_inventory_count':len(unknown),'inventory_is_not_a_requirement_for_every_bar':True,
        'permission_not_inferred_from_OHLCV':True,'orders_generated':False,'fills_generated':False,'formal_returns':None,
        'python_version':platform.python_version(),'implementation':'existing_run_shared_portfolio_with_scoped_authorizer',
        'limits':['Current native FQ1 retrieval vintage; not historical-vintage company-action identity.',
            'Daily close completion timestamps are an explicit replay model, not original source publication observations.',
            'Current watchlist selection/survival and censored historical signals remain.',
            'Latest-published-share research scope is distinct from actual registered-share coverage.']}
    blocked=None;replay=None;interrupted_snapshot=None
    if mode=='modeled-backtest':
        try:
            execution,strategy,native_sha=validate_configuration(root,config,config_path,sid,refs)
            if fact_errors:raise ValueError('Selected S14 fact bundles have pending/unverified sources')
            if sid==14 and not config.get('fact_bundles'):raise ValueError('Explicit S14 fact bundles required')
            if permissions is None:raise ValueError('Valid explicit permission input required; no presumed true permissions')
            replay=execute(data,execution,strategy,permissions,facts,config['confirmed_values'],native_sha,gaps)
        except (ValueError,KeyError) as exc:
            blocked=str(exc);result['status']='blocked_no_formal_financial_result';result['blocking_reason']=blocked
            if getattr(exc,'portfolio_snapshot',None) is not None:
                interrupted_snapshot=asdict(exc.portfolio_snapshot)
                result['interrupted_execution_audit_saved']=True
                result['orders_generated']=bool(interrupted_snapshot.get('orders'))
                result['fills_generated']=bool(interrupted_snapshot.get('fills'))
        else:
            result['status']='supplementary_modeled_backtest_completed';result['orders_generated']=bool(replay.orders);result['fills_generated']=bool(replay.fills)
            result['formal_returns']=(replay.equity_curve[-1].equity-execution.initial_cash)/execution.initial_cash
            result['financial_result_kind']='explicit_supplementary_model_not_TDX_native_or_broker_fills'
    source_errors=[]
    for item in refs:
        p=(root.parent.parent if item.get('repository_relative') else root)/item['path']
        try:observed=digest(p.read_bytes());error=None
        except OSError as exc:observed=None;error=str(exc)
        if observed!=item['sha256']:source_errors.append({'path':item['path'],'repository_relative':bool(item.get('repository_relative')),'expected_sha256':item['sha256'],'observed_sha256':observed,'read_error':error})
    if permissions:
        try:permissions.verify_immutable()
        except (ValueError,OSError) as exc:source_errors.append({'permission_revalidation_error':str(exc)})
    if source_errors:
        result['status']='invalid_source_no_formal_financial_result';result['formal_returns']=None
        result.pop('financial_result_kind',None)
        result['blocking_reason']='Source evidence changed or became unreadable before publication'
        if replay is not None:
            interrupted_snapshot={'kind':'engine_committed_audit_invalid_for_financial_publication','committed_state':asdict(replay)}
            replay=None;result['interrupted_execution_audit_saved']=True
        result['source_revalidation_errors']=source_errors
    result['sources']=refs;result['configuration']=config;result['all_sources_rechecked_before_publication']=not source_errors
    out.mkdir(parents=True)
    with (out/'unknown-permission-inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=('date','symbol','phase','reason'));w.writeheader();w.writerows(unknown)
    save_json(out/'actual-order-permission-gaps.json',gaps)
    if interrupted_snapshot is not None:save_json(out/'interrupted-committed-state-not-a-financial-result.json',interrupted_snapshot)
    if replay is not None:save_json(out/'modeled-replay.json',asdict(replay))
    result['artifacts']=[source_record(root,p,p.read_bytes()) for p in sorted(out.iterdir())]
    save_json(out/'preflight.json' if mode=='preflight' else out/'attempt.json',result)
    print(json.dumps({k:result[k] for k in ('strategy_id','status','full_native_rows','active_rows','permission_unknown_inventory_count','formal_returns')},ensure_ascii=True,default=str))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--strategy',type=int,choices=(14,20),required=True)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--permission-csv',type=Path);p.add_argument('--permission-metadata',type=Path)
    p.add_argument('--mode',choices=('preflight','modeled-backtest'),default='preflight');a=p.parse_args()
    r=run(sid=a.strategy,config_path=a.config,permission_csv=a.permission_csv,permission_metadata=a.permission_metadata,out=a.out,mode=a.mode)
    return 2 if r['status'] in ('blocked_no_formal_financial_result','invalid_source_no_formal_financial_result') else 0


if __name__=='__main__':raise SystemExit(main())
