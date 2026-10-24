"""Read-only audit of Tongdaxin GB18030 TSV exports named .xls.

Run from any directory:
  python -X utf8 audit_native_exports.py
  python -X utf8 audit_native_exports.py --strategy 3

Only native-audit/ is written. Original exports and quotation snapshots stay intact.
All money and published percentages are parsed as Decimal, serialized as strings.
This audits published native results; it does not run a backtest or infer permission.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import struct
import sys

ZERO = Decimal("0")
CENT = Decimal("0.01")
HALF_CENT = Decimal("0.005")
SUMMARY_COLUMNS = (
    "品种代码", "品种名称", "盈利数", "总次数", "胜率(%)", "手续费(元)",
    "净利润(元)", "收益率(%)", "年化收益率(%)", "相对收益率α/β(%)",
    "最大回撤(元)", "最大回撤比(%)",
)
TRADE_COLUMNS = (
    "品种代码", "品种名称", "时间", "信号", "价格(元)", "交易量(股/手)",
    "成交额(元)", "收益(元)", "收益率(%)", "相对收益率α(%)", "手续费(元)", "可用资金(元)",
)
DAY_RECORD = struct.Struct("<IIIIIfII")
NOT_AVAILABLE = {"", "-", "--", "---", "N/A", "n.a.", "无"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def decimal_value(value: str, *, nullable: bool = False):
    text = value.strip().replace(",", "")
    if text in NOT_AVAILABLE:
        if nullable:
            return None
        raise ValueError(f"Required numeric value missing: {value!r}")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite decimal: {value!r}")
    return result


def count_value(value: str) -> int:
    number = decimal_value(value)
    if number != number.to_integral_value() or number < 0:
        raise ValueError(f"Invalid nonnegative count: {value!r}")
    return int(number)


def read_native_tsv(path: Path, columns: tuple[str, ...]):
    raw_bytes = path.read_bytes()
    if raw_bytes[:8] == bytes.fromhex("d0cf11e0a1b11ae1") or raw_bytes[:2] == b"PK":
        raise ValueError(f"Expected GB18030 TSV, got workbook container: {path}")
    text = raw_bytes.decode("gb18030", errors="strict")
    parsed = list(csv.reader(io.StringIO(text), delimiter="\t"))
    parsed = [row for row in parsed if any(cell.strip() for cell in row)]
    if not parsed or tuple(cell.strip() for cell in parsed[0]) != columns:
        raise ValueError(f"Unexpected TSV schema in {path}: {parsed[:1]!r}")
    output = []
    for line_number, row in enumerate(parsed[1:], 2):
        if len(row) != len(columns):
            raise ValueError(f"{path}:{line_number}: expected {len(columns)} cells, got {len(row)}")
        output.append(dict(zip(columns, (cell.strip() for cell in row))))
    return output, {"sha256": hashlib.sha256(raw_bytes).hexdigest(), "bytes": len(raw_bytes),
                    "encoding": "gb18030", "format": "TSV_text_not_Excel_binary", "records": len(output)}


def parse_summary(raw: dict):
    aggregate = raw["品种名称"] == "综合统计"
    code = raw["品种代码"]
    if not aggregate and not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"Unexpected security code: {code!r}")
    return {
        "code": code, "name": raw["品种名称"], "aggregate": aggregate,
        "winning_roundtrips": count_value(raw["盈利数"]), "total_times": count_value(raw["总次数"]),
        "native_win_rate_pct": decimal_value(raw["胜率(%)"], nullable=True),
        "native_fees": decimal_value(raw["手续费(元)"]),
        "native_net_profit": decimal_value(raw["净利润(元)"]),
        "native_return_pct": decimal_value(raw["收益率(%)"], nullable=True),
        "native_annualized_linear_pct": decimal_value(raw["年化收益率(%)"], nullable=True),
        "native_relative_alpha_beta_text": raw["相对收益率α/β(%)"],
        "native_max_drawdown_amount": decimal_value(raw["最大回撤(元)"], nullable=True),
        "native_max_drawdown_pct": decimal_value(raw["最大回撤比(%)"], nullable=True),
        "source_row": raw,
    }


def parse_trade(raw: dict, line_number: int):
    code = raw["品种代码"]
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"Unexpected trade code: {code!r}")
    timestamp = dt.datetime.strptime(raw["时间"], "%Y-%m-%d %H:%M")
    return {
        "source_line": line_number, "code": code, "name": raw["品种名称"],
        "signal_datetime": timestamp.isoformat(sep=" ", timespec="minutes"),
        "signal_date": timestamp.date().isoformat(), "signal": raw["信号"],
        "forced_end_close": raw["信号"] == "平盘",
        "native_price": decimal_value(raw["价格(元)"]),
        "quantity": count_value(raw["交易量(股/手)"]),
        "native_amount": decimal_value(raw["成交额(元)"]),
        "native_gross_profit": decimal_value(raw["收益(元)"]),
        "native_trade_return_pct": decimal_value(raw["收益率(%)"], nullable=True),
        "native_fee": decimal_value(raw["手续费(元)"]),
        "native_available_cash": decimal_value(raw["可用资金(元)"]),
        "source_row": raw,
    }


def pairing_audit(trades: list[dict]):
    held = None
    completed = []
    errors = []
    previous_date = None
    for trade in trades:
        if previous_date and trade["signal_date"] < previous_date:
            errors.append({"line": trade["source_line"], "issue": "dates_not_nondecreasing"})
        previous_date = trade["signal_date"]
        if trade["signal"] == "买开":
            if held is not None:
                errors.append({"line": trade["source_line"], "issue": "entry_while_already_long"})
            else:
                held = trade
        elif trade["signal"] in {"卖平", "平盘"}:
            if held is None:
                errors.append({"line": trade["source_line"], "issue": "close_without_long"})
            else:
                if trade["quantity"] != held["quantity"]:
                    errors.append({"line": trade["source_line"], "issue": "entry_exit_quantity_differs",
                                   "entry_quantity": held["quantity"], "exit_quantity": trade["quantity"]})
                completed.append({"entry_line": held["source_line"], "exit_line": trade["source_line"],
                                  "forced_exit": trade["forced_end_close"]})
                held = None
        else:
            errors.append({"line": trade["source_line"], "issue": "unrecognized_or_non_long_signal",
                           "signal": trade["signal"]})
    return {"completed_roundtrips": len(completed), "roundtrip_pairs": completed,
            "open_entry_at_end": None if held is None else held["source_line"], "sequence_errors": errors}


def market_calendar(calendar: dict):
    start = dt.date.fromisoformat(calendar["requested_range"]["start"])
    end = dt.date.fromisoformat(calendar["requested_range"]["end"])
    closed = set()
    for source in calendar["sources"]:
        for first, last in source.get("closed_ranges", []):
            current = dt.date.fromisoformat(first)
            stop = dt.date.fromisoformat(last)
            while current <= stop:
                closed.add(current)
                current += dt.timedelta(days=1)
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in closed:
            dates.append(current.isoformat())
        current += dt.timedelta(days=1)
    return dates


def load_snapshot_quotes(root: Path, manifest: dict):
    quotes = {}
    metadata = []
    base = (root / "after-download").resolve()
    for item in manifest["securities"]:
        path = (base / item["snapshot_path"]).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Quotation snapshot escapes after-download directory")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        expected = item.get("snapshot_sha256", item["sha256"])
        if digest != expected or len(content) % DAY_RECORD.size:
            raise ValueError(f"Snapshot integrity failed: {path}")
        records = {}
        for offset in range(0, len(content), DAY_RECORD.size):
            date, opening, high, low, close, _amount, volume, _reserved = DAY_RECORD.unpack_from(content, offset)
            iso = dt.datetime.strptime(str(date), "%Y%m%d").date().isoformat()
            if iso in records:
                raise ValueError(f"Duplicate quote date: {item['security_code']} {iso}")
            records[iso] = {"open": Decimal(opening) / 100, "high": Decimal(high) / 100,
                            "low": Decimal(low) / 100, "close": Decimal(close) / 100,
                            "volume_raw": volume}
        quotes[item["security_code"]] = records
        metadata.append({"code": item["security_code"], "path": str(path.relative_to(root.resolve())),
                         "sha256": digest, "records": len(records), "read_only": True})
    return quotes, metadata


def price_date_audit(sid: int, trades: list[dict], calendar: dict, quotes: dict):
    month_ends = {entry["date"] for entry in calendar["month_ends"]}
    market_dates = market_calendar(calendar)
    result = []
    for trade in trades:
        signal_date = trade["signal_date"]
        record = {"source_line": trade["source_line"], "code": trade["code"],
                  "signal_date": signal_date, "signal": trade["signal"], "native_price": trade["native_price"],
                  "forced_end_close": trade["forced_end_close"], "permission_inferred": False}
        if trade["forced_end_close"]:
            candidate = signal_date
            field = "close"
            record["date_check"] = "forced_close_separate_from_natural_signal"
        else:
            index = bisect.bisect_right(market_dates, signal_date)
            candidate = market_dates[index] if index < len(market_dates) else None
            field = "open"
            record["date_check"] = ("month_end" if signal_date in month_ends else "not_month_end") if sid == 3 else "market_signal_day_only_not_rule_recomputed"
            record["signal_is_market_trading_date"] = signal_date in market_dates
        record["candidate_execution_date"] = candidate
        record["candidate_price_field"] = field
        quote = quotes.get(trade["code"], {}).get(candidate)
        record["raw_candidate_price"] = None if quote is None else quote[field]
        record["raw_price_matches_native_display"] = None if quote is None else quote[field] == trade["native_price"]
        record["price_scope"] = "unadjusted_snapshot_candidate_only_front_adjusted_price_identity_not_assumed"
        result.append(record)
    ordinary = [row for row in result if not row["forced_end_close"]]
    return {"checks": result, "regular_count": len(ordinary), "forced_close_count": len(result) - len(ordinary),
            "s3_non_month_end_regular_rows": [row["source_line"] for row in ordinary if sid == 3 and row["date_check"] != "month_end"],
            "raw_next_open_match_count": sum(row["raw_price_matches_native_display"] is True for row in ordinary),
            "raw_next_open_mismatch_count": sum(row["raw_price_matches_native_display"] is False for row in ordinary),
            "raw_next_open_missing_count": sum(row["raw_price_matches_native_display"] is None for row in ordinary),
            "interpretation": "A mismatch does not prove wrong execution: front-adjusted price basis is unverified. A match does not prove execution permission or intraday path."}


def sum_decimal(rows, key):
    return sum((row[key] for row in rows), ZERO)


def audit_strategy(root: Path, sid: int, calendar: dict, quotes: dict, expected_codes: set[str], initial: Decimal):
    folder = root / "native-results" / f"S{sid:02d}"
    summary_path, ledger_path = folder / "native-summary.xls", folder / "native-trades-all.xls"
    result = {"sid": sid, "status": "not_exported", "native_return_pct": None, "source_files": []}
    if not summary_path.exists():
        return result
    raw_summary, summary_meta = read_native_tsv(summary_path, SUMMARY_COLUMNS)
    summary_meta["path"] = str(summary_path.relative_to(root))
    result["source_files"].append(summary_meta)
    rows = [parse_summary(row) for row in raw_summary]
    aggregate = [row for row in rows if row["aggregate"]]
    stocks = [row for row in rows if not row["aggregate"]]
    if len(aggregate) != 1:
        raise ValueError(f"S{sid:02d}: need one aggregate row")
    summary_by_code = {row["code"]: row for row in stocks}
    if len(summary_by_code) != len(stocks):
        raise ValueError(f"S{sid:02d}: duplicate summary security")
    result.update(status="summary_saved_ledger_missing", summary=rows,
                  native_return_pct=aggregate[0]["native_return_pct"],
                  summary_codes=sorted(summary_by_code), expected_codes=sorted(expected_codes),
                  summary_codes_match_expected=set(summary_by_code) == expected_codes)
    if not ledger_path.exists():
        return result
    raw_ledger, ledger_meta = read_native_tsv(ledger_path, TRADE_COLUMNS)
    ledger_meta["path"] = str(ledger_path.relative_to(root))
    result["source_files"].append(ledger_meta)
    trades = [parse_trade(row, i) for i, row in enumerate(raw_ledger, 2)]
    groups = collections.defaultdict(list)
    for trade in trades:
        groups[trade["code"]].append(trade)
    audits = []
    for code in sorted(expected_codes | set(groups) | set(summary_by_code)):
        group, native = groups[code], summary_by_code.get(code)
        pairing = pairing_audit(group)
        fee = sum_decimal(group, "native_fee")
        gross = sum_decimal(group, "native_gross_profit")
        cash = initial
        cash_differences = []
        for trade in group:
            if trade["signal"] == "买开":
                expected_cash = cash - trade["native_amount"] - trade["native_fee"]
            elif trade["signal"] in {"卖平", "平盘"}:
                expected_cash = cash + trade["native_amount"] - trade["native_fee"]
            else:
                expected_cash = None
            if expected_cash is not None and expected_cash != trade["native_available_cash"]:
                cash_differences.append({"source_line": trade["source_line"],
                                         "native_minus_displayed_cash_rollforward": trade["native_available_cash"] - expected_cash})
            cash = trade["native_available_cash"]
        fee_delta = None if native is None else fee - native["native_fees"]
        bound = HALF_CENT * (len(group) + 1)
        item = {"code": code, "name": native["name"] if native else None, "ledger_rows": len(group),
                "signals": dict(collections.Counter(row["signal"] for row in group)), **pairing,
                "native_total_times": native["total_times"] if native else None,
                "native_total_times_matches_roundtrips": None if native is None else native["total_times"] == pairing["completed_roundtrips"],
                "ledger_display_fees": fee, "summary_display_fees": None if native is None else native["native_fees"],
                "ledger_minus_summary_fee": fee_delta,
                "fee_display_rounding_bound": bound,
                "fee_delta_within_independent_cent_display_rounding_bound": None if fee_delta is None else abs(fee_delta) <= bound,
                "ledger_display_gross_profit": gross,
                "summary_native_net_profit": None if native is None else native["native_net_profit"],
                "gross_minus_displayed_fees_minus_native_net": None if native is None else gross - fee - native["native_net_profit"],
                "last_native_available_cash": None if not group else cash,
                "last_cash_minus_initial_minus_native_net": None if not group or native is None else cash - initial - native["native_net_profit"],
                "cash_rollforward_display_differences": cash_differences,
                "winning_close_rows_by_gross_profit": sum(row["native_gross_profit"] > 0 for row in group if row["signal"] in {"卖平", "平盘"}),
                "quantities_not_multiple_of_100_rows": [row["source_line"] for row in group if row["quantity"] % 100],
                "quantity_note": "Native exported shares preserved; no inference that real-market lot or permission rules were enforced."}
        if native and native["total_times"]:
            calculated_win = Decimal(native["winning_roundtrips"]) / native["total_times"] * 100
            item["count_derived_win_rate_pct"] = calculated_win
            item["native_win_rate_minus_count_derived_pct"] = None if native["native_win_rate_pct"] is None else native["native_win_rate_pct"] - calculated_win
        audits.append(item)
    total = aggregate[0]
    fee_ledger = sum_decimal(trades, "native_fee")
    net_component_sum = sum_decimal(stocks, "native_net_profit")
    total_roundtrips = sum(row["completed_roundtrips"] for row in audits)
    result.update(
        status="native_exports_audited_with_precision_and_price_scope_notes",
        ledger_rows=len(trades), ledger_codes=sorted(groups),
        ledger_codes_outside_expected=sorted(set(groups) - expected_codes),
        expected_trading_codes_missing_ledger=sorted(code for code in expected_codes if summary_by_code.get(code, {}).get("total_times", 0) and not groups[code]),
        total_completed_roundtrips=total_roundtrips, aggregate_total_times=total["total_times"],
        aggregate_total_times_matches_roundtrips=total["total_times"] == total_roundtrips,
        ledger_display_fee_sum=fee_ledger, summary_aggregate_display_fees=total["native_fees"],
        ledger_minus_aggregate_fee=fee_ledger - total["native_fees"],
        summary_component_fee_sum=sum_decimal(stocks, "native_fees"),
        summary_component_net_profit_sum=net_component_sum,
        summary_component_net_minus_aggregate=net_component_sum - total["native_net_profit"],
        per_security=audits, date_price_audit=price_date_audit(sid, trades, calendar, quotes),
        normalized_ledger=trades,
        money_interpretation="Decimal parses exported displayed values exactly; small nonzero native reconciliation residuals are retained. Underlying native precision is unavailable; no cent-exact ledger reconstruction is claimed.",
        native_metrics_scope="Native annualized return is the client's linear annualization, not CAGR. Native aggregate drawdown is not a reconstructed shared-portfolio daily-equity drawdown. Seven independent accounts are not a shared portfolio.",
    )
    for source in result["source_files"]:
        source["unchanged_after_read"] = sha256(root / source["path"]) == source["sha256"]
    return result


def json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


def markdown_report(data):
    lines = ["# 通达信原生导出审计", "", "读取GB18030制表文本；原始.xls导出、行情快照只读。金额用Decimal读取。未运行项保留N/A/null。", "",
             "原生年化为软件线性口径，不是CAGR。原生综合回撤不是七股共享组合的逐日权益回撤。", "",
             "| 策略 | 状态 | 逐笔行 | 往返次数 | 原生收益率% | 逐笔费用减综合费用(元) | 次开原始价相符/不符 |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for item in data["strategies"]:
        prices = item.get("date_price_audit", {})
        comparison = f"{prices['raw_next_open_match_count']}/{prices['raw_next_open_mismatch_count']}" if prices else "N/A"
        lines.append(f"| {item['sid']} | {item['status']} | {item.get('ledger_rows', 'N/A')} | {item.get('total_completed_roundtrips', 'N/A')} | {item.get('native_return_pct') if item.get('native_return_pct') is not None else 'N/A'} | {item.get('ledger_minus_aggregate_fee', 'N/A')} | {comparison} |")
    for item in data["strategies"]:
        if not item.get("per_security"):
            continue
        lines.extend(["", f"## 策略{item['sid']}", "", f"{item['ledger_rows']}条逐笔，{len(item['ledger_codes'])}只股票，{item['total_completed_roundtrips']}次往返；原生总次数{item['aggregate_total_times']}。",
                      "", "| 股票 | 逐笔行 | 往返 | 逐笔费用 | 原生汇总费用 | 差额 | 逐笔毛收益减费用减原生净利润 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for stock in item["per_security"]:
            lines.append(f"| {stock['code']} {stock['name']} | {stock['ledger_rows']} | {stock['completed_roundtrips']} | {stock['ledger_display_fees']} | {stock['summary_display_fees']} | {stock['ledger_minus_summary_fee']} | {stock['gross_minus_displayed_fees_minus_native_net']} |")
        prices = item["date_price_audit"]
        lines.extend(["", f"期末“平盘”{prices['forced_close_count']}条，与自然退出分开。" ])
        if item["sid"] == 3:
            lines.append(f"普通信号{prices['regular_count']}条，非市场月末的普通信号{len(prices['s3_non_month_end_regular_rows'])}条。")
        lines.extend(["", "前述原始价比较只检验下一市场交易日候选开盘，不假定前复权执行价等于未复权开盘；不符项必须保留价格口径未核验说明。即使相符，也不能证明交易许可、涨跌停可成交或盘中路径。", "", "价格样例：", "", "| 股票 | 信号日 | 原生价格 | 候选成交日 | 快照原始价格 | 相符 |", "| --- | --- | ---: | --- | ---: | --- |"])
        first_by_code = {}
        for check in prices["checks"]:
            if not check["forced_end_close"]:
                first_by_code.setdefault(check["code"], check)
        for check in first_by_code.values():
            lines.append(f"| {check['code']} | {check['signal_date']} | {check['native_price']} | {check['candidate_execution_date']} | {check['raw_candidate_price']} | {check['raw_price_matches_native_display']} |")
        lines.extend(["", "费用显示差额保留原值；处于逐笔四舍五入可能范围不等于证明底层账务逐分相等。毛收益、费用、可用现金与汇总净利润之间的差额也原样保存，不补造修正流水。", "", f"完整审计、逐笔原字段、输入哈希和所有候选价格比较见 S{item['sid']:02d}-audit.json。"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--strategy", action="append", type=int, help="Repeat to audit selected strategy IDs; default 3..21")
    parser.add_argument("--initial-capital", default="1000000", help="Confirmed independent per-security native initial cash, not an inferred market rule")
    args = parser.parse_args(argv)
    root = args.report_root.resolve()
    output = root / "native-audit"
    initial = decimal_value(args.initial_capital)
    manifest_path = root / "after-download" / "input-manifest.json"
    calendar_path = root / "calendar.json"
    native_manifest_path = root / "native-evaluation" / "manifest.json"
    manifest, calendar, native_manifest = map(read_json, (manifest_path, calendar_path, native_manifest_path))
    expected_codes = {item["security_code"] for item in manifest["securities"]}
    quotes, snapshot_sources = load_snapshot_quotes(root, manifest)
    unsupported = set(native_manifest.get("unsupported_sid", []))
    selected = sorted(set(args.strategy or range(3, 22)))
    results = []
    for sid in selected:
        try:
            item = audit_strategy(root, sid, calendar, quotes, expected_codes, initial)
            if item["status"] == "not_exported":
                item["status"] = "pending_implementation_or_native_validation" if sid in unsupported else "pending_native_export"
                entry = next((entry for entry in native_manifest.get("strategies", []) if entry["sid"] == sid), {})
                item["not_run_reason"] = entry.get("not_run_reason", "Native summary/ledger has not been saved yet.")
                item["permanent_platform_incapability_claim"] = False
            item["validation_scope"] = {"native_export_file_audit": item["status"].startswith("native_exports_audited"), "indicator_numeric_values_verified": False, "adjusted_execution_price_identity_verified": False, "preparation_exclusion_list_is_not_live_completion_state": True}
            results.append(item)
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            results.append({"sid": sid, "status": "audit_error", "error": str(exc), "native_return_pct": None})
    data = {"schema_version": 1, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "kind": "read_only_native_export_audit_not_new_backtest", "python": platform.python_version(),
            "initial_cash_per_security": initial, "expected_codes": sorted(expected_codes),
            "money_unit": "CNY", "decimal_policy": "Exact exported display values; JSON monetary/percentage numbers are strings, unavailable values are null.",
            "sources": [{"path": str(path.relative_to(root)), "sha256": sha256(path)} for path in (manifest_path, calendar_path, native_manifest_path)]
                       + [{"path": Path(__file__).name, "sha256": sha256(Path(__file__))}],
            "quotation_snapshots": snapshot_sources, "strategies": results,
            "limits": ["Does not recompute indicators or authorize trades.", "No execution permission or corporate-action treatment inferred from OHLCV.",
                       "Signal timestamps are preserved; candidate next-open dates are comparisons, not proven execution timestamps.",
                       "Front-adjusted native price basis is unverified wherever raw price differs.",
                       "Native annualized return is linear, not CAGR; native drawdown is not shared-portfolio daily equity drawdown."]}
    output.mkdir(exist_ok=True)
    for item in results:
        write_json(output / f"S{item['sid']:02d}-audit.json", item)
    write_json(output / "index.json", data)
    (output / "audit-summary.md").write_text(markdown_report(data), encoding="utf-8")
    print(json.dumps({"output": str(output), "strategies": [{"sid": row["sid"], "status": row["status"], "ledger_rows": row.get("ledger_rows"), "roundtrips": row.get("total_completed_roundtrips"), "fee_delta": row.get("ledger_minus_aggregate_fee")} for row in results]}, ensure_ascii=False, default=json_default))
    return 1 if any(row["status"] == "audit_error" for row in results) else 0


if __name__ == "__main__":
    sys.exit(main())
