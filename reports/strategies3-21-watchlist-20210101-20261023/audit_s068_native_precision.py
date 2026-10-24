"""Native-v2 S08 ledger and S06 recursion/enclosure input audit.

No money, fees, returns or execution permission are recalculated. S06 checks
the independent Decimal mathematical model on actual native front-adjusted
inputs; the local-client UNKNOWN/BADINPUT diagnostic remains a separate test.
S08 retains exact-decoded Decimal and binary32 arithmetic candidate boundaries.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import struct

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation/additional-6-8"
CODES = ["300308", "300750", "601899", "603728", "605488", "688017", "688322"]
TICK = D("0.01")
SCALE = D(1048576)
START, END = "2021-01-01", "2026-10-23"
V2_SOURCE = "native-evaluation/CHECKALL-precision-v2.txt"
V2_SOURCE_SHA = "f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e"


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


READ = module("s7_native_reader", "verify_s07_native_turns.py")
S06 = module("s6_exact_interval", "prepare_s06_interval_formula.py")
SPLITV2 = module("s6_split_v2_labels", "prepare_s06_split_v2.py")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def f32(value):
    return D.from_float(struct.unpack("<f", struct.pack("<f", float(value)))[0])


def price2(value):
    return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def load(code):
    path = BASE / f"native-inputs/{code}-CHECKALL-v2.txt"
    blob, rows, v2 = READ.read_export(path)
    assert v2 and code in blob.decode("gb18030").splitlines()[0]
    provenance_path = BASE / "native-inputs/export-provenance.jsonl"
    provenance_blob = provenance_path.read_bytes()
    records = [json.loads(s) for s in provenance_blob.decode("utf-8-sig").splitlines() if s.strip()]
    binding = [r for r in records if r.get("path") == path.relative_to(BASE).as_posix() and r.get("sha256") == sha256(blob).hexdigest()]
    assert binding, "Native source hash binding required"
    binding = binding[-1]
    assert binding.get("source", binding.get("native_indicator_source")) == V2_SOURCE
    assert binding["source_sha256"] == V2_SOURCE_SHA, "Only this approved immutable native v2 source is covered"
    source = BASE / binding.get("source", binding.get("native_indicator_source"))
    assert digest(source) == binding["source_sha256"] and binding.get("scale") == 1048576
    assert binding.get("code", binding.get("security")) == code
    manifest_path = BASE / "after-download/input-manifest.json"
    manifest_blob = manifest_path.read_bytes()
    manifest = json.loads(manifest_blob.decode("utf-8-sig"))
    item = next(r for r in manifest["securities"] if r["security_code"] == code)
    raw_path = BASE / "after-download" / item["snapshot_path"]
    raw_blob = raw_path.read_bytes()
    assert sha256(raw_blob).hexdigest() == item["snapshot_sha256"]
    dates = []
    for offset in range(0, len(raw_blob), 32):
        packed = struct.unpack_from("<I", raw_blob, offset)[0]
        dates.append(date(packed // 10000, (packed // 100) % 100, packed % 100).isoformat())
    assert [r["date"] for r in rows] == dates, "Native v2 date inventory mismatch"
    paths = [path, source, raw_path, manifest_path, Path(__file__), Path(READ.__file__)]
    hashes = {p: digest(p) for p in paths}
    hashes.update({path: sha256(blob).hexdigest(), source: binding["source_sha256"],
                   raw_path: sha256(raw_blob).hexdigest(), manifest_path: sha256(manifest_blob).hexdigest()})
    return rows, binding, hashes


def unchanged(hashes):
    return all(digest(path) == value for path, value in hashes.items())


def source_records(hashes):
    return [{"path": p.relative_to(BASE).as_posix(), "sha256": h} for p, h in hashes.items()]


def channel_flags(rows, mode):
    result = []
    for i, row in enumerate(rows):
        if i < 5:
            result.append({"buy": False, "sell": False})
            continue
        previous = rows[i - 5:i]
        high, low = max(r["high"] for r in previous), min(r["low"] for r in previous)
        upper, lower = high + TICK, low - TICK
        current_high, current_low = row["high"], row["low"]
        if mode == "binary32_candidate":
            high, low = max(f32(r["high"]) for r in previous), min(f32(r["low"]) for r in previous)
            upper, lower = f32(high + f32(TICK)), f32(low - f32(TICK))
            current_high, current_low = f32(current_high), f32(current_low)
        ready = row["count"] >= 6
        sell = ready and current_low <= lower
        buy = ready and current_high >= upper
        result.append({"buy": buy and not sell, "sell": sell, "both_sides_touched": buy and sell,
                       "previous_five_high": high, "previous_five_low": low,
                       "upper": upper, "lower": lower,
                       "current_high_minus_upper": current_high - upper,
                       "current_low_minus_lower": current_low - lower})
    return result


def channel_lifecycle(rows, flags):
    """Only this batch's separately verified force_end_close=True convention."""
    actions, holding = [], False
    for i, (row, flag) in enumerate(zip(rows, flags)):
        if not START <= row["date"] <= END or i + 1 >= len(rows):
            continue
        if holding and flag["sell"]:
            actions.append((row["date"], "卖平"))
            holding = False
        elif not holding and flag["buy"]:
            actions.append((row["date"], "买开"))
            holding = True
    if holding:
        actions.append((rows[-1]["date"], "平盘"))
    return actions


