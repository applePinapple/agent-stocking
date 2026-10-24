"""Audit native precision-v2 S21 indicators/signals/prices. No financial replay.

The scaled export saves existing native precision. It is not a claim of newly
created market precision or cross-platform bit identity. Older evidence stays.
"""
import argparse
import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import io
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

BASE=Path(__file__).resolve().parent
PROJECT=BASE.parent.parent
sys.path.insert(0,str(BASE))
sys.path.insert(0,str(PROJECT/'src'))
from audit_strategy21_reference import calculate, projected_actions
from agent_stocking.data import load_tdx_day

D=Decimal
SCALE=D(1048576)
SOURCE_SHA='f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e'
FIELDS=['FQ','BC','OX','HX','LX','CX','R2X','R4X','M200X','CAPSH','SARX','TURN3','POPX','FCX','AMT']
UNSCALED={'FQ','BC','CAPSH','TURN3','AMT'}

def f32(v):
    return struct.unpack('<f',struct.pack('<f',float(v)))[0]

def f32_ulp(v):
    value=abs(f32(v))
    n=struct.unpack('<I',struct.pack('<f',value))[0]
    return D(str(struct.unpack('<f',struct.pack('<I',n+1))[0]))-D(str(value))

def decode_export(path):
    blob=path.read_bytes()
    table=list(csv.reader(io.StringIO(blob.decode('gb18030')),delimiter='\t'))
    hi=next(i for i,r in enumerate(table) if all(any(x.strip()=='CHECKALL.'+k for x in r) for k in FIELDS))
    headers=[s.strip() for s in table[hi]]
    indices={k:headers.index('CHECKALL.'+k) for k in FIELDS}
    rows=[]
    for line in table[hi+1:]:
        if not line or '/' not in line[0]:continue
        assert len(line)>max(indices.values()),'incomplete native row'
        day=line[0].strip().replace('/','-');date.fromisoformat(day)
        row={'date':day}
        for k,i in indices.items():
            text=line[i].strip()
            value=D(text) if text else None
            # -1 is an unscaled invalid-RSI sentinel; never divide it or
            # reinterpret it as a negative oversold observation.
            if value is not None and k not in UNSCALED and not (k in {'R2X','R4X'} and value==-1):value/=SCALE
            row[k]=value
        for k in ['R2X','R4X']:
            assert row[k] is None or row[k]==-1 or 0<=row[k]<=100,(day,k,row[k])
        assert row['FQ']==1 and row['BC']==len(rows),(day,'FQ or BC discontinuity')
        assert all(row[k] is not None for k in ['OX','HX','LX','CX'])
        assert row['LX']<=min(row['OX'],row['CX'])<=max(row['OX'],row['CX'])<=row['HX']
        rows.append(row)
    assert rows and len({r['date'] for r in rows})==len(rows)
    assert [r['date'] for r in rows]==sorted(r['date'] for r in rows)
    return blob,rows

