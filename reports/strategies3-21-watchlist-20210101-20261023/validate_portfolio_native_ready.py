"""Publish per-attempt adapter tests and real preflight evidence; no returns."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

BASE=Path(__file__).resolve().parent
REPO=BASE.parent.parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    relative=[BASE/'portfolio_native_runner.py',BASE/'native_execution_permissions.py',BASE/'scale_in_native_preflight.py',BASE/'test_portfolio_native_runner.py',Path(__file__).resolve()]
    relative += [REPO/'src/agent_stocking'/n for n in ('portfolio.py','portfolio_strategies.py','point_in_time.py','models.py','data.py')]
    relative += [BASE/f'portfolio14-20/native-runner/S{sid}-rules-pending.json' for sid in (14,20)]
    sources=[{'project_path':p.relative_to(REPO).as_posix(),'sha256':sha(p)} for p in relative]
    folder=BASE/'portfolio14-20/native-runner/validation'/datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True,exist_ok=False);commands=[]
    def invoke(name,args,expected=0,count=None):
        command=[sys.executable,'-X','utf8',*map(str,args)]
        process=subprocess.run(command,cwd=REPO,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        log=folder/(name+'.log');log.write_bytes(process.stdout)
        commands.append({'name':name,'command':command,'exit_code':process.returncode,'expected_exit_code':expected,'test_count':count,'log':log.relative_to(BASE).as_posix(),'log_sha256':sha(log)})
        if process.returncode!=expected:raise AssertionError((name,process.stdout.decode('utf-8',errors='replace')))
    invoke('adapter-tests',[BASE/'test_portfolio_native_runner.py','-v'],count=11)
    outcomes=[]
    for sid in (14,20):
        args=[BASE/'portfolio_native_runner.py','--strategy',str(sid),'--config',BASE/f'portfolio14-20/native-runner/S{sid}-rules-pending.json']
        for mode,exitcode in (('preflight',0),('modeled-backtest',2)):
            out=folder/f'S{sid}-{mode}'
            invoke(f'S{sid}-{mode}',args+['--mode',mode,'--out',out],exitcode)
            result_path=out/('preflight.json' if mode=='preflight' else 'attempt.json')
            r=json.loads(result_path.read_text(encoding='utf-8'))
            assert r['full_native_rows']==10443 and r['active_rows']==9387 and r['permission_unknown_inventory_count']==18774
            assert r['formal_returns'] is None and not r['orders_generated'] and not r['fills_generated']
            assert not r['fact_loader_errors'] and not r['selected_rules_confirmed']
            outcomes.append({'strategy_id':sid,'mode':mode,'path':result_path.relative_to(BASE).as_posix(),'sha256':sha(result_path),'status':r['status']})
    for item in sources:assert sha(REPO/item['project_path'])==item['sha256'],'Source changed during validation'
    evidence={'schema_version':1,'kind':'actual_input_adapter_readiness_not_a_backtest','status':'passed','created_at':datetime.now().astimezone().isoformat(timespec='seconds'),
        'adapter_tests':11,'project_unittest_rerun':False,'synthetic_test_witnesses_are_not_market_permissions':True,
        'commands':commands,'outcomes':outcomes,'source_hashes':sources,'orders_generated':False,'fills_generated':False,'formal_returns':None}
    path=folder/'evidence.json';path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    index={'path':path.relative_to(BASE).as_posix(),'sha256':sha(path),'status':'passed','kind':evidence['kind']}
    (folder.parent/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'passed','adapter_tests':11,'actual_native_rows':10443,'pending_modeled_guards':2,'evidence':index},ensure_ascii=True))


if __name__=='__main__':main()
