"""Independent S21 numeric/signal comparison on raw-candidate 688322 only.

No return, cash, quantity or permission model is inferred. Other securities must
use actual native forward-adjusted exports before a like-for-like comparison.
"""
import csv
from decimal import Decimal, getcontext
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

getcontext().prec = 40
D = Decimal
BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent.parent
sys.path.insert(0, str(PROJECT / "src"))
from agent_stocking.data import load_tdx_day

OUT = BASE / "native-evaluation/S21-reference"
CODE = "688322"


def calculate(bars, *, sample_std=False, forecast_ahead=False, count_origin=1):
    data=[]
    deltas=[]
    for i,bar in enumerate(bars):
        row={"date":str(bar.date),"bar_index_0based":i,"open":bar.open,"high":bar.high,"low":bar.low,"close":bar.close,"std":None,"sq":None,"delta":None,"slope":None,"fc":None,"buy":False,"sell":False}
        if i>=19:
            window=bars[i-19:i+1]
            mean=sum((x.close for x in window),D(0))/20
            variance=sum(((x.close-mean)**2 for x in window),D(0))/(19 if sample_std else 20)
            std=variance.sqrt()
            typical=sum(((x.high+x.low+x.close)/3 for x in window),D(0))/20
            spread=sum((x.high-x.low for x in window),D(0))/20
            upper,lower=mean+2*std,mean-2*std
            kcu,kcl=typical+D('1.5')*spread,typical-D('1.5')*spread
            dcm=(max(x.high for x in window)+min(x.low for x in window))/2
            row.update(std=std,sq=upper<kcu and lower>kcl,delta=bar.close-(dcm+mean)/2,bb_upper=upper,bb_lower=lower,kc_upper=kcu,kc_lower=kcl)
        deltas.append(row['delta'])
        if i>=38:
            ys=deltas[i-19:i+1]
            assert all(y is not None for y in ys)
            mean=sum(ys,D(0))/20
            slope=sum(((D(j)-D('9.5'))*(y-mean) for j,y in enumerate(ys)),D(0))/D(665)
            row.update(slope=slope,fc=mean+slope*(D('10.5') if forecast_ahead else D('9.5')))
        if i>=39 and i+count_origin>=40:
            row['buy']=bool(data[-1]['sq'] and not row['sq'] and row['fc']>0 and row['fc']>data[-1]['fc'])
        if i>=40 and i+count_origin>=41:
            row['sell']=bool(row['fc']>0 and row['fc']<data[-1]['fc'] and data[-1]['fc']>0 and data[-1]['fc']<data[-2]['fc'])
        data.append(row)
    return data


def projected_actions(data):
    held=False
    result=[]
    for row in data:
        if not '2021-01-01'<=row['date']<='2026-10-23':
            continue
        if held and row['sell']:
            result.append((row['date'],'sell'))
            held=False
        elif not held and row['buy'] and not row['sell']:
            result.append((row['date'],'buy'))
            held=True
    return result,held


def dump_default(value):
    if isinstance(value,D): return str(value)
    raise TypeError(type(value).__name__)


