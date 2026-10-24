"""Read-only complete S08 688322 native ledger/date/next-open comparison.

No corporate actions for this security is a root-provided scope premise; raw
OHLC is not substituted for other securities' front-adjusted data. No returns
are calculated and no execution permission is inferred.
"""
import bisect
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import struct

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation" / "additional-6-8"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    ledger_path = BASE / "native-results" / "S08" / "native-trades-all.xls"
    source_path = BASE / "native-evaluation" / "per-strategy" / "S08-expert.txt"
    manifest_path = BASE / "after-download" / "input-manifest.json"
    calendar_path = BASE / "calendar.json"
    manifest = read_json(manifest_path)
    item = next(item for item in manifest["securities"] if item["security_code"] == "688322")
    quote_path = BASE / "after-download" / item["snapshot_path"]
    assert digest(quote_path) == item["snapshot_sha256"]
    content = quote_path.read_bytes()
    quotes = []
    for offset in range(0, len(content), 32):
        day, opening, high, low, close, *_ = struct.unpack_from("<IIIIIfII", content, offset)
        quotes.append({"date": date(day // 10000, (day // 100) % 100, day % 100),
                       "open": Decimal(opening) / 100, "high": Decimal(high) / 100,
                       "low": Decimal(low) / 100, "close": Decimal(close) / 100})
    indices = {row["date"].isoformat(): i for i, row in enumerate(quotes)}
    calendar = read_json(calendar_path)
    closed = set()
    for source in calendar["sources"]:
        for first, last in source["closed_ranges"]:
            day, end = date.fromisoformat(first), date.fromisoformat(last)
            while day <= end:
                closed.add(day)
                day += timedelta(days=1)
    start, end = date(2021, 1, 1), date(2026, 10, 23)
    market_dates = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in closed:
            market_dates.append(day)
        day += timedelta(days=1)
    ledger_bytes = ledger_path.read_bytes()
    all_rows = list(csv.DictReader(io.StringIO(ledger_bytes.decode("gb18030")), delimiter="\t"))
    native = [row for row in all_rows if row["品种代码"] == "688322"]
    tick = Decimal("0.01")
    checks = []
    for row in native:
        signal_date = row["时间"][:10]
        index = indices[signal_date]
        quote = quotes[index]
        previous = quotes[max(0, index - 5):index]
        high_threshold = max(item["high"] for item in previous) + tick
        low_threshold = min(item["low"] for item in previous) - tick
        buy = len(previous) == 5 and index >= 6 and quote["high"] >= high_threshold
        sell = len(previous) == 5 and index >= 6 and quote["low"] <= low_threshold
        forced = row["信号"] == "平盘"
        native_price = Decimal(row["价格(元)"])
        if forced:
            execution_date = quote["date"]
            expected_price = quote["close"]
            signal_ok = quote["date"] == end
        else:
            next_market_index = bisect.bisect_right(market_dates, quote["date"])
            execution_date = market_dates[next_market_index] if next_market_index < len(market_dates) else None
            next_quote = quotes[index + 1] if index + 1 < len(quotes) else None
            expected_price = None if next_quote is None else next_quote["open"]
            assert next_quote is None or next_quote["date"] == execution_date, "Missing/suspended quote requires separate treatment"
            signal_ok = buy and not sell if row["信号"] == "买开" else sell if row["信号"] == "卖平" else False
        checks.append({"signal_date": signal_date, "signal": row["信号"], "forced_end_close": forced,
                       "previous_five_dates": [item["date"].isoformat() for item in previous],
                       "channel_upper_plus_tick": str(high_threshold), "channel_lower_minus_tick": str(low_threshold),
                       "current_high": str(quote["high"]), "current_low": str(quote["low"]),
                       "buy_condition": buy, "sell_condition": sell, "native_signal_condition_matches": signal_ok,
                       "candidate_execution_date": None if execution_date is None else execution_date.isoformat(),
                       "candidate_price_field": "end_close" if forced else "next_market_open",
                       "native_price": str(native_price), "raw_candidate_price": None if expected_price is None else str(expected_price),
                       "price_exact_decimal_match": native_price == expected_price,
                       "permission_inferred": False})
    # Independent long-only signal lifecycle, not a cash/permission simulator.
    expected_events, holding = [], False
    for i, quote in enumerate(quotes):
        if not start <= quote["date"] <= end or i < 6 or i + 1 >= len(quotes):
            continue
        previous = quotes[i - 5:i]
        buy = quote["high"] >= max(x["high"] for x in previous) + tick
        sell = quote["low"] <= min(x["low"] for x in previous) - tick
        if holding and sell:
            expected_events.append((quote["date"].isoformat(), "卖平"))
            holding = False
        elif not holding and buy and not sell:
            expected_events.append((quote["date"].isoformat(), "买开"))
            holding = True
    if holding:
        expected_events.append((end.isoformat(), "平盘"))
    actual_events = [(row["时间"][:10], row["信号"]) for row in native]
    result = {
        "status": "pass" if all(check["native_signal_condition_matches"] and check["price_exact_decimal_match"] for check in checks) and expected_events == actual_events else "differences_found",
        "kind": "real_native_ledger_vs_raw_688322_signal_and_price_check_not_new_backtest",
        "security_code": "688322", "scope_premise": "Root confirms no corporate actions for 688322 in this interval; no cross-security price-basis conclusion.",
        "native_total_ledger_rows": len(all_rows), "security_ledger_rows": len(native),
        "native_buy_rows": sum(row["信号"] == "买开" for row in native),
        "native_regular_sell_rows": sum(row["信号"] == "卖平" for row in native),
        "native_forced_close_rows": sum(row["信号"] == "平盘" for row in native),
        "signal_date_condition_matches": sum(row["native_signal_condition_matches"] for row in checks),
        "price_exact_matches": sum(row["price_exact_decimal_match"] for row in checks),
        "independent_signal_lifecycle_matches_native_all_dates": expected_events == actual_events,
        "expected_signal_event_count": len(expected_events),
        "tick_used": str(tick), "native_BARSCOUNT_origin_used": 0,
        "ledger_timestamp_interpretation": "Source signal date; candidate next-market-open date independently derived from exchange calendar.",
        "sources": [{"path": str(path.relative_to(BASE)), "sha256": digest(path)} for path in (ledger_path, source_path, manifest_path, quote_path, calendar_path)],
        "all_record_checks": checks, "returns_calculated": False, "permission_inferred": False,
        "limits": ["A matching date and price does not prove fill permission, liquidity, price-limit handling or real-market round lots.",
                   "Signal lifecycle comparison uses repeated-entry-ignore convention without simulating cash or rejection; equality is an observed match, not independent fill proof.",
                   "Other securities' front-adjusted prices still require their own price-basis checks."]}
    result["source_hashes_unchanged_after_read"] = all(digest(BASE / row["path"]) == row["sha256"] for row in result["sources"])
    OUT.mkdir(exist_ok=True)
    output_path = OUT / "S08-688322-real-native-check.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ["status", "native_total_ledger_rows", "security_ledger_rows", "native_buy_rows", "native_regular_sell_rows", "native_forced_close_rows", "signal_date_condition_matches", "price_exact_matches", "independent_signal_lifecycle_matches_native_all_dates", "source_hashes_unchanged_after_read"]}, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
