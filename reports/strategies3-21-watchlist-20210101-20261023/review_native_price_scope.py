"""Read-only price-basis candidates, never a fill/permission verification."""
from bisect import bisect_right
from collections import defaultdict
import csv
from decimal import Decimal, getcontext
from hashlib import sha256
import io
import json
from pathlib import Path
import struct

getcontext().prec = 36
D = Decimal
BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation"
TOL = D("0.011")  # Candidate-search residual only, not execution validation tolerance.
MIN_POINTS = 4


def digest(data):
    return sha256(data).hexdigest()


def fit(points):
    n = D(len(points))
    sx = sum((p["raw_next_open"] for p in points), D(0))
    sy = sum((p["native_price"] for p in points), D(0))
    sxx = sum((p["raw_next_open"] ** 2 for p in points), D(0))
    sxy = sum((p["raw_next_open"] * p["native_price"] for p in points), D(0))
    denominator = sxx - sx * sx / n
    if denominator <= 0:
        return None
    a = (sxy - sx * sy / n) / denominator
    b = sy / n - a * sx / n
    errors = [p["native_price"] - a * p["raw_next_open"] - b for p in points]
    return {"a": a, "b": b, "max_abs_residual": max(abs(v) for v in errors), "mean_abs_residual": sum(abs(v) for v in errors) / n}


