"""Deliberate mutation of an isolated COPY; no native input is modified."""
from contextlib import redirect_stdout
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import audit_s21_precision_v2 as audit

def main():
    out=BASE/'native-evaluation/S21-precision-v2'
    fixture=out/('SYNTHETIC-source-change-fixture-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    fixture.mkdir()
    actual=BASE/'native-inputs/688322-CHECKALL-v2.txt'
    raw=BASE/'after-download/source-files/sh688322.day'
    ledger=BASE/'native-results/S21/native-trades-all.xls'
    originals={str(p):p.read_bytes() for p in [actual,raw,ledger]}
    copies=[(actual,'native-inputs/688322-CHECKALL-v2.txt'),(raw,'after-download/source-files/sh688322.day'),(ledger,'native-results/S21/native-trades-all.xls'),(BASE/'native-evaluation/CHECKALL-precision-v2.txt','native-evaluation/CHECKALL-precision-v2.txt'),(BASE/'native-evaluation/per-strategy/S21-expert.txt','native-evaluation/per-strategy/S21-expert.txt'),(BASE/'audit_strategy21_reference.py','audit_strategy21_reference.py')]
    for src,relative in copies:
        target=fixture/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(src.read_bytes())
    native_copy=fixture/'native-inputs/688322-CHECKALL-v2.txt'
    (fixture/'after-download/input-manifest.json').write_text(json.dumps({'kind':'SYNTHETIC_SOURCE_CHANGE_TEST_ONLY','securities':[{'security_code':'688322','snapshot_path':'source-files/sh688322.day','snapshot_sha256':sha256(originals[str(raw)]).hexdigest()}]}),encoding='utf-8')
    (fixture/'run-manifest.json').write_text(json.dumps({'kind':'SYNTHETIC_SOURCE_CHANGE_TEST_ONLY','client_configuration':{'force_end_close_included':True}}),encoding='utf-8')
    record={'kind':'SYNTHETIC_SOURCE_CHANGE_TEST_ONLY_NOT_A_NATIVE_EXPORT_RECORD','code':'688322','source':'native-evaluation/CHECKALL-precision-v2.txt','source_sha256':audit.SOURCE_SHA,'path':'native-inputs/688322-CHECKALL-v2.txt','sha256':sha256(native_copy.read_bytes()).hexdigest(),'scale':1048576}
    (fixture/'native-inputs/export-provenance.jsonl').write_text(json.dumps(record)+'\n',encoding='utf-8')
    oldbase=audit.BASE;oldcalculate=audit.calculate;oldargv=sys.argv[:]
    modified=False;error=None
    def mutate_copy_after_load(bars,**kwargs):
        nonlocal modified
        result=oldcalculate(bars,**kwargs)
        if not modified:
            native_copy.write_bytes(native_copy.read_bytes()+b'\nSYNTHETIC DELIBERATE COPY MUTATION\n');modified=True
        return result
    try:
        audit.BASE=fixture;audit.calculate=mutate_copy_after_load
        sys.argv=['audit_s21_precision_v2.py','--code','688322','--input',str(native_copy)]
        with redirect_stdout(io.StringIO()):
            try:audit.main()
            except AssertionError as exc:error=str(exc)
    finally:
        audit.BASE=oldbase;audit.calculate=oldcalculate;sys.argv=oldargv
    published=fixture/'native-evaluation/S21-precision-v2/688322'
    assert modified and error=='native input changed during audit'
    assert not (published/'audit.json').exists() and not (published/'values.csv').exists()
    for p,blob in originals.items():assert Path(p).read_bytes()==blob
    report={'kind':'SYNTHETIC_SOURCE_CHANGE_CONTROL_TEST_NOT_MARKET_OR_NATIVE_RESULT','status':'pass','fixture_path':str(fixture.relative_to(BASE)),'real_quote_source_was_copied':True,'only_isolated_copy_deliberately_modified':True,'observed_error':error,'completed_audit_published':False,'values_csv_published':False,'original_input_ledger_and_raw_bytes_unchanged':True,'adapter_sha256':sha256((BASE/'audit_s21_precision_v2.py').read_bytes()).hexdigest(),'test_script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'formal_returns_generated':False}
    (out/'synthetic-source-change-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'pass','source_mutation_rejected_before_publication':True,'original_files_unchanged':True}))

if __name__=='__main__':main()
