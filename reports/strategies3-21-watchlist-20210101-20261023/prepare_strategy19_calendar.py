"""Prepare fixed market-date signals. No quotation or execution input is read."""
from pathlib import Path
from datetime import date, timedelta
import calendar
import hashlib
import json

BASE = Path(__file__).resolve().parent
CUTOFF = date(2026, 10, 23)
calendar_path = BASE / "calendar.json"
data = json.loads(calendar_path.read_text(encoding="utf-8"))
source_map = {s["id"]: s for s in data["sources"]}
closures = [(date.fromisoformat(a), date.fromisoformat(b)) for s in data["sources"] if s["exchange"] == "SSE" for a, b in s["closed_ranges"]]

def is_open(d):
    return d.weekday() < 5 and not any(a <= d <= b for a, b in closures)

def month_days(year, month):
    return [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1) if is_open(date(year, month, day))]

def to_tdx(d):
    return int(d.strftime("%Y%m%d")) - 19000000

events = []
for year in range(2021, 2027):
    for month in range(1, 13):
        if (year, month) > (2026, 10):
            break
        days = month_days(year, month)
        assert len(days) >= 5
        buy = days[-5]
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        next_days = month_days(ny, nm)
        assert len(next_days) >= 3
        sell = next_days[2]
        buy_source_ids = [f"{prefix}-{year}" for prefix in ("sse", "szse")]
        sell_source_ids = [f"{prefix}-{ny}" for prefix in ("sse", "szse")]
        source_ids = sorted(set(buy_source_ids + sell_source_ids))
        buy_known = all(date.fromisoformat(source_map[s]["published_date"]) <= buy for s in buy_source_ids)
        sell_known = all(date.fromisoformat(source_map[s]["published_date"]) <= sell for s in sell_source_ids)
        sell_known_at_buy = all(date.fromisoformat(source_map[s]["published_date"]) <= buy for s in sell_source_ids)
        assert buy_known and sell_known
        # Independently verify the ordinal positions by counting open civil dates.
        following = [date(year, month, day) for day in range(buy.day + 1, calendar.monthrange(year, month)[1] + 1) if is_open(date(year, month, day))]
        preceding = [date(ny, nm, day) for day in range(1, sell.day) if is_open(date(ny, nm, day))]
        assert len(following) == 4 and len(preceding) == 2
        events.append({"buy_month": f"{year}-{month:02}", "buy_market_day": str(buy), "buy_tdx_date": to_tdx(buy), "market_days_in_buy_month": len(days), "market_days_after_buy_in_same_month": len(following), "sell_month": f"{ny}-{nm:02}", "sell_market_day": str(sell), "sell_tdx_date": to_tdx(sell), "market_days_before_sell_in_same_month": len(preceding), "buy_in_active_expression": buy <= CUTOFF, "sell_in_active_expression": sell <= CUTOFF, "buy_source_ids": buy_source_ids, "sell_source_ids": sell_source_ids, "sources_used": source_ids, "buy_sources_published_before_buy": buy_known, "sell_sources_published_before_sell": sell_known, "sell_schedule_already_published_at_buy": sell_known_at_buy})

active_buys = [e["buy_tdx_date"] for e in events if e["buy_in_active_expression"]]
active_sells = [e["sell_tdx_date"] for e in events if e["sell_in_active_expression"]]
assert len(events) == 70 and len(active_buys) == len(active_sells) == 69
assert events[0]["buy_market_day"] == "2021-01-25" and events[0]["sell_market_day"] == "2021-02-03"
assert events[11]["buy_market_day"] == "2021-12-27" and events[11]["sell_market_day"] == "2022-01-06"
assert events[23]["buy_market_day"] == "2022-12-26" and events[23]["sell_market_day"] == "2023-01-05"
assert events[48]["buy_market_day"] == "2025-01-21" and events[48]["sell_market_day"] == "2025-02-07"
assert events[-1]["buy_market_day"] == "2026-10-26" and events[-1]["sell_market_day"] == "2026-11-04"

