"""Read-only .day audit and announcement-based calendar; produces no returns."""
from pathlib import Path
from datetime import date, datetime, timedelta
import bisect
import calendar
import hashlib
import json
import platform
import struct

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
PREV = ROOT / "reports/strategy2-watchlist-20230101-20261023-preparation"
START, END = date(2021, 1, 1), date(2026, 10, 23)
ACCESS_DATE = "2026-10-24"
old_calendar = json.loads((PREV / "calendar.json").read_text(encoding="utf-8-sig"))
old_audit = json.loads((PREV / "input-audit.json").read_text(encoding="utf-8-sig"))
old_hashes = {r["security_code"]: r["sha256"] for r in old_audit["securities"]}

early = {
    2021: {
        "published_date": "2020-12-24",
        "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20201224_5286949.shtml",
        "closed_ranges": [["2021-01-01", "2021-01-03"], ["2021-02-11", "2021-02-17"], ["2021-04-03", "2021-04-05"], ["2021-05-01", "2021-05-05"], ["2021-06-12", "2021-06-14"], ["2021-09-19", "2021-09-21"], ["2021-10-01", "2021-10-07"]],
    },
    2022: {
        "published_date": "2021-12-20",
        "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20211220_5662606.shtml",
        "closed_ranges": [["2022-01-01", "2022-01-03"], ["2022-01-31", "2022-02-06"], ["2022-04-03", "2022-04-05"], ["2022-04-30", "2022-05-04"], ["2022-06-03", "2022-06-05"], ["2022-09-10", "2022-09-12"], ["2022-10-01", "2022-10-07"]],
    },
}
sources = []
for year, info in early.items():
    sources.append({"id": f"sse-{year}", "year": year, "exchange": "SSE", "title": f"SSE {year} annual holiday closure notice", **info, "review_method": "Official search returned the full holiday section; direct official page also opened successfully on access date."})
for original in old_calendar["sources"]:
    sources.append({**original, "exchange": "SSE", "review_method": "Reused the previously reviewed official annual notice in strategy2 preparation; 2023-2026 dates are checked against that saved calendar."})
sz_urls = {
    2021: "https://www.szse.cn/disclosure/notice/general/t20201224_583950.html",
    2022: "https://www.szse.cn/disclosure/notice/t20211220_590321.html",
    2023: "https://www.szse.cn/disclosure/notice/general/t20221227_598022.html",
    2024: "https://www.szse.cn/disclosure/notice/t20231226_605108.html",
    2025: "https://www.szse.cn/disclosure/notice/t20241223_611283.html",
    2026: "https://www.szse.cn/disclosure/notice/t20251222_618087.html",
}
for sse in list(sources):
    year = sse["year"]
    method = "Official-domain search returned the full holiday section on access date; closure dates agree with SSE."
    if year == 2022:
        method += " Direct official page also opened successfully."
    if year in (2021, 2025, 2026):
        method += " Direct fetch timed out; do not treat it as a successful direct-page retrieval."
    if year == 2024:
        method += " The full text was from the exchange's official Traditional Chinese mirror."
    row = {"id": f"szse-{year}", "year": year, "exchange": "SZSE", "title": f"SZSE {year} holiday closure notice", "published_date": sse["published_date"], "url": sz_urls[year], "closed_ranges": sse["closed_ranges"], "review_method": method}
    if year == 2024:
        row["reviewed_mirror_url"] = "https://big5.szse.cn/site/cht/www.szse.cn/disclosure/notice/t20231226_605108.html"
    sources.append(row)

by_id = {s["id"]: s for s in sources}
closures = {x: [(date.fromisoformat(a), date.fromisoformat(b)) for s in sources if s["exchange"] == x for a, b in s["closed_ranges"]] for x in ("SSE", "SZSE")}
def is_open(d, exchange="SSE"):
    return d.weekday() < 5 and not any(a <= d <= b for a, b in closures[exchange])

month_ends = []
for year in range(2021, 2027):
    for month in range(1, 13):
        if (year, month) > (2026, 9):
            break
        last = date(year, month, calendar.monthrange(year, month)[1])
        while not is_open(last):
            last -= timedelta(days=1)
        next_day = last + timedelta(days=1)
        while not is_open(next_day):
            next_day += timedelta(days=1)
        used_years = sorted({last.year, next_day.year})
        ids = [f"{prefix}-{y}" for y in used_years for prefix in ("sse", "szse")]
        known = all(date.fromisoformat(by_id[s]["published_date"]) <= last for s in ids)
        assert known, (last, ids)
        month_ends.append({"month": f"{year}-{month:02}", "date": last.isoformat(), "tdx_date": int(last.strftime("%Y%m%d")) - 19000000, "next_market_date": next_day.isoformat(), "next_market_trading_day": next_day.isoformat(), "annual_source_id": f"sse-{year}", "annual_source_ids": [f"sse-{year}", f"szse-{year}"], "sources_used_including_next_market_date": ids, "annual_schedule_published_before_month_end": known})

