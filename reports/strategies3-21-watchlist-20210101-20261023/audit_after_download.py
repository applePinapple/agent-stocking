"""Snapshot and audit authorized post-download inputs without writing source files."""
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import Counter
import bisect
import hashlib
import json
import struct

BASE = Path(__file__).resolve().parent
OUT = BASE / "after-download"
SNAPS = OUT / "source-files"
START, EVAL_START, END = date(2020, 1, 1), date(2021, 1, 1), date(2026, 10, 23)
CAL = json.loads((BASE / "calendar.json").read_text(encoding="utf-8-sig"))
BEFORE = json.loads((BASE / "input-before-download-manifest.json").read_text(encoding="utf-8-sig"))
FIELDS = ["date", "open_int", "high_int", "low_int", "close_int", "amount_float32", "volume_uint", "reserved_uint"]
LAYOUT = "<IIIIIfII"
def sha(blob):
    return hashlib.sha256(blob).hexdigest()
def textjson(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

annual_ranges = [["2020-01-01", "2020-01-01"], ["2020-01-24", "2020-01-30"], ["2020-04-04", "2020-04-06"], ["2020-05-01", "2020-05-05"], ["2020-06-25", "2020-06-27"], ["2020-10-01", "2020-10-08"]]
new_sources = [
    {"id": "sse-2020-annual", "exchange": "SSE", "published_date": "2019-12-20", "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20191220_4969627.shtml", "closed_ranges": annual_ranges},
    {"id": "sse-2020-spring-extension", "exchange": "SSE", "published_date": "2020-01-27", "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml", "closed_ranges": [["2020-01-31", "2020-02-02"]]},
    {"id": "szse-2020-annual", "exchange": "SZSE", "published_date": "2019-12-20", "url": "https://www.szse.cn/disclosure/notice/general/t20191220_572766.html", "closed_ranges": annual_ranges},
    {"id": "szse-2020-spring-extension", "exchange": "SZSE", "published_date": "2020-01-27", "url": "https://investor.szse.cn/disclosure/notice/t20200127_573917.html", "closed_ranges": [["2020-01-31", "2020-02-02"]]},
]
sources = CAL["sources"] + new_sources
closures = {exchange: [(date.fromisoformat(a), date.fromisoformat(b)) for s in sources if s["exchange"] == exchange for a, b in s["closed_ranges"]] for exchange in ("SSE", "SZSE")}
def is_open(d, exchange="SSE"):
    return d.weekday() < 5 and not any(a <= d <= b for a, b in closures[exchange])
all_days = [START + timedelta(days=i) for i in range((END - START).days + 1)]
assert all(is_open(d, "SSE") == is_open(d, "SZSE") for d in all_days)
market_days = [d for d in all_days if is_open(d)]
eval_days = [d for d in market_days if d >= EVAL_START]
assert len([d for d in market_days if d.year == 2020]) == 243
assert len(eval_days) == 1405
listing = {
    "688017": {"date": "2020-08-28", "published_date": "2020-08-27", "url": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20200827_79102752.shtml"},
    "688322": {"date": "2022-07-07", "published_date": "2022-07-06", "url": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220706_86080462.shtml"},
    "605488": {"date": "2021-05-13", "published_date": "2021-05-12", "url": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210512_81574827.shtml"},
}

OUT.mkdir(exist_ok=True)
SNAPS.mkdir(exist_ok=True)
rows, differences = [], []
for original in BEFORE["securities"]:
    code = original["code"]
    src = Path(original["source"])
    before_path = BASE / original["snapshot"]
    old = before_path.read_bytes()
    assert sha(old) == original["sha256"]
    new = src.read_bytes()
    assert sha(src.read_bytes()) == sha(new), f"Source changed while reading {code}"
    assert len(new) % 32 == 0
    snapshot = SNAPS / src.name
    if snapshot.exists():
        assert snapshot.read_bytes() == new, f"Existing snapshot differs: {snapshot}"
    else:
        with snapshot.open("xb") as handle:
            handle.write(new)
    assert sha(snapshot.read_bytes()) == sha(new)
    old_map = {struct.unpack_from("<I", old, i)[0]: old[i:i+32] for i in range(0, len(old), 32)}
    new_map = {struct.unpack_from("<I", new, i)[0]: new[i:i+32] for i in range(0, len(new), 32)}
    ints = [struct.unpack_from("<I", new, i)[0] for i in range(0, len(new), 32)]
    assert ints == sorted(set(ints))
    ds = [datetime.strptime(str(i), "%Y%m%d").date() for i in ints]
    ds_set = set(ds)
    added = sorted(new_map.keys() - old_map.keys())
    removed = sorted(old_map.keys() - new_map.keys())
    changed = []
    field_counts = Counter()
    for day in sorted(old_map.keys() & new_map.keys()):
        ob, nb = old_map[day], new_map[day]
        if ob != nb:
            ov, nv = struct.unpack(LAYOUT, ob), struct.unpack(LAYOUT, nb)
            changes = [{"field": FIELDS[i], "before": ov[i], "after": nv[i], "before_raw_hex": ob[i*4:(i+1)*4].hex(), "after_raw_hex": nb[i*4:(i+1)*4].hex()} for i in range(8) if ob[i*4:(i+1)*4] != nb[i*4:(i+1)*4]]
            field_counts.update(c["field"] for c in changes)
            changed.append({"date": str(datetime.strptime(str(day), "%Y%m%d").date()), "fields": changes})
    listing_info = listing.get(code)
    expected_start = max(START, date.fromisoformat(listing_info["date"])) if listing_info else START
    expected = [d for d in market_days if expected_start <= d <= END]
    missing_expected = [str(d) for d in expected if d not in ds_set]
    span_missing = [str(d) for d in market_days if ds[0] <= d <= ds[-1] and d not in ds_set]
    requested = [d for d in ds if EVAL_START <= d <= END]
    first_eval = requested[0]
    coverage = [{"month": m["month"], "date": m["date"], "has_day_record": date.fromisoformat(m["date"]) in ds_set, "records_up_to_date": bisect.bisect_right(ds, date.fromisoformat(m["date"])), "has_at_least_200_stored_records": bisect.bisect_right(ds, date.fromisoformat(m["date"])) >= 200} for m in CAL["month_ends"]]
    row = {"security_code": code, "market": src.name[:2].upper(), "source_path": str(src), "snapshot_path": snapshot.relative_to(OUT).as_posix(), "sha256": sha(new), "sha256_after_read": sha(src.read_bytes()), "snapshot_sha256": sha(snapshot.read_bytes()), "bytes": len(new), "record_count": len(ds), "record_size_bytes": 32, "file_mtime_local": datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="seconds"), "first_stored_date": str(ds[0]), "last_stored_date": str(ds[-1]), "records_by_year": {str(year): sum(d.year == year for d in ds) for year in range(2020, 2027)}, "records_before_2021": sum(d < EVAL_START for d in ds), "records_in_evaluation_range": len(requested), "first_date_in_evaluation_range": str(first_eval), "last_date_in_evaluation_range": str(requested[-1]), "full_2021_start_calendar_coverage": all(d in ds_set for d in eval_days), "listing_date_evidence": listing_info, "expected_stored_coverage_start": str(expected_start), "missing_market_dates_from_listing_or_download_start": missing_expected, "missing_market_dates_inside_stored_span": span_missing, "stored_dates_on_market_closed_days": [str(d) for d in ds if not is_open(d)], "first_date_with_200_complete_stored_records": str(ds[199]), "records_before_first_evaluation_bar": bisect.bisect_left(ds, first_eval), "records_including_first_evaluation_bar": bisect.bisect_right(ds, first_eval), "has_200_records_before_first_evaluation_bar": bisect.bisect_left(ds, first_eval) >= 200, "has_200_records_including_first_evaluation_bar": bisect.bisect_right(ds, first_eval) >= 200, "first_evaluation_date_with_200_records_including_signal_bar": str(next(d for d in requested if bisect.bisect_right(ds, d) >= 200)), "calendar_month_ends_with_day_record": sum(c["has_day_record"] for c in coverage), "first_month_end_with_200_complete_stored_records": next((c["date"] for c in coverage if c["has_day_record"] and c["has_at_least_200_stored_records"]), None), "month_end_record_coverage": coverage, "dates_strictly_ascending_unique": True, "source_unchanged_during_audit": sha(src.read_bytes()) == sha(new)}
    assert row["source_unchanged_during_audit"] and not removed
    assert not span_missing and not missing_expected and not row["stored_dates_on_market_closed_days"]
    rows.append(row)
    differences.append({"security_code": code, "before_snapshot": "../" + original["snapshot"], "after_snapshot": row["snapshot_path"], "before_sha256": sha(old), "after_sha256": sha(new), "file_bytes_changed": old != new, "record_count_before": len(old_map), "record_count_after": len(new_map), "record_count_delta": len(new_map)-len(old_map), "first_stored_date_before": str(datetime.strptime(str(min(old_map)), "%Y%m%d").date()), "first_stored_date_after": str(ds[0]), "last_stored_date_before": str(datetime.strptime(str(max(old_map)), "%Y%m%d").date()), "last_stored_date_after": str(ds[-1]), "added_dates": added, "removed_dates": removed, "overwritten_existing_record_count": len(changed), "changed_field_counts": dict(field_counts), "ohlc_unchanged_on_overlapping_dates": not any(field_counts[f] for f in ("open_int", "high_int", "low_int", "close_int")), "overwritten_existing_records": changed})

evidence = sorted(f for f in (BASE / "ui-evidence").glob("download-*") if f.is_file() and (f.name.startswith("download-exact-scope-before-start") or f.name.startswith("download-completed")))
calendar_out = {"kind": "post_download_actual_market_day_coverage_audit_calendar", "status": "coverage_audit_only_no_2020_signals", "start": str(START), "end": str(END), "market_day_count": len(market_days), "2020_market_day_count": 243, "evaluation_market_day_count_2021_onward": 1405, "base_2021_2026_calendar": "../calendar.json", "base_calendar_sha256": sha((BASE / "calendar.json").read_bytes()), "additional_2020_sources": new_sources, "source_review": "Official-domain searches returned full holiday/listing announcement text on 2026-10-24; 2020 Spring Festival extension is separately applied.", "actual_market_days": [str(d) for d in market_days], "sse_szse_days_agree": True, "point_in_time_boundary": "The 2020 extension was published 2020-01-27. This calendar audits actual historical bar coverage; it must not be treated as a preannounced final January 2020 month-end plan before that publication. Existing 2021-2026 signal calendars are unchanged."}
textjson(OUT / "calendar-coverage.json", calendar_out)
textjson(OUT / "before-after-differences.json", {"kind": "authorized_download_raw_record_differences", "status": "no_backtest_returns", "record_layout": LAYOUT, "raw_field_units": "OHLC integer cents; amount stored float32; volume raw unsigned integer; last 4 bytes reserved/unknown. No execution permission inferred.", "securities": differences, "checks": {"no_old_dates_removed": all(not d["removed_dates"] for d in differences), "all_ohlc_unchanged_on_overlapping_dates": all(d["ohlc_unchanged_on_overlapping_dates"] for d in differences)}, "interpretation": "Download added earlier records and rewrote some overlapping amount/volume/reserved fields. Exact snapshots and per-field bytes are retained; no unsupported cause or permission semantics are assigned."})
audit_out = {"schema_version": 1, "kind": "authorized_post_download_read_only_input_audit", "status": "download_completed_inputs_audited_no_returns", "recorded_date": "2026-10-24", "download_range": {"start": str(START), "end": str(END)}, "evaluation_range": {"start": str(EVAL_START), "end": str(END)}, "record_layout": LAYOUT, "method": "Original .day files read only; exact new bytes copied into source-files; hashes checked before/after. Compare against untouched before-download copies by date and every 4-byte field.", "authorization_path": "../authorization.json", "before_manifest_path": "../input-before-download-manifest.json", "before_manifest_sha256": sha((BASE / "input-before-download-manifest.json").read_bytes()), "calendar_coverage_path": "calendar-coverage.json", "calendar_coverage_sha256": sha((OUT / "calendar-coverage.json").read_bytes()), "differences_path": "before-after-differences.json", "generator_sha256": sha(Path(__file__).read_bytes()), "download_ui_evidence": [{"path": "../" + f.relative_to(BASE).as_posix(), "sha256": sha(f.read_bytes())} for f in evidence], "securities": rows, "checks": {"seven_sources_snapshotted": len(rows) == 7, "all_source_copy_hashes_match": all(r["sha256"] == r["snapshot_sha256"] == r["sha256_after_read"] for r in rows), "all_sources_stable_while_audited": all(r["source_unchanged_during_audit"] for r in rows), "no_source_truncated_to_2024": all(r["first_stored_date"] < "2024-01-01" for r in rows), "all_last_dates_20261023": all(r["last_stored_date"] == str(END) for r in rows), "no_missing_market_dates_since_known_listing_or_download_start": all(not r["missing_market_dates_from_listing_or_download_start"] for r in rows), "no_old_dates_removed": all(not d["removed_dates"] for d in differences), "ohlc_unchanged_on_all_overlap": all(d["ohlc_unchanged_on_overlapping_dates"] for d in differences), "before_snapshots_preserved": all(sha((BASE / r["snapshot"]).read_bytes()) == r["sha256"] for r in BEFORE["securities"])}, "limitations": ["No strategy backtest returns are generated by this audit.", "A 200-record threshold is an availability fact; actual indicator warmup, recursion and native boundaries remain strategy-specific.", "688017, 688322 and 605488 start on officially verified IPO dates; earlier dates are prelisting, not bars to fabricate.", "No suspension/limit-price permission is inferred from OHLCV or the reserved word.", "Raw source hashes identify these post-download inputs; prior hashes remain valid only for the preserved pre-download snapshot.", "Current-watchlist historical testing remains exposed to sample selection/survivorship and historical signal censoring biases."]}
assert all(audit_out["checks"].values())
textjson(OUT / "input-audit.json", audit_out)
textjson(OUT / "input-manifest.json", {"schema_version": 1, "status": "post_download_snapshots_verified", "created_date": "2026-10-24", "source_write_by_auditor": False, "record_layout": LAYOUT, "audit_path": "input-audit.json", "audit_sha256": sha((OUT / "input-audit.json").read_bytes()), "securities": [{k:r[k] for k in ("security_code", "market", "source_path", "snapshot_path", "sha256", "snapshot_sha256", "bytes", "record_count", "first_stored_date", "last_stored_date")} for r in rows]})
print(json.dumps({"checks": audit_out["checks"], "summary": [{k:r[k] for k in ("security_code", "first_stored_date", "record_count", "records_before_2021", "first_date_with_200_complete_stored_records", "has_200_records_before_first_evaluation_bar", "calendar_month_ends_with_day_record")} for r in rows]}, ensure_ascii=True))
