"""Read native CHECKALL export; compare displayed S21 values, never returns."""
import csv
import argparse
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from audit_strategy21_reference import calculate, projected_actions

D=Decimal
OUT=BASE/'native-evaluation/S21-reference'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--code',choices=['688322','688017'],default='688322')
    args=parser.parse_args()
    code=args.code
    filename='688322-CHECKALL-screen-probe.txt' if code=='688322' else '688017-CHECKALL.txt'
    path=BASE/'native-inputs'/filename
    blob=path.read_bytes()
    table=list(csv.reader(io.StringIO(blob.decode('gb18030')),delimiter='\t'))
    head_index=next(i for i,r in enumerate(table) if any('CHECKALL.FC21' in x for x in r))
    headers=[x.strip() for x in table[head_index]]
    columns={k:next(i for i,x in enumerate(headers) if x=='CHECKALL.'+k) for k in ['FQ','BC','LD','UD','SEQD','SEQPOP','SEQSTD','POP21','FC21']}
    rawrows=[r for r in table[head_index+1:] if r and '/' in r[0] and len(r)>=max(columns.values())+1]
    from types import SimpleNamespace
    bars=[SimpleNamespace(date=r[0].strip().replace('/','-'),open=D(r[1].strip()),high=D(r[2].strip()),low=D(r[3].strip()),close=D(r[4].strip())) for r in rawrows]
    reference=calculate(bars,count_origin=0)
    points=[]
    checks={k:[] for k in ['SEQD','SEQPOP','SEQSTD','POP21','FC21']}
    seqpop=(D(665)/20).sqrt()
    seqstd=(D(665)/19).sqrt()
    for i,(raw,row) in enumerate(zip(rawrows,reference)):
        point={'date':row['date'],'index_0based':i}
        for k,col in columns.items():
            point[k+'_native']=D(raw[col].strip()) if raw[col].strip() else None
        assert point['FQ_native']==1
        assert point['BC_native']==i
        assert point['LD_native']==len(bars)==point['UD_native']
        expected={'SEQD':D(0) if i>=19 else None,'SEQPOP':seqpop if i>=19 else None,'SEQSTD':seqstd if i>=19 else None,'POP21':row['std'],'FC21':row['fc']}
        for k,value in expected.items():
            observed=point[k+'_native']
            point[k+'_independent']=value
            if value is None:
                point[k+'_comparison']='outside_complete_window'
                continue
            equal=observed is not None and value.quantize(D('.01'),rounding=ROUND_HALF_UP)==observed
            point[k+'_comparison']='equal_at_2_decimal_display' if equal else 'display_mismatch'
            checks[k].append({'date':row['date'],'index_0based':i,'native':observed,'independent':value,'rounded':value.quantize(D('.01'),rounding=ROUND_HALF_UP),'matches':equal,'abs_difference':abs(observed-value) if observed is not None else None})
        points.append(point)
    summary={k:{'complete_window_rows':len(v),'matching_rows':sum(r['matches'] for r in v),'mismatches':[r for r in v if not r['matches']],'max_abs_difference':max((r['abs_difference'] for r in v if r['abs_difference'] is not None),default=None)} for k,v in checks.items()}
    # Deliberately modified future values are temporal tests only, never market
    # results or replacement input. Compare the full prior indicator/signal dict.
    temporal=[]
    for cutoff in sorted({19,38,40,200,len(bars)-2}):
        prefix=calculate(bars[:cutoff+1],count_origin=0)
        changed=bars[:cutoff+1]+[SimpleNamespace(date=b.date,open=b.open*3+7,high=b.high*3+7,low=b.low*3+7,close=b.close*3+7) for b in bars[cutoff+1:]]
        altered=calculate(changed,count_origin=0)
        assert prefix==reference[:cutoff+1]
        assert altered[:cutoff+1]==reference[:cutoff+1]
        temporal.append({'cutoff':bars[cutoff].date,'prefix_rows':cutoff+1,'truncated_prior_values_and_signals_equal':True,'future_price_perturbation_prior_values_and_signals_equal':True})
    # Compare active entry/exit actions using the exported native OHLC. Formula
    # guards BC>=40/41 are zero-origin in this installation, exactly as exported.
    actions,held=projected_actions(reference)
    ledger_path=BASE/'native-results/S21/native-trades-all.xls'
    ledger_blob=ledger_path.read_bytes()
    ledger=list(csv.reader(io.StringIO(ledger_blob.decode('gb18030')),delimiter='\t'))
    labels={'\u4e70\u5f00':'buy','\u5356\u5e73':'sell'}
    native=[(r[2][:10],labels[r[3]]) for r in ledger[1:] if r and r[0]==code and r[3] in labels]
    outcsv=OUT/(code+'-CHECKALL-value-comparison.csv')
    with outcsv.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(points[0]));w.writeheader();w.writerows(points)
    report={
      'status':'native_display_numeric_comparison_completed_with_explicit_initial_STDP_exception' if all(all(r['index_0based']==19 and k in ['SEQPOP','POP21'] for r in v['mismatches']) for k,v in summary.items()) else 'native_display_compared_discrepancies_retained_need_high_precision_inputs',
      'scope':code+' only; actual native front-adjusted OHLC and CHECKALL values; no precision claim beyond 2-decimal export',
      'native_path':str(path.relative_to(BASE)),'native_sha256':sha256(blob).hexdigest(),
      'native_rows':len(bars),'period':[bars[0].date,bars[-1].date],
      'formula_path':'native-evaluation/CHECKALL-indicator.txt',
      'formula_sha256':sha256((BASE/'native-evaluation/CHECKALL-indicator.txt').read_bytes()).hexdigest(),
      'S21_formula_sha256':sha256((BASE/'native-evaluation/per-strategy/S21-expert.txt').read_bytes()).hexdigest(),
      'all_rows_FQ':1,'BARSCOUNT_origin_observed':0,'LOCALDAYNUM_and_USEDDATANUM_all_rows':len(bars),
      'values':summary,
      'independent_definition':'Decimal precision40; rolling20 population variance and OLS regression at current endpoint; full FC requires20 already complete delta windows, first index38.',
      'initial_STDP_boundary':{'date':bars[19].date,'index':19,'observed_SEQPOP':points[19]['SEQPOP_native'],'observed_POP21':points[19]['POP21_native'],'note':'First complete20-row window compared explicitly, not silently omitted.'},
      'first_STDP_boundary_can_affect_this_S21_signal':False,
      'boundary_signal_reason':'This S21 uses BC>=40 entry/BC>=41 exit. STDP is a rolling primitive here; the initial index19 value is not accessed by those later entry/exit conditions. FC has no STDP dependency.',
      'SEQ_endpoint_diagnostic':'Current endpoint predicts0.00; next-point model predicts1.00. Complete-row counts/mismatches recorded above.',
      'projected_actions_from_native_OHLC':actions,'native_actions':native,
      'all_native_nonterminal_signal_dates_equal':actions==native,
      'projected_held_after_last_close':held,
      'native_ledger_sha256':sha256(ledger_blob).hexdigest(),
      'other_codes_numeric_status':'Each code requires its own native input comparison; no generalization from this one code',
      'execution_permission_inferred':False,'cash_or_returns_recomputed':False,
      'comparison_csv_sha256':sha256(outcsv.read_bytes()).hexdigest(),
      'temporal_tests':{'kind':'explicit_future_price_perturbation_and_truncation_only_not_market_returns','cases':temporal,'comparisons':len(temporal)*2},
      'reference_script_sha256':sha256((BASE/'audit_strategy21_reference.py').read_bytes()).hexdigest(),
      'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (OUT/(code+'-CHECKALL-native-review.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    assert path.read_bytes()==blob and ledger_path.read_bytes()==ledger_blob
    print(json.dumps({'rows':len(bars),'values':{k:{'complete':v['complete_window_rows'],'matches':v['matching_rows'],'mismatches':[(x['date'],str(x['native']),str(x['rounded'])) for x in v['mismatches']]} for k,v in summary.items()},'signal_dates_equal':actions==native},ensure_ascii=True))

if __name__=='__main__':main()