expected_2021 = "2021-01-29 2021-02-26 2021-03-31 2021-04-30 2021-05-31 2021-06-30 2021-07-30 2021-08-31 2021-09-30 2021-10-29 2021-11-30 2021-12-31".split()
expected_2022 = "2022-01-28 2022-02-28 2022-03-31 2022-04-29 2022-05-31 2022-06-30 2022-07-29 2022-08-31 2022-09-30 2022-10-31 2022-11-30 2022-12-30".split()
assert [m["date"] for m in month_ends[:24]] == expected_2021 + expected_2022
assert [(m["date"], m["next_market_date"]) for m in month_ends[24:]] == [(m["date"], m["next_market_trading_day"]) for m in old_calendar["month_ends"]]
all_dates = [START + timedelta(days=i) for i in range((END - START).days + 1)]
assert all(is_open(d, "SSE") == is_open(d, "SZSE") for d in all_dates)
market_days = [d for d in all_dates if is_open(d)]
calendar_out = {"schema_version": 1, "kind": "official_announcement_based_market_month_end_calendar", "strategy_ids": list(range(3, 22)), "status": "calendar_preparation_only_no_returns", "generated_date": ACCESS_DATE, "access_date": ACCESS_DATE, "exchanges": ["SSE", "SZSE"], "requested_range": {"start": str(START), "end": str(END)}, "signal_calendar_range": {"start": "2021-01-01", "end": "2026-09-30"}, "count": len(month_ends), "tdx_date_encoding": "YYYYMMDD - 19000000", "method": "Enumerate civil dates, exclude Saturdays, Sundays and annual official holiday closure intervals, then choose each completed month's final open date. Determine the next open date from the same announced calendar, independently of security quotations.", "excluded_incomplete_month": "2026-10", "weekend_makeup_workdays_remain_closed": True, "sources": sources, "month_ends": month_ends, "checks": {"count_69": len(month_ends) == 69, "unique_and_ascending_dates": [m["date"] for m in month_ends] == sorted({m["date"] for m in month_ends}), "sse_szse_open_days_agree": True, "independent_2021_2022_month_end_list_agrees": True, "previous_45_dates_and_next_dates_unchanged": True, "all_used_sources_published_before_signal": True}, "limitations": ["A market-open date is not proof of a particular security's trading permission or bar availability.", "No suspension, limit-price permission, fees, adjustment or listing-date facts are inferred from the calendar.", "The calendar is input preparation, not formula compatibility or a native backtest result."]}
(BASE / "calendar.json").write_text(json.dumps(calendar_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

securities = []
for market, code in [("sh", "688017"), ("sh", "688322"), ("sh", "605488"), ("sh", "603728"), ("sz", "300750"), ("sh", "601899"), ("sz", "300308")]:
    path = Path("C:/new_tdx/vipdoc") / market / "lday" / f"{market}{code}.day"
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    assert len(blob) % 32 == 0
    ds = [datetime.strptime(str(struct.unpack_from("<I", blob, off)[0]), "%Y%m%d").date() for off in range(0, len(blob), 32)]
    assert ds and ds == sorted(set(ds)), code
    requested = [d for d in ds if START <= d <= END]
    ds_set = set(ds)
    missing = [d for d in market_days if d not in ds_set]
    interior_missing = [d for d in missing if ds[0] <= d <= ds[-1]]
    record_coverage = [{"month": m["month"], "calendar_date": m["date"], "has_day_record": date.fromisoformat(m["date"]) in ds_set, "records_up_to_date": bisect.bisect_right(ds, date.fromisoformat(m["date"])), "has_at_least_200_stored_records": bisect.bisect_right(ds, date.fromisoformat(m["date"])) >= 200} for m in month_ends]
    after_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after_digest == digest, code
    securities.append({"security_code": code, "market": market.upper(), "path": str(path), "bytes": len(blob), "record_size_bytes": 32, "record_count": len(ds), "sha256": digest, "sha256_after_read": after_digest, "source_unchanged_during_read": digest == after_digest, "matches_previous_strategy2_input_hash": digest == old_hashes[code], "file_mtime_local": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"), "first_stored_date": str(ds[0]), "last_stored_date": str(ds[-1]), "records_before_2021": sum(d < START for d in ds), "records_in_requested_range": len(requested), "first_date_in_requested_range": str(requested[0]), "last_date_in_requested_range": str(requested[-1]), "first_date_with_200_complete_stored_records": str(ds[199]) if len(ds) >= 200 else None, "has_200_stored_records_at_first_requested_record": bisect.bisect_right(ds, requested[0]) >= 200, "first_month_end_with_200_complete_stored_records": next((r["calendar_date"] for r in record_coverage if r["has_day_record"] and r["has_at_least_200_stored_records"]), None), "market_calendar_days_in_requested_range": len(market_days), "market_calendar_dates_without_security_record": [str(d) for d in missing], "calendar_dates_missing_before_first_stored_record_count": sum(d < ds[0] for d in missing), "calendar_dates_missing_inside_stored_span": [str(d) for d in interior_missing], "stored_dates_on_calendar_closed_day_in_requested_range": [str(d) for d in requested if not is_open(d)], "calendar_month_end_count": len(month_ends), "calendar_month_ends_with_day_record": sum(r["has_day_record"] for r in record_coverage), "missing_month_end_dates": [r["calendar_date"] for r in record_coverage if not r["has_day_record"]], "month_end_record_coverage": record_coverage, "coverage_caution": "The first stored date is local coverage, not a verified listing date. An absent bar is not by itself evidence of suspension or trading prohibition. No data are fabricated."})

audit_out = {"schema_version": 1, "kind": "strategies3_21_read_only_input_audit", "status": "inputs_audited_no_backtest_returns", "created_date": ACCESS_DATE, "strategy_ids": list(range(3, 22)), "requested_range": {"start": str(START), "end": str(END)}, "watchlist_verification": {"source": "Seven securities from prior task, independently awaiting parent agent current UI confirmation", "current_ui_verified_by_this_audit": False, "securities": [r["security_code"] for r in securities]}, "record_layout": "32-byte little-endian <IIIIIfII; first uint32 is YYYYMMDD; date audit only", "calendar_path": "calendar.json", "calendar_sha256": hashlib.sha256((BASE / "calendar.json").read_bytes()).hexdigest(), "audit_method": "Read exact source .day bytes, decode stored dates, count records and compare with official announced market calendar, hash before and after read. Never write to source files.", "runtime": {"python": platform.python_version(), "platform": platform.platform(), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, "securities": securities, "checks": {"seven_source_hashes_unchanged_during_audit": all(r["source_unchanged_during_read"] for r in securities), "seven_source_hashes_match_previous_strategy2_audit": all(r["matches_previous_strategy2_input_hash"] for r in securities), "all_last_stored_dates_20261023": all(r["last_stored_date"] == str(END) for r in securities), "no_generated_market_records": True}, "limitations": ["The requested 2021 start is not fully covered for all seven securities.", "200 stored records are an audited availability threshold, not an invented strategy warmup rule; actual formula requirements and initialization remain strategy-specific.", "688017 has only 84 stored records before 2021; five files start 2021-08-02; 688322 starts 2022-07-07.", "For a 200-record requirement, the earliest common availability date across all seven is 2023-05-04; a monthly signal's earliest common eligible month end is 2023-05-31.", "No returns, execution permissions, fee schedules, adjustment method or listing-date evidence are produced by this input audit."]}
evidence_paths = [BASE / "ui-evidence/current-watchlist-0.jpg", BASE / "ui-evidence/current-watchlist.txt"]
audit_out["watchlist_verification"] = {
    "source": "Parent agent confirmed the current native UI, and this audit inspected the saved screenshot: all seven codes and their order agree with the supplied watchlist.",
    "verification_date": ACCESS_DATE,
    "current_ui_verified": True,
    "current_ui_verified_by_this_audit": True,
    "inspection_method": "Saved current-watchlist-0.jpg visually reviewed; no UI interaction by the input-audit agent.",
    "securities": [r["security_code"] for r in securities],
    "evidence": [{"path": p.relative_to(BASE).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in evidence_paths],
}
(BASE / "input-audit.json").write_text(json.dumps(audit_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"month_end_count": len(month_ends), "market_days": len(market_days), "audit_checks": audit_out["checks"], "summary": [{k:r[k] for k in ("security_code", "record_count", "records_in_requested_range", "first_stored_date", "first_date_with_200_complete_stored_records", "calendar_month_ends_with_day_record", "calendar_dates_missing_before_first_stored_record_count", "calendar_dates_missing_inside_stored_span")} for r in securities]}, ensure_ascii=True))