def audit_s08(code):
    rows, binding, hashes = load(code)
    run_manifest = BASE / "run-manifest.json"
    run_blob = run_manifest.read_bytes()
    config = json.loads(run_blob.decode("utf-8-sig"))["client_configuration"]
    assert config["force_end_close_included"] is True, "This historical-batch audit requires explicit terminal-close configuration"
    assert config["entry_price"] == config["exit_price"] == "次周期开盘价"
    assert config["price_adjustment"] == "前复权"
    hashes[run_manifest] = sha256(run_blob).hexdigest()
    formula = BASE / "native-evaluation/per-strategy/S08-expert.txt"
    ledger = BASE / "native-results/S08/native-trades-all.xls"
    hashes.update({formula: digest(formula), ledger: digest(ledger)})
    ledger_blob = ledger.read_bytes()
    table = list(csv.reader(io.StringIO(ledger_blob.decode("gb18030")), delimiter="\t"))
    assert table[0][:5] == ["品种代码", "品种名称", "时间", "信号", "价格(元)"]
    native = [r for r in table[1:] if r and r[0] == code]
    assert native, "This security has no native ledger rows"
    dec = channel_flags(rows, "exact_decoded_Decimal")
    single = channel_flags(rows, "binary32_candidate")
    decoded_actions = channel_lifecycle(rows, dec)
    candidate_actions = channel_lifecycle(rows, single)
    observed_actions = [(r[2][:10], r[3]) for r in native]
    index = {r["date"]: i for i, r in enumerate(rows)}
    checks = []
    for line in native:
        day, side = line[2][:10], line[3]
        i = index[day]
        if side == "平盘":
            assert i == len(rows) - 1
            expected, execution_day, kind = rows[i]["close"], day, "explicit_native_terminal_close"
        else:
            assert side in {"买开", "卖平"} and i + 1 < len(rows)
            expected, execution_day, kind = rows[i + 1]["open"], rows[i + 1]["date"], "next_exported_session_open"
        key = "buy" if side == "买开" else "sell"
        checks.append({"signal_date": day, "signal": side, "native_price_display": D(line[4]),
                       "candidate_execution_date": execution_day, "candidate_price_kind": kind,
                       "actual_native_front_adjusted_input_price": expected,
                       "native_price_matches_input_at_display_precision": D(line[4]) == price2(expected),
                       "exact_decoded_Decimal_signal_condition": side == "平盘" or dec[i][key],
                       "binary32_candidate_signal_condition": side == "平盘" or single[i][key],
                       "exact_decoded_Decimal_boundaries": dec[i], "binary32_candidate_boundaries": single[i]})
    arithmetic_differences = [{"date": row["date"], "native_BC": row["count"], "current_high": row["high"], "current_low": row["low"],
                               "exact_decoded_Decimal": a, "binary32_candidate": b}
                              for row, a, b in zip(rows, dec, single)
                              if a["buy"] != b["buy"] or a["sell"] != b["sell"]]
    # Validate that float32 rounding is a witness compatible with every saved
    # scaled two-decimal OHLC cell; do not call this internal arithmetic proof.
    witness = all(price2(f32(r[k]) * SCALE) == price2(r[k] * SCALE)
                  for r in rows for k in ("open", "high", "low", "close"))
    result = {"kind": "actual_native_v2_channel_ledger_audit_not_financial_replay", "security_code": code,
              "native_rows": len(rows), "native_security_trade_rows": len(native), "native_input_binding": binding,
              "native_BARSCOUNT_origin": 0, "ready_condition": "BC>=6", "MINDIFF": str(TICK),
              "explicit_native_client_configuration": config,
              "MINDIFF_basis": "Root-observed local-client minimum tick .01 for all seven requested A shares",
              "price_basis": "Actual saved native front-adjusted FQ=1 precision-v2 OHLC; raw inventory used only for date coverage",
              "all_native_display_fill_prices_match": all(r["native_price_matches_input_at_display_precision"] for r in checks),
              "exact_decoded_Decimal_lifecycle_matches_native": decoded_actions == observed_actions,
              "binary32_candidate_lifecycle_matches_native": candidate_actions == observed_actions,
              "all_scaled_OHLC_cells_have_consistent_binary32_witness": witness,
              "exact_decoded_Decimal_expected_actions": decoded_actions,
              "binary32_candidate_expected_actions": candidate_actions, "native_actions": observed_actions,
              "arithmetic_boundary_difference_rows": arithmetic_differences,
              "all_native_trade_checks": checks, "sources": source_records(hashes),
              "no_permission_inference": True, "returns_calculated": False,
              "limits": ["Binary32 is an explicitly named arithmetic candidate corroborated, when true, by the actual native lifecycle; it is not an unobserved client arithmetic guarantee.",
                         "Exact-decoded Decimal boundary differences are retained; display rounding is never used to decide a trading signal.",
                         "Ordinary entries/exits are completed-H/L channel triggers followed by next-session opens; this is the authorized native variant, not an intrabar dynamic stop order.",
                         "No daily price-limit, liquidity, permission, volume unit, cash, quantity or fee assumptions are inferred.",
                         "Current front-adjustment vintage, current-watchlist selection and native terminal/signal censoring biases remain."]}
    assert unchanged(hashes) and ledger.read_bytes() == ledger_blob, "Source changed during read-only audit"
    result["source_hashes_unchanged_before_output"] = True
    OUT.mkdir(exist_ok=True)
    path = OUT / f"S08-{code}-native-v2-check.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {k: result[k] for k in ("security_code", "native_rows", "native_security_trade_rows", "all_native_display_fill_prices_match", "exact_decoded_Decimal_lifecycle_matches_native", "binary32_candidate_lifecycle_matches_native", "all_scaled_OHLC_cells_have_consistent_binary32_witness")}
    summary["arithmetic_boundary_differences"] = len(arithmetic_differences)
    print(json.dumps(summary), flush=True)
    return summary


