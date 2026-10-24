"""Synthetic parser risk checks. These files are NOT native or market inputs."""
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from audit_s21_precision_v2 import decode_export,FIELDS,terminal_lifecycle

def main():
    out=BASE/'native-evaluation/S21-precision-v2/SYNTHETIC-parser-fixtures';out.mkdir(parents=True,exist_ok=True)
    sc=1048576
    def body(*,bad_fq=False,bad_bc=False,bad_rsi=False,bad_ohlc=False):
        first=[1,0,10*sc,11*sc,9*sc,10*sc,-1,-1,None,100000,None,None,None,None,12345]
        second=[2 if bad_fq else 1,3 if bad_bc else 1,10*sc,11*sc,12*sc if bad_ohlc else 9*sc,10*sc,-2 if bad_rsi else 80*sc,-1,None,100000,None,None,None,None,12345]
        return 'SYNTHETIC PARSER TEST ONLY; NOT NATIVE OR MARKET INPUT\n'+ '\t'.join(['date']+['CHECKALL.'+k for k in FIELDS])+'\n'+'\n'.join('\t'.join([day]+['' if x is None else str(x) for x in row]) for day,row in [('2026/01/05',first),('2026/01/06',second)])+'\n'
    evidence=[]
    for name,kwargs in [('sentinel-and-scale',{}),('reject-wrong-FQ',{'bad_fq':True}),('reject-discontinuous-BC',{'bad_bc':True}),('reject-negative-valid-RSI',{'bad_rsi':True}),('reject-invalid-OHLC',{'bad_ohlc':True})]:
        p=out/(name+'.txt');p.write_bytes(body(**kwargs).encode('gb18030'))
        rejected=False
        try:
            _,rows=decode_export(p)
            assert rows[0]['R2X']==rows[0]['R4X']==rows[1]['R4X']==Decimal(-1)
            assert rows[1]['R2X']==80 and rows[1]['OX']==10
        except AssertionError:
            if not kwargs:raise
            rejected=True
        assert rejected==bool(kwargs),name
        evidence.append({'case':name,'expected_rejection':bool(kwargs),'observed_rejection':rejected,'path':str(p.relative_to(BASE)),'sha256':sha256(p.read_bytes()).hexdigest()})
    for name,held,positions,expected in [('terminal-required-missing',True,[],False),('terminal-duplicate',True,[2,3],False),('terminal-not-last',True,[1],False),('terminal-unexpected-while-flat',False,[3],False),('terminal-one-last-when-held',True,[3],True),('terminal-none-when-flat',False,[],True)]:
        r=terminal_lifecycle(held_after_last_signal=held,terminal_positions=positions,ledger_rows=4,force_end_close=True)
        assert r['matches']==expected,name
        evidence.append({'case':name,'kind':'synthetic_terminal_lifecycle','expected_match':expected,'observed_match':r['matches']})
    result={'kind':'SYNTHETIC_PARSER_UNIT_CHECKS_NOT_MARKET_OR_NATIVE_DATA','status':'pass','checks':len(evidence),'cases':evidence,'adapter_sha256':sha256((BASE/'audit_s21_precision_v2.py').read_bytes()).hexdigest(),'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'formal_returns_generated':False}
    (out.parent/'synthetic-parser-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':len(evidence),'formal_returns_generated':False}))

if __name__=='__main__':main()