def partition(points):
    """Descriptive segmentation: minimum unresolved points, then fewest segments.

    Neither boundaries nor coefficients are treated as corporate-action facts.
    A segment needs >=4 distinct sample dates and a positive slope.
    """
    count = len(points)
    best = [None] * (count + 1)
    best[0] = ((0, 0), [])
    for end in range(1, count + 1):
        old_score, old_parts = best[end - 1]
        best[end] = ((old_score[0] + 1, old_score[1]), old_parts + [("unresolved", end - 1, end, None)])
        for start in range(end - MIN_POINTS + 1):
            result = fit(points[start:end])
            if not result or result["a"] <= 0 or result["max_abs_residual"] > TOL:
                continue
            previous, parts = best[start]
            score = (previous[0], previous[1] + 1)
            if score < best[end][0]:
                best[end] = (score, parts + [("candidate", start, end, result)])
    candidates, unresolved = [], []
    for kind, start, end, result in best[-1][1]:
        sample = points[start:end]
        if kind == "unresolved":
            unresolved.extend(sample)
            continue
        candidates.append({
            "kind": "statistical_candidate_NOT_verified_adjustment_factor",
            "first_sample_signal_date": sample[0]["signal_date"],
            "last_sample_signal_date": sample[-1]["signal_date"],
            "sample_count": len(sample),
            "formula": "native_display_price approximately a * raw_next_stored_open + b",
            **result,
            "samples_for_native_UI_review": [sample[i] for i in sorted({0, len(sample)//2, len(sample)-1})],
            "all_signal_dates": [p["signal_date"] for p in sample],
        })
    return candidates, unresolved


def decimal_json(value):
    if isinstance(value, D):
        return str(value)
    raise TypeError(type(value).__name__)


def main():
    # Synthetic arithmetic check, not prices or returns: recover y=0.7*x-3.
    synthetic = [{"raw_next_open": D(x), "native_price": D("0.7") * D(x) - 3} for x in [10, 20, 30, 40]]
    check = fit(synthetic)
    assert check["a"] == D("0.7") and check["b"] == D(-3) and check["max_abs_residual"] == 0

    input_manifest = BASE / "after-download/input-manifest.json"
    inputs = json.loads(input_manifest.read_text(encoding="utf-8-sig"))
    raw, source_hashes = {}, []
    for security in inputs["securities"]:
        path = BASE / "after-download" / security["snapshot_path"]
        blob = path.read_bytes()
        assert digest(blob) == security["snapshot_sha256"]
        rows = list(struct.iter_unpack("<IIIIIfII", blob))
        code = security["security_code"]
        raw[code] = [{"date": str(r[0]), "open": D(r[1])/100, "close": D(r[4])/100} for r in rows]
        source_hashes.append({"security_code": code, "path": str(path.relative_to(BASE)), "sha256": digest(blob), "source": "after-download snapshot"})

    records, terminal, ledgers = [], [], []
    for sid in ["S03", "S04"]:
        path = BASE / "native-results" / sid / "native-trades-all.xls"
        blob = path.read_bytes()
        data = csv.reader(io.StringIO(blob.decode("gb18030")), delimiter="\t")
        count = 0
        for row in data:
            if len(row) < 7 or row[0] not in raw:
                continue
            code, signal = row[0], row[2][:10]
            price = D(row[4])
            dates = [r["date"] for r in raw[code]]
            encoded = signal.replace("-", "")
            index = bisect_right(dates, encoded)
            item = {"strategy": sid, "security_code": code, "signal_date": signal, "native_signal_label": row[3], "native_price": price}
            count += 1
            if row[3] == "\u5e73\u76d8" or index == len(dates):
                item.update({"handling": "terminal_row_excluded_from_next_open_fit", "last_raw_close": raw[code][-1]["close"]})
                terminal.append(item)
                continue
            next_bar = raw[code][index]
            item.update({"raw_next_stored_date": f'{next_bar["date"][:4]}-{next_bar["date"][4:6]}-{next_bar["date"][6:]}', "raw_next_open": next_bar["open"], "difference_from_raw_next_open": price-next_bar["open"]})
            records.append(item)
        assert digest(path.read_bytes()) == digest(blob), "Ledger changed during review; rerun from a stable copy."
        ledgers.append({"strategy": sid, "path": str(path.relative_to(BASE)), "sha256": digest(blob), "row_count": count})

    groups = defaultdict(list)
    for row in records:
        groups[row["security_code"]].append(row)
    securities = []
    for code, rows in sorted(groups.items()):
        # Duplicate observations from two strategies do not inflate a candidate's sample count.
        unique = {}
        conflicts = []
        for row in rows:
            key = (row["signal_date"], row["native_price"], row["raw_next_open"])
            unique.setdefault(key, dict(row, observed_in=[]))["observed_in"].append(row["strategy"])
        same_date = defaultdict(set)
        for row in rows:
            same_date[row["signal_date"]].add(row["native_price"])
        conflicts = [{"date": day, "native_prices": sorted(values)} for day, values in same_date.items() if len(values)>1]
        points = sorted(unique.values(), key=lambda r: (r["signal_date"], r["native_price"]))
        candidates, unresolved = partition(points)
        securities.append({"security_code": code, "ledger_observations": len(rows), "unique_price_date_observations": len(points), "direct_raw_next_open_equal_within_0_005": sum(abs(r["difference_from_raw_next_open"])<=D("0.005") for r in rows), "raw_next_open_difference_min": min(r["difference_from_raw_next_open"] for r in rows), "raw_next_open_difference_max": max(r["difference_from_raw_next_open"] for r in rows), "same_date_price_conflicts": conflicts, "affine_candidates": candidates, "unresolved_observations": unresolved})

    formula_path = OUT / "B321NAT-expert.txt"
    formula_bytes = formula_path.read_bytes()
    normalized = formula_bytes.replace(b"\r\n", b"\n")
    native_manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8-sig"))
    signal_evidence = json.loads((OUT / "signal-causality-evidence.json").read_text(encoding="utf-8-sig"))
    line_review = {"physical_bytes": len(formula_bytes), "crlf_count": formula_bytes.count(b"\r\n"), "lf_normalized_bytes": len(normalized), "physical_sha256": digest(formula_bytes), "lf_normalized_sha256": digest(normalized), "manifest_sha256_matches_physical": native_manifest["formula_sha256"] == digest(formula_bytes), "signal_evidence_sha256_matches_physical": signal_evidence["formula_sha256"] == digest(formula_bytes), "old_manifest_length_was_logical_text_characters": native_manifest["formula_ascii_bytes"] == len(normalized), "signals_need_rerun_for_this_length_discrepancy": False}
    assert line_review["manifest_sha256_matches_physical"] and line_review["signal_evidence_sha256_matches_physical"]

    gbbq = Path("C:/new_tdx/T0002/hq_cache/gbbq")
    cache = {"path": str(gbbq), "exists": gbbq.is_file(), "decoded_or_validated_for_7_symbols": False}
    if gbbq.is_file():
        blob = gbbq.read_bytes()
        cache.update({"bytes": len(blob), "sha256": digest(blob)})
    review = {
        "generated_date": "2026-10-24",
        "review_script_sha256": digest(Path(__file__).read_bytes()),
        "status": "candidate_price_basis_scope_only_NOT_price_verification_passed",
        "market_returns_recomputed": False,
        "execution_permission_inferred": False,
        "native_UI_operated": False,
        "post_download_input_manifest_sha256": digest(input_manifest.read_bytes()),
        "raw_snapshot_sources": source_hashes,
        "ledger_sources": ledgers,
        "normal_observations": len(records),
        "terminal_rows_excluded_from_next_open_fit": terminal,
        "segmentation_method": {"arithmetic": "Decimal precision36", "minimum_sample_observations": MIN_POINTS, "candidate_search_max_abs_residual": str(TOL), "selection": "minimize unresolved observations then segment count; positive affine slope; sparse ledger dates only", "not_a_pass_threshold": True, "no_corporate_action_dates_inferred_from_OHLC": True},
        "securities": securities,
        "existing_project_evidence": {"real_7_symbol_adjustment_factor_series_found": False, "only_688017_rights_UI_snapshot_found": "reports/strategy1-688017-20230101-20261023/evidence/corporate-actions-tdx-uia.txt", "units_and_full_event_cash_timing_previously_unconfirmed": True, "cash_dividend_engine_tests_are_synthetic_not_market_factor_evidence": True, "client_market_cache": cache},
        "formula_line_ending_review": line_review,
        "statistical_method_synthetic_arithmetic_check": "exact recovery of a=0.7,b=-3 from synthetic points; not market evidence",
        "limitations": ["Piecewise affine fits are descriptive candidates built from the same ledger being studied; in-sample agreement cannot establish adjustment formula, factor validity, date alignment, absence of delayed fills, or permission.", "Next stored .day bar is a lookup candidate, not proof of the next tradable market session. No OHLCV permission inference.", "Terminal native rows are separated and never automatically matched to a nonexistent next bar.", "Sparse ledger dates cannot locate corporate-action boundaries or establish behavior between sampled dates.", "Native display prices have two decimals; candidate search tolerance is not a fill accuracy standard.", "A raw binary market cache exists but has no validated parser/schema in this project; it is not used to fabricate an adjustment reconstruction."]
    }
    (OUT / "native-price-scope-review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2, default=decimal_json)+"\n", encoding="utf-8")
    print(json.dumps({"status": review["status"], "ledger_rows": sum(r["row_count"] for r in ledgers), "normal_observations": len(records), "terminal_excluded": len(terminal), "securities": [{"code": s["security_code"], "observations": s["ledger_observations"], "raw_equal": s["direct_raw_next_open_equal_within_0_005"], "segments": len(s["affine_candidates"]), "unresolved": len(s["unresolved_observations"])} for s in securities], "line_ending_review": line_review}))


if __name__ == "__main__":
    main()