def audit_s06(code):
    rows, binding, hashes = load(code)
    formula = BASE / "native-evaluation/per-strategy/S06-expert.txt"
    reference_script = BASE / "prepare_s06_interval_formula.py"
    hashes.update({formula: digest(formula), reference_script: digest(reference_script)})
    assert formula.read_text(encoding="ascii") == S06.formula_source(128)[0], "Saved expert no longer equals the 128-layer mathematical model"
    hashes[BASE / "prepare_s06_split_v2.py"] = digest(BASE / "prepare_s06_split_v2.py")
    for name, generated in zip(SPLITV2.FILES, SPLITV2.generate()):
        saved = formula.parent / name
        assert saved.read_text(encoding="ascii") == generated, "Saved v2 split source no longer equals the unchanged 64+64 expansion with repaired output labels"
        hashes[saved] = digest(saved)
    inputs = [{"C": r["close"], "H": r["high"], "L": r["low"]} for r in rows]
    upper, lower, up = S06.recursive_reference(inputs)
    ulo, uhi, llo, lhi, may_up, may_down = S06.expanded_enclosures(inputs, 128)
    exact = [a != b for a, b in zip(may_up, may_down)]
    window = [i for i, r in enumerate(rows) if START <= r["date"] <= END and i >= 10]
    invalid = [i for i, r in enumerate(rows) if any(not r[k].is_finite() for k in ("close", "high", "low")) or r["high"] < r["low"]]
    unknown = [i for i in window if not exact[i] or not exact[i - 1]]
    not_enclosed = [i for i in range(len(rows)) if not (ulo[i] <= upper[i] <= uhi[i] and llo[i] <= lower[i] <= lhi[i])]
    direction_diff = [i for i in range(len(rows)) if exact[i] and may_up[i] != up[i]]
    signal_differences = [i for i in window if exact[i] and exact[i - 1] and ((may_up[i] and may_down[i - 1]) != (up[i] and not up[i - 1]) or (may_down[i] and may_up[i - 1]) != (not up[i] and up[i - 1]))]
    result = {"kind": "actual_native_front_adjusted_inputs_independent_Decimal_S06_model_check_not_native_runtime_diagnostic",
              "security_code": code, "native_input_binding": binding, "rows": len(rows), "requested_window_signal_eligible_rows": len(window),
              "interval_expansion_depth": 128, "model_BADINPUT_count": len(invalid), "model_UNKNOWN_count": len(unknown),
              "saved_full_expert_matches_generated_128_layer_source": True,
              "saved_v2_64_plus_64_helper_expert_diagnostic_match_generated_equivalent_sources": True,
              "v2_helper_output_names": SPLITV2.RENAMES,
              "v2_label_inverse_substitution_restores_v1_sources_byte_for_byte": True,
              "all_recursive_bands_enclosed": not not_enclosed, "all_certified_directions_equal_full_recursion": not direction_diff,
              "all_certified_signal_dates_equal_full_recursion": not signal_differences,
              "unknown_dates": [rows[i]["date"] for i in unknown], "bad_input_dates": [rows[i]["date"] for i in invalid],
              "not_enclosed_dates": [rows[i]["date"] for i in not_enclosed], "direction_difference_dates": [rows[i]["date"] for i in direction_diff],
              "signal_difference_dates": [rows[i]["date"] for i in signal_differences],
              "native_UI_UNKNOWN": None, "native_UI_BADINPUT": None,
              "native_UI_status": "Requires same-history/FQ/period local-client S06 indicator diagnostic; independent Decimal arithmetic cannot certify native evaluation state",
              "sources": source_records(hashes), "returns_calculated": False,
              "limits": ["SMA true-range10/multiplier3/first10 bearish initialization local variant; no claim of source-platform identity.",
                         "Every input is actual saved native FQ=1 high-precision OHLC, not raw price substitution.",
                         "The mathematical interval/full-recursion comparison does not establish native numeric arithmetic, formula compilation, execution permission or returns."]}
    assert unchanged(hashes)
    result["source_hashes_unchanged_before_output"] = True
    OUT.mkdir(exist_ok=True)
    path = OUT / f"S06-{code}-native-v2-model-check.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {k: result[k] for k in ("security_code", "rows", "requested_window_signal_eligible_rows", "model_BADINPUT_count", "model_UNKNOWN_count", "all_recursive_bands_enclosed", "all_certified_directions_equal_full_recursion", "all_certified_signal_dates_equal_full_recursion")}
    print(json.dumps(summary), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("6", "8", "all"), required=True)
    parser.add_argument("--code", choices=CODES + ["all"], required=True)
    args = parser.parse_args()
    codes = CODES if args.code == "all" else [args.code]
    strategies = ["6", "8"] if args.strategy == "all" else [args.strategy]
    summaries = {sid: [(audit_s06 if sid == "6" else audit_s08)(code) for code in codes]
                 for sid in strategies}
    if args.code == args.strategy == "all":
        reports = [OUT / f"S06-{code}-native-v2-model-check.json" for code in CODES]
        reports += [OUT / f"S08-{code}-native-v2-check.json" for code in CODES]
        index = {"kind": "independent_native_input_math_and_ledger_audit_not_financial_summary",
                 "status": "saved_with_native_S08_boundary_observations_and_native_S06_UI_diagnostics_still_required",
                 "securities": CODES, "summaries": summaries,
                 "report_sources": [{"path": p.relative_to(BASE).as_posix(), "sha256": digest(p)} for p in reports],
                 "audit_script_sha256": digest(Path(__file__)), "returns_calculated": False}
        (OUT / "S06-S08-native-v2-independent-index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
