"""S21 SYNTHETIC signal-only checks; no market returns or native UI."""
import ast
from datetime import date, timedelta
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation"
FORMULA = OUT / "per-strategy/S21-expert.txt"


def load_primitives():
    spec = importlib.util.spec_from_file_location("prior_native_model", BASE / "validate_native_signal_causality.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    primitives = load_primitives()

    class Model(primitives.FormulaModel):
        def call(self, name, args):
            if name in {"STDP", "FORCAST"}:
                values, length = args
                result = []
                for i in range(self.n):
                    ys = values[max(0, i-int(length)+1):i+1]
                    n = len(ys)
                    mean = sum(ys)/n
                    if name == "STDP":
                        result.append(math.sqrt(max(0, sum((y-mean)**2 for y in ys)/n)))
                    else:
                        xm = (n-1)/2
                        denominator = sum((j-xm)**2 for j in range(n))
                        slope = sum((j-xm)*(y-mean) for j,y in enumerate(ys))/denominator if denominator else 0
                        result.append(mean+slope*xm)
                return result
            return super().call(name,args)

    rows=[]
    day=date(2020,1,2)
    while len(rows)<1600:
        if day.weekday()<5:
            i=len(rows)
            phase=i%100
            close=100+i*0.02+(0.05*math.sin(i) if phase<50 else (phase-50)*0.45+2*math.sin(i/3))
            width=1.2 if phase<50 else 1.5
            rows.append(primitives.record(day,close,close+width,close-width))
        day+=timedelta(days=1)
    text=FORMULA.read_text(encoding="ascii")
    cases=[]
    counts={}
    for origin in [0,1]:
        model=Model(text,origin)
        env=model.common_values(rows)
        original=model.selected(env,21)
        assert any(original[0]) and any(original[1]), "Synthetic fixture must exercise both sides"
        counts[str(origin)]={"synthetic_buys":sum(original[0]),"synthetic_exits":sum(original[1])}
        for wrong in [3,11,13,20,22,21.5]:
            assert not any(any(v) for v in model.selected(env,wrong))
        for cut in [39,40,41,200,500,900,1250]:
            changed=[dict(r) for r in rows]
            for i in range(cut,len(changed)):
                changed[i].update(C=10000+i*50,H=11000+i*50,L=9000+i*50)
            mutated=model.selected(model.common_values(changed),21)
            prefix=model.selected(model.common_values(rows[:cut]),21)
            expected=tuple(v[:cut] for v in original)
            assert tuple(v[:cut] for v in mutated)==expected
            assert prefix==expected
            cases.append({"barscount_origin":origin,"cut":cut,"future_changed":"pass","future_removed":"pass"})
        # Exact logic boundaries: equal bands are not squeezed; equal momentum not declining.
        native_ast=dict(model.common)
        fixture={"C":[1,1,1],"BBU":[2,2,2],"KCU":[2,3,3],"BBL":[0,0,1],"KCL":[-1,-1,1]}
        assert model.node(native_ast["SQZ"],fixture)==[False,True,False]
        fixture={"C":[1,1,1],"RS":[True]*3,"MOM":[3,2,1]}
        model.n=3
        assert model.node(native_ast["S21"],fixture)[-1]
        fixture["MOM"]=[3,2,2]
        assert not model.node(native_ast["S21"],fixture)[-1]
        fixture["MOM"]=[1,0,-1]
        assert not model.node(native_ast["S21"],fixture)[-1]
        fixture={"C":[1,1,1],"RB":[True]*3,"SQZ":[True,False,False],"MOM":[1,2,3]}
        assert model.node(native_ast["B21"],fixture)==[False,True,False]
        fixture["MOM"]=[-1,0,1]
        assert not any(model.node(native_ast["B21"],fixture))
    metadata={"status":"confirmed_for_native_validation","strategy_id":21,"source_path":"per-strategy/S21-expert.txt","source_sha256":sha256(FORMULA.read_bytes()).hexdigest(),"physical_bytes":len(FORMULA.read_bytes()),"native_compile_tested":False,"native_ui_operated":False,"real_quotes_read":False,"orders_or_returns_calculated":False,"synthetic_kind":"SYNTHETIC_TEST_NOT_MARKET_RETURNS","synthetic_rows":1600,"synthetic_counts":counts,"temporal_cases":cases,"boundary_tests":"pass: strict containment, first release, rising positive entry, two strictly falling positive exit, wrong SID zero","model_limit":"STDP population deviation and FORCAST current regression endpoint are explicit causal test primitives. Native FORCAST implementation/seed to be checked; this does not claim StockCharts or native numerical identity.","warmup":"nominal complete delta+20 regression requires39 bars; rising entry compares prior momentum =>40; two declining bars compares three momenta=>41. BARSCOUNT origin native boundary still checked.","source_differences":["Original-style KC chosen explicitly as typical-price SMA20 +/-1.5*SMA20(H-L), not modern EMA/ATR","native STDP and FORCAST selected","daily close signals next-open entry/exit; source execution time not presumed identical","no fixed8-10bar exit, no short, no automatic zero-cross exit"],"official_sources":["https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html","https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ttm-squeeze","https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels"],"validator_sha256":sha256(Path(__file__).read_bytes()).hexdigest()}
    (OUT/'S21-adaptation-manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({"status":metadata['status'],"sha256":metadata['source_sha256'],"bytes":metadata['physical_bytes'],"temporal_cases":len(cases),"synthetic_counts":counts,"native_compile_tested":False}))


if __name__=='__main__':
    main()
