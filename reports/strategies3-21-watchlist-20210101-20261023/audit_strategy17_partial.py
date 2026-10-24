"""Audit preserved partial S17 exports only; never combine or generate trades."""
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import csv
import hashlib
import io
import json

BASE = Path(__file__).resolve().parent
FOLDER = BASE / "native-results/S17"
session_path = BASE / "native-session-status.json"
session = json.loads(session_path.read_text(encoding="utf-8-sig")) if session_path.exists() else {}
blocked = bool(session.get("technical_blocked"))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
summary_path = FOLDER / "native-summary.xls"
summary_bytes = summary_path.read_bytes()
summary_digest = hashlib.sha256(summary_bytes).hexdigest()
summary_rows = list(csv.DictReader(io.StringIO(summary_bytes.decode("gb18030")), delimiter="\t"))
stocks = {r["品种代码"]:r for r in summary_rows if r["品种代码"].isdigit()}
assert len(summary_rows) == 8 and len(stocks) == 7
aggregate = next(r for r in summary_rows if r["品种名称"] == "综合统计")
assert sum(int(r["总次数"]) for r in stocks.values()) == int(aggregate["总次数"]) == 738
source_records = [json.loads(line) for line in (BASE / "native-run-source-log.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
source_record = next(r for r in reversed(source_records) if r.get("sid") == 17 and r.get("summary_sha256") == sha(summary_path) and r.get("source_path"))
source_path = (BASE / source_record["source_path"]).resolve()
assert source_path.is_relative_to(BASE) and sha(source_path) == source_record["source_sha256"]
files, missing = [], []
first_header = None
for code in sorted(stocks):
    path = FOLDER / "per-security" / f"native-trades-{code}.xls"
    expected_roundtrips = int(stocks[code]["总次数"])
    if not path.exists():
        missing.append({"security_code":code,"expected_native_roundtrips":expected_roundtrips,"expected_entry_and_exit_rows":expected_roundtrips*2,"status":"not_saved"})
        continue
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    header = raw[:raw.index(b"\n")+1]
    if first_header is None:
        first_header = header
    assert header == first_header
    rows = list(csv.DictReader(io.StringIO(raw.decode("gb18030")), delimiter="\t"))
    assert rows and len(rows) == expected_roundtrips*2
    held, last_time, bought, closed = None, None, 0, 0
    for row in rows:
        assert row["品种代码"] == code
        stamp = datetime.strptime(row["时间"], "%Y-%m-%d %H:%M")
        assert last_time is None or stamp >= last_time
        last_time = stamp
        quantity = Decimal(row["交易量(股/手)"])
        assert quantity.is_finite() and quantity > 0
        if row["信号"] == "买开":
            assert held is None
            held, bought = quantity, bought+1
        elif row["信号"] in ("卖平", "平盘"):
            assert held is not None and held == quantity
            held, closed = None, closed+1
        else:
            raise AssertionError("Unexpected native signal")
    assert held is None and bought == closed == expected_roundtrips
    assert sha(path) == digest
    files.append({"security_code":code,"path":path.relative_to(BASE).as_posix(),"sha256":digest,"bytes":len(raw),"header_sha256":hashlib.sha256(header).hexdigest(),"native_rows":len(rows),"native_buy_rows":bought,"native_close_rows":closed,"native_summary_total_count":expected_roundtrips,"first_native_signal_time":rows[0]["时间"],"last_native_signal_time":rows[-1]["时间"],"raw_source_unchanged_during_read":True,"code_order_quantity_and_pairing_checks_passed":True})
assert sha(summary_path) == summary_digest
result = {"schema_version":1,"kind":"partial_native_per_security_export_manifest_not_complete_strategy","status":"partial_saved_waiting_user_manual_recovery","recorded_at_local":datetime.now().astimezone().isoformat(timespec="seconds"),"strategy_id":17,"source_path":source_path.relative_to(BASE).as_posix(),"source_sha256":source_record["source_sha256"],"summary_path":summary_path.relative_to(BASE).as_posix(),"summary_sha256":summary_digest,"summary_rows":8,"summary_native_roundtrips":738,"expected_full_entry_exit_rows":1476,"native_per_security_files_saved":len(files),"native_rows_saved":sum(r["native_rows"] for r in files),"missing_native_files":missing,"files":files,"full_merged_ledger_created_by_this_audit":False,"financial_fields_recomputed":False,"new_trades_generated":False,"strategy_may_be_marked_complete":False,"strategy_returns":None,"scope":"Only native headers, raw hashes, single-code scope, positive quantity, chronology, open/close pairing and summary counts checked. No local strategy backtest or fee/profit recomputation.","current_technical_block":"Parent reports SaveDialog remains unresponsive after reconnecting Sky; user manual save or restart/login recovery was requested and is pending.","generator_sha256":sha(Path(__file__))}
out = FOLDER / "native-trades-partial.manifest.json"
result["status"] = "partial_saved_waiting_user_manual_recovery" if blocked else "partial_saved_export_in_progress"
result["current_technical_block"] = session.get("block_reason") if blocked else None
result["current_session_status"] = session.get("status")
out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["# 策略17流水尚未完整保存","",f"已只读核对{len(files)}只原生逐股导出、共{result['native_rows_saved']}条；完整汇总为738次，按每次开平两行应为1476条。未生成合并全股流水，策略保持进行中、收益字段为空。","","|股票|已存原生行|买开|卖平或平盘|汇总总次数|","|---|---:|---:|---:|---:|"]
for row in files:
    lines.append(f"|{row['security_code']}|{row['native_rows']}|{row['native_buy_rows']}|{row['native_close_rows']}|{row['native_summary_total_count']}|")
lines += ["", "尚缺："+"、".join(f"{r['security_code']}（预期{r['expected_entry_and_exit_rows']}行）" for r in missing)+"。", "", "当前通达信保存对话框持续无响应，主执行代理重连Sky后未恢复，已请求用户手动保存或重启登录。当前技术恢复仍待用户完成，不是等待强退审批；本审计没有操作界面或写行情。", "", f"已存{len(files)}份原始文件、汇总、源公式和旧来源日志均保持原样。完整文件路径及SHA256见`native-trades-partial.manifest.json`。本次只检查原生行的结构与完整性，不重算费用或收益，不将部分流水当作完整策略结果。", ""]
if not blocked:
    lines = [line.replace("当前通达信保存对话框持续无响应，主执行代理重连Sky后未恢复，已请求用户手动保存或重启登录。当前技术恢复仍待用户完成，不是等待强退审批；本审计没有操作界面或写行情。", "用户回复“应该好了”，主执行代理确认保存窗口已恢复，688017原生212行已由审计代理校验并原字节归档。现继续剩余导出，不再等待用户恢复或强退审批；本审计没有操作界面或写行情。") for line in lines]
lines += ["恢复方案`00-native-export-inbox/recovery-S10.tes`仅是S10方案。若重新加载它，必须同步当前B321NAT的S17源正文及runtime SID17，不能直接当S17方案运行。", ""]
(FOLDER / "流水未完成说明.md").write_text("\n".join(lines),encoding="utf-8")
print(json.dumps({"saved_files":len(files),"saved_rows":result["native_rows_saved"],"expected_rows":1476,"missing":[r["security_code"] for r in missing],"complete":False},ensure_ascii=True))