def expression(prefix, values):
    lines = []
    identifiers = []
    for i, year in enumerate(range(2021, 2027)):
        selected = [v for v in values if (v + 19000000) // 10000 == year]
        if selected:
            ident = prefix + chr(ord("A") + i)
            identifiers.append(ident)
            lines.append(ident + ":=(" + " OR ".join(f"DATE={v}" for v in selected) + ");")
    lines.append(prefix + ":=(" + " OR ".join(identifiers) + ");")
    return "\n".join(lines)

formula = "{STRATEGY19 DATE INPUTS ONLY; CUTOFF 20261023; NATIVE COMPILE/BOUNDARIES PENDING}\n" + expression("B19", active_buys) + "\n\n" + expression("S19", active_sells) + "\n"
formula_path = BASE / "strategy19-date-expressions.txt"
formula_path.write_text(formula, encoding="utf-8")
out = {"schema_version": 1, "kind": "strategy19_fixed_market_date_preparation", "status": "pending_native_formula_validation_no_returns", "strategy_id": 19, "generated_date": "2026-10-24", "evaluation_cutoff": str(CUTOFF), "buy_month_range": {"start": "2021-01", "end": "2026-10"}, "rule": {"buy": "Fifth-to-last market trading day of each calendar month, close", "sell": "Third market trading day of the immediately following calendar month, close"}, "market_calendar": {"path": "calendar.json", "sha256": hashlib.sha256(calendar_path.read_bytes()).hexdigest(), "source_detail_path": "calendar-sources.md", "exchanges": ["SSE", "SZSE"], "sources": [{k:s[k] for k in ("id", "published_date", "url")} for s in data["sources"]]}, "quotation_data_read": False, "sell_schedule_not_yet_published_at_buy_months": [e["buy_month"] for e in events if not e["sell_schedule_already_published_at_buy"]], "formula_path": formula_path.name, "formula_sha256": hashlib.sha256(formula_path.read_bytes()).hexdigest(), "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "planned_pair_count": len(events), "active_buy_date_count": len(active_buys), "active_sell_date_count": len(active_sells), "active_buy_tdx_dates": active_buys, "active_sell_tdx_dates": active_sells, "events": events, "checks": {"70_monthly_plans": len(events) == 70, "69_historical_buy_and_sell_dates": len(active_buys) == len(active_sells) == 69, "positions_checked_by_independent_counts": True, "all_buy_sources_preannounced_before_buy": all(e["buy_sources_published_before_buy"] for e in events), "all_sell_sources_preannounced_before_sell": all(e["sell_sources_published_before_sell"] for e in events), "october_buy_and_november_sell_excluded_from_active_expression": not events[-1]["buy_in_active_expression"] and not events[-1]["sell_in_active_expression"], "no_future_quotation_or_security_bar_count_used": True}, "limitations": ["The source strategy is pending. This calendar is input preparation, not authorization of fees, execution timing changes or a return calculation.", "Dates are market-wide anchors. A missing security bar or suspension never shifts the market ordinal to the next security bar.", "The local first stored date is not a verified IPO date. Pre-listing, suspension and incomplete download require separate evidence.", "Buy/sell failures and end holdings remain governed by user-confirmed rules, not an added rollover assumption.", "Unelapsed October 2026 buy and November sell are stored as plans only, and excluded from active expressions.", "Static DATE comparisons do not read future quotations. This does not replace actual native compile and output checks."]}
(BASE / "strategy19-calendar.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
rows = ["|买点所属月份|倒数第5市场日买点|DATE|次月第3市场日卖点|DATE|截至2026-10-23状态|", "|---|---|---:|---|---:|---|"]
for e in events:
    status = "已到日期；未证明实际成交" if e["buy_in_active_expression"] and e["sell_in_active_expression"] else "仅计划；不进入活动信号"
    rows.append(f'|{e["buy_month"]}|{e["buy_market_day"]}|{e["buy_tdx_date"]}|{e["sell_market_day"]}|{e["sell_tdx_date"]}|{status}|')
(BASE / "strategy19-date-table.md").write_text("# 策略19固定市场日历清单\n\n本表是预先公告日历的日期准备，不是回测收益或成交记录。完整来源与边界见`strategy19-calendar-notes.md`。\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
print(json.dumps({"planned_pairs": len(events), "active_buy_dates": len(active_buys), "active_sell_dates": len(active_sells), "last_active_pair": events[-2], "future_plan": events[-1]}, ensure_ascii=True))
