"""Process only finished v2 exports with matching UI-owner provenance."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

BASE=Path(__file__).resolve().parent
SOURCE_SHA='f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e'
CODES=['300308','300750','601899','603728','605488','688017','688322']

def main():
    p=BASE/'native-inputs/export-provenance.jsonl'
    lines=p.read_text(encoding='utf-8-sig').splitlines()
    records=[]
    for i,line in enumerate(lines):
        if not line.strip():continue
        try:records.append(json.loads(line))
        except json.JSONDecodeError:
            if i==len(lines)-1:break  # writer may still be appending final line
            raise
    script=BASE/'audit_s21_precision_v2.py';script_sha=sha256(script.read_bytes()).hexdigest()
    result=[]
    for code in CODES:
        candidates=[r for r in records if r.get('code',r.get('security'))==code and r.get('source_sha256')==SOURCE_SHA]
        if not candidates:
            result.append({'code':code,'status':'pending_native_export_with_source_binding','financial_result':None});continue
        r=candidates[-1];native=(BASE/r['path']).resolve()
        assert native.is_relative_to((BASE/'native-inputs').resolve())
        if not native.is_file() or sha256(native.read_bytes()).hexdigest()!=r['sha256']:
            result.append({'code':code,'status':'awaiting_finished_file_matching_provenance','financial_result':None});continue
        out=BASE/'native-evaluation/S21-precision-v2'/code/'audit.json'
        previous=json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
        if previous is None or previous.get('native_input_sha256')!=r['sha256'] or previous.get('script_sha256')!=script_sha:
            proc=subprocess.run([sys.executable,str(script),'--code',code,'--input',str(native)],capture_output=True,text=True,encoding='utf-8',errors='replace')
            if proc.returncode:
                result.append({'code':code,'status':'audit_failed_explicitly','error':proc.stderr.strip(),'financial_result':None});continue
        audit=json.loads(out.read_text(encoding='utf-8'))
        result.append({'code':code,'status':'native_high_precision_audit_saved','rows':audit['rows'],'native_trades':audit['native_trade_rows'],'signal_dates_equal':audit['all_nonterminal_signal_dates_equal'],'all_fill_display_prices_equal':audit['all_native_display_prices_match_v2_fill_session_price'],'terminal_lifecycle_matches':audit['terminal_lifecycle']['matches'],'signals_terminal_and_prices_all_match':audit['signals_terminal_and_prices_all_match'],'metrics':audit['metrics'],'audit_path':str(out.relative_to(BASE)),'audit_sha256':sha256(out.read_bytes()).hexdigest(),'input_sha256':r['sha256'],'financial_result':None})
    out=BASE/'native-evaluation/S21-precision-v2';out.mkdir(exist_ok=True)
    index={'kind':'S21_real_native_input_validation_not_a_financial_summary','status':'all_seven_audits_saved' if all(r['status']=='native_high_precision_audit_saved' for r in result) else 'awaiting_remaining_exports_or_explicit_audit_issue','source_sha256':SOURCE_SHA,'securities':result,'audit_script_sha256':script_sha,'runner_script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'public_batch_report_updated':False,'formal_returns_recomputed':False}
    saved=[r for r in result if r['status']=='native_high_precision_audit_saved']
    index['aggregate']={'saved_security_count':len(saved),'input_rows':sum(r['rows'] for r in saved),'native_trade_rows':sum(r['native_trades'] for r in saved),'all_saved_signals_terminal_and_prices_match':all(r['signals_terminal_and_prices_all_match'] for r in saved) if saved else None,'reference_temporal_comparisons':len(saved)*10,'indicators':{k:{'complete_rows':sum(r['metrics'][k]['complete_window_rows'] for r in saved),'same_two_decimal_rows':sum(r['metrics'][k]['same_2_decimal_rows'] for r in saved),'same_scaled_two_decimal_rows':sum(r['metrics'][k]['same_scaled_2_decimal_rows'] for r in saved),'mismatch_dates':{r['code']:r['metrics'][k]['display_2dp_mismatch_dates'] for r in saved if r['metrics'][k]['display_2dp_mismatch_dates']}} for k in ['POP','FC']},'no_hidden_precision_identity_claim':True}
    (out/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    count=sum(r['status']=='native_high_precision_audit_saved' for r in result)
    md=['# S21 七股原生高精度输入审计','',f'已保存 {count}/7 股独立审计。本目录不计算收益，不改变批量评测公共报告；旧两位证据保留在 `../S21-reference/`。', '',
        '每份输入须同时匹配 UI 操作者追加的 export-provenance 记录、原件哈希与不可变 v2 源 `f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e`。UI 名仍为 CHECKALL；表头本身不能证明版本。所有X字段除以1048576，RSI的原生-1无效标志不缩放。FQ逐行1、BC逐行连续从0起，并与只读.day库存的全部日期精确核对；不用raw价格替代原生前复权价格。', '',
        '|股票|完整行|原生S21流水|独立信号日期一致|原生成交显示价一致|期末状态完整|', '|---|---:|---:|---|---|---|']
    for r in result:
        saved=r['status']=='native_high_precision_audit_saved'
        md.append('|'+r['code']+'|'+str(r.get('rows','待导出'))+'|'+str(r.get('native_trades','—'))+'|'+('是' if r.get('signal_dates_equal') else '否' if saved else '待核')+'|'+('是' if r.get('all_fill_display_prices_equal') else '否' if saved else '待核')+'|'+('是' if r.get('terminal_lifecycle_matches') else '否' if saved else '待核')+'|')
    agg=index['aggregate'];pop=agg['indicators']['POP'];fc=agg['indicators']['FC']
    md+=['',f'当前合计 {agg["input_rows"]} 行原生输入、{agg["native_trade_rows"]} 行原生S21流水；独立参考时序检查 {agg["reference_temporal_comparisons"]} 项。POP完整窗口两位相同 {pop["same_two_decimal_rows"]}/{pop["complete_rows"]}，FC {fc["same_two_decimal_rows"]}/{fc["complete_rows"]}。七股各自首次完整窗口STDP=0的边界保留；300750于2023-10-19的FC有一个舍入边界差（原生-18.1649951935、独立-18.1650011008），没有改变已核原生交易信号日期。']
    md+=['','逐值CSV保留Decimal40独立数学计算、本机解码值、原始差值、两位显示相符与scaled两位相符两种统计。**两位一致不等于内部bit一致。** Float32 ULP距离仅用于描述误差，没有按观察结果选择容差来宣布通过。每股首个完整20根STDP为0的本机边界原样保留，另列后续窗口误差；FC从完整20个MDEL输入开始核对。', '',
        '价格核验只对已保存原生流水：普通买开/卖平行按信号日的下一已导出交易时段开盘，期末平盘（若有）按原生末日收盘，均按成交栏两位显示精度比较。没有重算交易数量、金额、费用、资金与收益，也没有从OHLCV推断交易许可。', '',
        '每股另做5个截止点的截断与明确标测试的未来价格扰动（共10项完整历史指标/信号dict比较）。当前前复权基准和当前自选股的历史可见性/选择偏差范围没有因这些测试而消失。原生源绑定、每股审计/CSV哈希、所有尚待导出状态见 `index.json` 与各股 `audit.json`。解析器风险检查 `synthetic-parser-checks.json` 的11例（5项解析、6项期末状态）均为合成输入，不能当真实市场或原生输出。','']
    (out/'README.md').write_text('\n'.join(md),encoding='utf-8')
    print(json.dumps({'status':index['status'],'securities':[{k:r.get(k) for k in ['code','status','rows','native_trades','signal_dates_equal','all_fill_display_prices_equal']} for r in result]}))
    return 1 if any(r['status']=='audit_failed_explicitly' for r in result) else 0

if __name__=='__main__':
    if '--watch' in sys.argv:
        # Read-only input monitor, finite15-minute lifetime. No UI/network and
        # no file is admitted before its matching source-binding hash arrives.
        deadline=time.monotonic()+900
        last=None
        while time.monotonic()<deadline:
            current=sha256((BASE/'native-inputs/export-provenance.jsonl').read_bytes()).hexdigest()
            if current!=last:
                status=main();last=current
                if status:sys.exit(status)
                index=json.loads((BASE/'native-evaluation/S21-precision-v2/index.json').read_text(encoding='utf-8'))
                if index['status']=='all_seven_audits_saved':sys.exit(0)
            time.sleep(2)
        print('Finite monitor ended; pending exports remain pending, no financial result inferred.',flush=True)
    else:sys.exit(main())