def terminal_lifecycle(*,held_after_last_signal,terminal_positions,ledger_rows,force_end_close):
    assert type(force_end_close) is bool
    expected=int(held_after_last_signal and force_end_close)
    actual=len(terminal_positions)
    last=(not terminal_positions or terminal_positions==[ledger_rows-1])
    return {'force_end_close_configured':force_end_close,'held_after_last_strategy_signal':held_after_last_signal,'expected_terminal_count':expected,'observed_terminal_count':actual,'terminal_positions_0based':terminal_positions,'terminal_at_most_one_and_last':actual<=1 and last,'matches':actual==expected and actual<=1 and last}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code',required=True,choices=['300308','300750','601899','603728','605488','688017','688322'])
    parser.add_argument('--input',required=True,help='Actual native v2 export path; headers remain CHECKALL.')
    args=parser.parse_args()
    source=BASE/'native-evaluation/CHECKALL-precision-v2.txt'
    source_blob=source.read_bytes()
    reference_path=BASE/'audit_strategy21_reference.py'
    reference_blob=reference_path.read_bytes()
    script_blob=Path(__file__).read_bytes()
    strategy_source=BASE/'native-evaluation/per-strategy/S21-expert.txt'
    strategy_blob=strategy_source.read_bytes()
    assert sha256(source_blob).hexdigest()==SOURCE_SHA,'immutable v2 source changed'
    path=Path(args.input).resolve();blob,rows=decode_export(path)
    assert args.code in blob.decode('gb18030').splitlines()[0],'native title code mismatch'
    provenance_path=BASE/'native-inputs/export-provenance.jsonl'
    provenance_blob=provenance_path.read_bytes()
    provenance=[json.loads(line) for line in provenance_blob.decode('utf-8-sig').splitlines() if line.strip()]
    binding=[r for r in provenance if r.get('path')==str(path.relative_to(BASE)).replace('\\','/') and r.get('sha256')==sha256(blob).hexdigest()]
    assert binding,'missing matching root native-input export provenance'
    for r in binding:
        assert r.get('source_sha256')==SOURCE_SHA
        assert r.get('source',r.get('native_indicator_source'))=='native-evaluation/CHECKALL-precision-v2.txt'
        assert r.get('code',r.get('security'))==args.code and r.get('scale')==1048576
    binding_record=binding[-1]
    out=BASE/'native-evaluation/S21-precision-v2'/args.code;out.mkdir(parents=True,exist_ok=True)
    manifest_path=BASE/'after-download/input-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    inputrow=next(r for r in manifest['securities'] if r['security_code']==args.code)
    raw_path=BASE/'after-download'/inputrow['snapshot_path']
    raw_blob=raw_path.read_bytes()
    assert sha256(raw_blob).hexdigest()==inputrow['snapshot_sha256']
    stored=load_tdx_day(raw_path)
    assert [r['date'] for r in rows]==[str(b.date) for b in stored],'native v2 date coverage differs from saved raw date inventory'
    bars=[SimpleNamespace(date=r['date'],open=r['OX'],high=r['HX'],low=r['LX'],close=r['CX']) for r in rows]
    reference=calculate(bars,count_origin=0)
    comparisons=[]
    for i,(native,ref) in enumerate(zip(rows,reference)):
        p={'date':native['date'],'index_0based':i,'BC':native['BC'],'open_native':native['OX'],'high_native':native['HX'],'low_native':native['LX'],'close_native':native['CX'],'RSI2_native':native['R2X'],'RSI4_native':native['R4X'],'MA200_native':native['M200X'],'CAPSH_native':native['CAPSH'],'AMOUNT_native':native['AMT'],'SQ_independent':ref['sq'],'buy_independent':ref['buy'],'sell_independent':ref['sell']}
        for label,key,value in [('POP','POPX',ref['std']),('FC','FCX',ref['fc'])]:
            observed=native[key]
            p[label+'_native']=observed;p[label+'_independent_Decimal']=value
            p[label+'_native_minus_Decimal']=observed-value if value is not None and observed is not None else None
            p[label+'_same_2dp']=observed.quantize(D('.01'),rounding=ROUND_HALF_UP)==value.quantize(D('.01'),rounding=ROUND_HALF_UP) if value is not None and observed is not None else None
            p[label+'_same_scaled_2dp']=((observed*SCALE).quantize(D('.01'),rounding=ROUND_HALF_UP)==(value*SCALE).quantize(D('.01'),rounding=ROUND_HALF_UP)) if value is not None and observed is not None else None
            p[label+'_error_in_independent_float32_ULP']=abs(observed-value)/f32_ulp(value) if value not in {None,D(0)} and observed is not None else None
        comparisons.append(p)
    metrics={}
    for label in ['POP','FC']:
        used=[r for r in comparisons if r[label+'_independent_Decimal'] is not None]
        metrics[label]={'complete_window_rows':len(used),'same_2_decimal_rows':sum(r[label+'_same_2dp'] is True for r in used),'same_scaled_2_decimal_rows':sum(r[label+'_same_scaled_2dp'] is True for r in used),'max_abs_difference':max((abs(r[label+'_native_minus_Decimal']) for r in used if r[label+'_native_minus_Decimal'] is not None),default=None),'max_difference_in_float32_ULP':max((r[label+'_error_in_independent_float32_ULP'] for r in used if r[label+'_error_in_independent_float32_ULP'] is not None),default=None),'display_2dp_mismatch_dates':[r['date'] for r in used if r[label+'_same_2dp'] is not True],'note':'Float32-ULP distances are descriptive, not a post-hoc pass tolerance. All row differences remain in CSV.'}
        after_initial=[r for r in used if label!='POP' or r['index_0based']>19]
        metrics[label]['after_initial_STDP_boundary']={'rows':len(after_initial),'same_2_decimal_rows':sum(r[label+'_same_2dp'] is True for r in after_initial),'max_abs_difference':max((abs(r[label+'_native_minus_Decimal']) for r in after_initial if r[label+'_native_minus_Decimal'] is not None),default=None),'same_scaled_2_decimal_rows':sum(r[label+'_same_scaled_2dp'] is True for r in after_initial)}
    # Replay only one-position signal filtering, never cash/quantities/returns.
    actions,held=projected_actions(reference)
    config_path=BASE/'run-manifest.json'
    config=json.loads(config_path.read_text(encoding='utf-8'))['client_configuration']
    force_end=config['force_end_close_included']
    assert type(force_end) is bool,'force-close policy must be explicit configuration'
    ledger_path=BASE/'native-results/S21/native-trades-all.xls'
    ledger_blob=ledger_path.read_bytes()
    table=list(csv.reader(io.StringIO(ledger_blob.decode('gb18030')),delimiter='\t'))
    ledger=[r for r in table[1:] if r and r[0]==args.code]
    labels={'\u4e70\u5f00':'buy','\u5356\u5e73':'sell'}
    native_actions=[];price_checks=[];terminal_positions=[]
    index={r['date']:i for i,r in enumerate(rows)}
    for ledger_position,line in enumerate(ledger):
        signal_day=line[2][:10];i=index[signal_day];side=line[3]
        if side in labels:
            native_actions.append((signal_day,labels[side]))
            assert i+1<len(rows),'native ordinary fill lacks next exported session'
            fill_day=rows[i+1]['date'];expected=rows[i+1]['OX'];kind='next_exported_session_open'
        elif side=='\u5e73\u76d8':
            assert i==len(rows)-1,'terminal native row not at final exported date'
            terminal_positions.append(ledger_position)
            fill_day=signal_day;expected=rows[i]['CX'];kind='native_terminal_close_not_a_strategy_exit'
        else:raise ValueError('Unknown native trade type '+repr(side))
        shown=D(line[4]);price_checks.append({'signal_date':signal_day,'native_type':side,'native_price_display_2dp':shown,'expected_fill_session':fill_day,'native_export_fill_price':expected,'price_basis':'actual_v2_front_adjusted_export','comparison_kind':kind,'same_2dp':shown==expected.quantize(D('.01'),rounding=ROUND_HALF_UP),'amount_quantity_fee_recomputed':False})
    terminal=terminal_lifecycle(held_after_last_signal=held,terminal_positions=terminal_positions,ledger_rows=len(ledger),force_end_close=force_end)
    temporal=[]
    for cutoff in sorted({19,38,40,200,len(bars)-2}):
        prefix=calculate(bars[:cutoff+1],count_origin=0)
        modified=bars[:cutoff+1]+[SimpleNamespace(date=b.date,open=b.open*3+7,high=b.high*3+7,low=b.low*3+7,close=b.close*3+7) for b in bars[cutoff+1:]]
        changed=calculate(modified,count_origin=0)
        assert prefix==reference[:cutoff+1]==changed[:cutoff+1]
        temporal.append({'cutoff':bars[cutoff].date,'truncation_equal':True,'synthetic_future_perturbation_equal':True})
    # Check all computational/source snapshots BEFORE publishing complete
    # derivatives. A changed input must never leave a new completed audit.
    assert path.read_bytes()==blob,'native input changed during audit'
    assert ledger_path.read_bytes()==ledger_blob,'native ledger changed during audit'
    assert source.read_bytes()==source_blob,'CHECKALL source changed during audit'
    assert strategy_source.read_bytes()==strategy_blob,'S21 source changed during audit'
    assert reference_path.read_bytes()==reference_blob,'reference source changed during audit'
    assert Path(__file__).read_bytes()==script_blob,'audit source changed during audit'
    assert raw_path.read_bytes()==raw_blob,'date inventory changed during audit'
    assert json.loads(config_path.read_text(encoding='utf-8'))['client_configuration']==config,'confirmed configuration changed during audit'
    csv_path=out/'values.csv'
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(comparisons[0]));w.writeheader();w.writerows(comparisons)
    report={'status':'actual_native_high_precision_comparison_with_all_differences_retained','security_code':args.code,'native_input_path':str(path.relative_to(BASE)),'native_input_sha256':sha256(blob).hexdigest(),'native_export_UI_name':'CHECKALL','native_source_path':str(source.relative_to(BASE)),'native_source_sha256':SOURCE_SHA,'source_binding_limit':'UI owner identifies saved v2 source; text headers alone do not encode a source hash. Keep UI source-binding evidence separately.','scale':str(SCALE),'unscaled_RSI_invalid_sentinel':'-1','rows':len(rows),'date_range':[rows[0]['date'],rows[-1]['date']],'FQ_all_rows':1,'BC_origin':0,'raw_inventory_used_only_for_dates_and_coverage':{'path':str(raw_path.relative_to(BASE)),'sha256':inputrow['snapshot_sha256']},'metrics':metrics,'native_actions':native_actions,'independent_actions_from_native_OHLC':actions,'all_nonterminal_signal_dates_equal':actions==native_actions,'independent_signal_position_still_open':held,'native_trade_rows':len(ledger),'native_ledger_sha256':sha256(ledger_blob).hexdigest(),'price_checks':price_checks,'all_native_display_prices_match_v2_fill_session_price':bool(price_checks) and all(r['same_2dp'] for r in price_checks),'temporal_tests':{'kind':'testing_only_not_market_results','cases':temporal,'comparisons':len(temporal)*2},'limitations':['No claim of hidden-bit numeric identity; numeric row errors retained','Current native front-adjustment vintage is the user-selected basis; temporal model tests do not prove historical corporate-action availability','Current-watchlist selection has survivorship/selection bias and native terminal/unfinished signals retain their original censoring scope','No inferred execution permissions and no recomputed cash, quantities, fees or financial returns','Two-decimal older evidence retained unchanged; cross-security RSI ranking needs its own precision and source audit'],'values_csv_sha256':sha256(csv_path.read_bytes()).hexdigest(),'reference_script_sha256':sha256((BASE/'audit_strategy21_reference.py').read_bytes()).hexdigest(),'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    report['native_source_binding']={'provenance_path':str(provenance_path.relative_to(BASE)),'provenance_sha256_at_read':sha256(provenance_blob).hexdigest(),'matching_record':binding_record,'matching_record_canonical_sha256':sha256(json.dumps(binding_record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest(),'source_file_hash_checked':True,'input_file_hash_checked':True}
    report['S21_formula_sha256']=sha256(strategy_blob).hexdigest()
    report['source_immutability_rechecked_before_publication']=True
    report['reference_script_sha256']=sha256(reference_blob).hexdigest()
    report['script_sha256']=sha256(script_blob).hexdigest()
    report['terminal_lifecycle']=terminal
    report['terminal_configuration_evidence']={'path':str(config_path.relative_to(BASE)),'client_configuration_canonical_sha256':sha256(json.dumps(config,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest(),'scope':'UI-owner-confirmed batch configuration, not an engine default'}
    report['signals_terminal_and_prices_all_match']=report['all_nonterminal_signal_dates_equal'] and terminal['matches'] and report['all_native_display_prices_match_v2_fill_session_price']
    (out/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({'code':args.code,'rows':len(rows),'metrics':metrics,'native_trade_rows':len(ledger),'signal_dates_equal':actions==native_actions,'all_fill_display_prices_equal':report['all_native_display_prices_match_v2_fill_session_price']},default=str))

if __name__=='__main__':main()