def main():
    OUT.mkdir(exist_ok=True)
    manifest_path=BASE/'after-download/input-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    source=next(x for x in manifest['securities'] if x['security_code']==CODE)
    raw_path=BASE/'after-download'/source['snapshot_path']
    raw=raw_path.read_bytes()
    assert sha256(raw).hexdigest()==source['snapshot_sha256']
    bars=load_tdx_day(raw_path)
    ledger_path=BASE/'native-results/S21/native-trades-all.xls'
    ledger_blob=ledger_path.read_bytes()
    table=list(csv.reader(io.StringIO(ledger_blob.decode('gb18030')),delimiter='\t'))
    target=[r for r in table[1:] if r and r[0]==CODE]
    summary_path=BASE/'native-results/S21/native-summary.xls'
    summary_blob=summary_path.read_bytes()
    summary=list(csv.reader(io.StringIO(summary_blob.decode('gb18030')),delimiter='\t'))
    total_column=summary[0].index('\u603b\u6b21\u6570')
    native_count=int(next(r for r in summary[1:] if r and r[0]==CODE)[total_column])
    assert len(target)==native_count*2
    labels={'\u4e70\u5f00':'buy','\u5356\u5e73':'sell'}
    native_actions=[(r[2][:10],labels[r[3]]) for r in target if r[3] in labels]
    terminal=[r[:5] for r in target if r[3]=='\u5e73\u76d8']
    reference=calculate(bars)
    dates={r['date']:i for i,r in enumerate(reference)}
    observed=[]
    for row in target:
        if row[3] not in labels: continue
        index=dates[row[2][:10]]
        point=reference[index]
        action=labels[row[3]]
        next_open=bars[index+1].open if index+1<len(bars) else None
        observed.append({'date':row[2][:10],'side':action,'condition_on_raw_reference':point[action],
                         'native_display_price':row[4],'raw_next_stored_date':str(bars[index+1].date) if next_open is not None else None,
                         'raw_next_open':next_open,'display_price_equal_to_raw_next_open':D(row[4])==next_open,
                         'reference_population_std':point['std'],'reference_regression_current_endpoint':point['fc'],
                         'reference_regression_slope':point['slope']})
    matrix=[]
    for sample in [False,True]:
        for ahead in [False,True]:
            for origin in [0,1]:
                values=calculate(bars,sample_std=sample,forecast_ahead=ahead,count_origin=origin)
                actions,held=projected_actions(values)
                matrix.append({'std_model':'sample_Nminus1' if sample else 'population_N',
                               'regression_endpoint':'one_ahead' if ahead else 'current_last',
                               'barscount_origin':origin,'projected_action_dates':actions,
                               'matches_all_native_nonterminal_dates':actions==native_actions,
                               'projected_position_still_open':held})
    # Numeric UI candidates distinguish endpoint by slope, not by action coincidence.
    complete=[r for r in reference if r['fc'] is not None and r['std'] is not None]
    selected=sorted(complete,key=lambda r:abs(r['slope']),reverse=True)[:3]
    selected+= [reference[dates['2023-12-29']],reference[dates['2024-01-04']]]
    candidates=[]
    seen=set()
    for p in selected:
        if p['date'] in seen:continue
        seen.add(p['date'])
        candidates.append({'date':p['date'],'raw_close':p['close'],'POP_expected_if_raw_basis':p['std'],
                           'FC_current_expected_if_raw_basis':p['fc'],'FC_one_ahead_candidate':p['fc']+p['slope'],
                           'slope_separation':p['slope']})
    outcsv=OUT/'688322-independent-indicators.csv'
    fields=['date','bar_index_0based','open','high','low','close','std','sq','delta','slope','fc','buy','sell']
    with outcsv.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        w.writeheader();w.writerows(reference)
    report={'status':'independent_raw_candidate_signal_comparison_not_native_numeric_verification',
            'security_code':CODE,'real_quotes_read':True,'price_basis':'stored_raw_snapshot; selected by UI owner as no-action candidate; full native forward-adjusted series not independently exported yet',
            'raw_path':str(raw_path.relative_to(BASE)),'raw_sha256':sha256(raw).hexdigest(),
            'raw_bars':len(bars),'native_ledger_path':str(ledger_path.relative_to(BASE)),
            'native_summary_sha256':sha256(summary_blob).hexdigest(),'native_summary_total_count_for_security':native_count,
            'native_formula_sha256':sha256((BASE/'native-evaluation/per-strategy/S21-expert.txt').read_bytes()).hexdigest(),
            'diagnostic_indicator_sha256':sha256((BASE/'native-evaluation/S21-native-diagnostic-indicator.txt').read_bytes()).hexdigest(),
            'native_ledger_sha256':sha256(ledger_blob).hexdigest(),'native_rows_for_security':len(target),
            'native_nonterminal_action_dates':native_actions,'terminal_rows_excluded_from_signal_condition_test':terminal,
            'observed_dates':observed,'population_current_endpoint_projection_matches_native':projected_actions(reference)[0]==native_actions,
            'model_matrix':matrix,'numeric_UI_check_candidates':candidates,
            'model_definition':'Decimal precision40, complete20-bar windows, population STDP, linear OLS regression evaluated at index19/current endpoint; no recursive seed. Only bar39 onward is used; entry warmup40, exit41.',
            'warmup_seed_scope':'If FORCAST is truly rolling20, native partial-window seeds before full20 inputs cannot affect signals after nominal40/41 bars; this reasoning does not independently prove native function internals.',
            'native_primitive_diagnostic_path':'../S21-native-diagnostic-indicator.txt',
            'independent_numeric_verification_status':'pending actual native POP/FC values or CHECK21 export; date agreement alone does not prove numeric identity',
            'other_securities_status':'No raw-to-front-adjusted equivalence assumed; need actual native adjusted OHLC and corresponding native indicator exports.',
            'execution_permissions_inferred':False,'orders_or_financial_returns_recomputed':False,
            'indicator_csv_sha256':sha256(outcsv.read_bytes()).hexdigest(),
            'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    assert ledger_path.read_bytes()==ledger_blob
    (OUT/'688322-reference-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=dump_default)+'\n',encoding='utf-8')
    print(json.dumps({'rows':len(target),'native_dates':native_actions,'all_native_dates_have_reference_condition':all(r['condition_on_raw_reference'] for r in observed),
                      'population_current_projection_matches':report['population_current_endpoint_projection_matches_native'],
                      'raw_next_open_matches':sum(r['display_price_equal_to_raw_next_open'] for r in observed),
                      'models_matching':[{'std':r['std_model'],'endpoint':r['regression_endpoint'],'origin':r['barscount_origin']} for r in matrix if r['matches_all_native_nonterminal_dates']],
                      'native_numeric_verification':'still_pending'},ensure_ascii=True))


if __name__=='__main__':main()
