"""Compare native S19 signal timestamps with preannounced fixed market anchors."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json

BASE = Path(__file__).resolve().parent
def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

audit_path = BASE / "native-audit/S19-audit.json"
calendar_path = BASE / "strategy19-calendar.json"
market_calendar_path = BASE / "calendar.json"
audit, calendar, market_calendar = read(audit_path), read(calendar_path), read(market_calendar_path)
source_map = {s["id"]:s for s in market_calendar["sources"]}
buys = {e["buy_market_day"]:e for e in calendar["events"] if e["buy_in_active_expression"]}
sells = {e["sell_market_day"]:e for e in calendar["events"] if e["sell_in_active_expression"]}
line_map = {r["source_line"]:r for r in audit["normalized_ledger"]}
checks, pair_checks = [], []
per_stock = defaultdict(Counter)
for row in audit["normalized_ledger"]:
    forced = row["forced_end_close"]
    kind = "forced_end_close" if forced else "buy" if row["signal"] == "买开" else "sell" if row["signal"] == "卖平" else "unknown"
    event = buys.get(row["signal_date"]) if kind == "buy" else sells.get(row["signal_date"]) if kind == "sell" else None
    ids = event["buy_source_ids"] if kind == "buy" and event else event["sell_source_ids"] if kind == "sell" and event else []
    known = all(source_map[s]["published_date"] <= row["signal_date"] for s in ids) if ids else None
    checks.append({"source_line":row["source_line"],"code":row["code"],"native_signal_date":row["signal_date"],"signal":row["signal"],"kind":kind,"forced_end_close":forced,"matches_own_market_anchor":None if forced else event is not None,"calendar_source_ids":ids,"calendar_sources_published_before_own_signal":known})
    per_stock[row["code"]][kind] += 1
for security in audit["per_security"]:
    for pair in security["roundtrip_pairs"]:
        entry, exit_row = line_map[pair["entry_line"]], line_map[pair["exit_line"]]
        event = buys.get(entry["signal_date"])
        expected_sell = event["sell_market_day"] if event else None
        forced = pair["forced_exit"]
        match = None if forced else expected_sell == exit_row["signal_date"]
        pair_checks.append({"code":security["code"],"entry_line":pair["entry_line"],"exit_line":pair["exit_line"],"native_entry_signal_date":entry["signal_date"],"expected_next_month_third_market_day":expected_sell,"native_exit_signal_date":exit_row["signal_date"],"forced_exit":forced,"matches_immediately_following_month_sell_anchor":match})

ordinary = [r for r in checks if not r["forced_end_close"]]
date_bad = [r for r in ordinary if not r["matches_own_market_anchor"]]
pair_bad = [r for r in pair_checks if r["matches_immediately_following_month_sell_anchor"] is False]
source_bad = [r for r in ordinary if r["calendar_sources_published_before_own_signal"] is not True]
result = {"schema_version":1,"strategy_id":19,"kind":"native_signal_date_calendar_anchor_audit_not_execution_price_audit","status":"calendar_signal_dates_match" if not date_bad and not pair_bad and not source_bad else "calendar_date_mismatches_detected","active_buy_anchor_count":len(buys),"active_sell_anchor_count":len(sells),"native_ledger_rows":len(checks),"ordinary_signal_count":len(ordinary),"native_roundtrip_count":len(pair_checks),"forced_end_close_count":len(checks)-len(ordinary),"anchor_date_mismatch_count":len(date_bad),"immediate_next_month_pair_mismatch_count":len(pair_bad),"source_publication_timing_mismatch_count":len(source_bad),"per_security":{k:dict(v) for k,v in per_stock.items()},"signal_checks":checks,"roundtrip_checks":pair_checks,"mismatches":date_bad+pair_bad+source_bad,"native_export_hashes":{Path(r["path"]).name:r["sha256"] for r in audit["source_files"]},"sources":[{"path":p.relative_to(BASE).as_posix(),"sha256":sha(p)} for p in (audit_path,calendar_path,market_calendar_path,Path(__file__))],"checks":{"all_ordinary_signals_on_own_static_market_anchor":not date_bad,"all_natural_roundtrips_exit_on_immediately_next_month_third_market_day":not pair_bad,"all_own_calendar_sources_preannounced_before_signal":not source_bad,"forced_end_closes_checked_separately":True},"limits":["Compares exported signal timestamps only; does not prove actual fill timestamp, front-adjusted price, permission or intraday path.","Forced-end-close rows, if any, are kept separate from natural calendar exits.","No local backtest, market rows or replacement trades are generated."]}
out = BASE / "native-audit/S19-calendar-audit.json"
out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["# 策略19原生信号日期核对","",f"原生流水{len(checks)}条，普通信号{len(ordinary)}条，往返{len(pair_checks)}次；期末强制平盘{result['forced_end_close_count']}条单独计算。", "",f"对照69个买点和69个卖点：信号锚点日期不符{len(date_bad)}条，买点与紧接次月第3市场日卖点的配对不符{len(pair_bad)}对，公告发布时序不符{len(source_bad)}条。", "","|股票|买开|自然卖平|期末平盘|","|---|---:|---:|---:|"]
for code, counts in per_stock.items():
    lines.append(f"|{code}|{counts['buy']}|{counts['sell']}|{counts['forced_end_close']}|")
lines += ["","69+69日期来自上级`strategy19-calendar.json`，该文件引用`calendar.json`内的沪深官方年度公告；完整输入SHA256、每条信号和每对开平记录见`S19-calendar-audit.json`。2026年10月尚未到来的买点不进入本次活动白名单。","","本核对只验证原生导出的信号日期，不宣称实际成交日期、复权执行价格或交易许可已经核验；没有重算策略收益或补造交易。",""]
(BASE / "native-audit/S19-calendar-audit.md").write_text("\n".join(lines),encoding="utf-8")
print(json.dumps({k:result[k] for k in ("status","native_ledger_rows","native_roundtrip_count","forced_end_close_count","anchor_date_mismatch_count","immediate_next_month_pair_mismatch_count","source_publication_timing_mismatch_count")},ensure_ascii=True))
raise SystemExit(0 if all(result["checks"].values()) else 1)
