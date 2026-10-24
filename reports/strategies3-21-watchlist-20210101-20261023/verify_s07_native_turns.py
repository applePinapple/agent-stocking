"""Read-only native SAR/SARTURN initialization, turn and fill-date audit.

This is not an implementation of the native recursive SAR algorithm and does
not calculate returns or infer execution permission. Native three-input SAR
is never asserted to be numerically identical to the four-input system line.
"""
import csv
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP
from hashlib import sha256
import io
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation/additional-6-8"
SCALE = D(1048576)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read_export(path):
    blob = path.read_bytes()
    table = list(csv.reader(io.StringIO(blob.decode("gb18030")), delimiter="\t"))
    hi = next(i for i, row in enumerate(table) if "CHECKALL.TURN3" in [s.strip() for s in row])
    head = [s.strip() for s in table[hi]]
    v2 = "CHECKALL.SARX" in head
    names = {"count": "CHECKALL.BC", "turn": "CHECKALL.TURN3", "fq": "CHECKALL.FQ",
             "sar": "CHECKALL.SARX" if v2 else "CHECKALL.SAR3",
             "open": "CHECKALL.OX" if v2 else "开盘",
             "high": "CHECKALL.HX" if v2 else "最高",
             "low": "CHECKALL.LX" if v2 else "最低",
             "close": "CHECKALL.CX" if v2 else "收盘"}
    indices = {k: head.index(v) for k, v in names.items()}
    rows = []
    for line in table[hi + 1:]:
        if not line or "/" not in line[0]:
            continue
        day = date.fromisoformat(line[0].strip().replace("/", "-"))
        row = {"date": day.isoformat()}
        for key, index in indices.items():
            value = D(line[index].strip()) if line[index].strip() else None
            if v2 and key in {"open", "high", "low", "close", "sar"} and value is not None:
                value /= SCALE
            row[key] = value
        assert row["count"] == len(rows) and row["fq"] == 1
        assert row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]
        rows.append(row)
    assert rows and [r["date"] for r in rows] == sorted({r["date"] for r in rows})
    return blob, rows, v2


def audit(code):
    source = BASE / "native-evaluation/per-strategy/S07-expert.txt"
    expert_sha = digest(source)
    path = BASE / f"native-inputs/{code}-CHECKALL-v2.txt"
    blob, rows, v2 = read_export(path)
    assert v2 and code in blob.decode("gb18030").splitlines()[0]
    provenance_path = BASE / "native-inputs/export-provenance.jsonl"
    provenance_blob = provenance_path.read_bytes()
    provenance = [json.loads(s) for s in provenance_blob.decode("utf-8-sig").splitlines() if s.strip()]
    binding = [r for r in provenance if r.get("path") == path.relative_to(BASE).as_posix()
               and r.get("sha256") == sha256(blob).hexdigest()]
    assert binding, "Native v2 source/provenance binding required"
    binding = binding[-1]
    assert binding.get("source", binding.get("native_indicator_source")) == "native-evaluation/CHECKALL-precision-v2.txt"
    assert binding["source_sha256"] == "f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e"
    check_source = BASE / binding.get("source", binding.get("native_indicator_source"))
    assert digest(check_source) == binding["source_sha256"] and binding.get("scale") == 1048576
    assert binding.get("code", binding.get("security")) == code
    enum_values = {D(-1), D(0), D(1)}
    initialization = [{"date": r["date"], "native_BC": r["count"], "native_sar": r["sar"],
                       "native_turn": r["turn"], "expert_ready": r["count"] >= 4}
                      for r in rows if r["count"] < 4]
    assert all(r["turn"] in enum_values and r["sar"] is not None for r in rows[4:])
    checks, turns, actions = [], [], []
    holding = False
    for i in range(4, len(rows)):
        current, previous = rows[i], rows[i - 1]
        # This comparison uses actual native exported SAR and OHLC. It does
        # not approximate its hidden recursive state or infer an intrabar path.
        prev_direction = (previous["close"] > previous["sar"]) - (previous["close"] < previous["sar"])
        direction = (current["close"] > current["sar"]) - (current["close"] < current["sar"])
        expected_turn = D(direction) if direction and prev_direction and direction != prev_direction else D(0)
        monotonic = True if current["turn"] else ((current["sar"] >= previous["sar"]) if direction == 1 else current["sar"] <= previous["sar"] if direction == -1 else None)
        check = {"date": current["date"], "native_BC": current["count"], "native_turn": current["turn"],
                 "previous_close_minus_sar": previous["close"] - previous["sar"],
                 "close_minus_sar": current["close"] - current["sar"],
                 "sar": current["sar"], "previous_sar": previous["sar"],
                 "direction": direction, "previous_direction": prev_direction,
                 "turn_matches_native_SAR_side_change": current["turn"] == expected_turn,
                 "same_direction_SAR_monotonic": monotonic,
                 "zero_side_requires_separate_equality_review": not direction or not prev_direction}
        checks.append(check)
        if current["turn"] not in {-1, 1}:
            continue
        event = {**check, "type": "up_turn" if current["turn"] == 1 else "down_turn",
                 "next_exported_session": rows[i + 1]["date"] if i + 1 < len(rows) else None,
                 "next_exported_open": rows[i + 1]["open"] if i + 1 < len(rows) else None}
        turns.append(event)
        if not "2021-01-01" <= current["date"] <= "2026-10-23" or i + 1 >= len(rows):
            continue
        if current["turn"] == 1 and not holding:
            actions.append({"signal_date": current["date"], "signal": "买开", "candidate_execution_date": rows[i + 1]["date"], "candidate_export_open": rows[i + 1]["open"]})
            holding = True
        elif current["turn"] == -1 and holding:
            actions.append({"signal_date": current["date"], "signal": "卖平", "candidate_execution_date": rows[i + 1]["date"], "candidate_export_open": rows[i + 1]["open"]})
            holding = False
    if holding:
        actions.append({"signal_date": rows[-1]["date"], "signal": "平盘", "candidate_execution_date": rows[-1]["date"], "candidate_export_close": rows[-1]["close"]})
    result = {"status": "native_turn_observations_checked_not_a_new_backtest", "security_code": code,
              "rows": len(rows), "native_BC_origin": 0, "expert_ready_predicate": "BARSCOUNT(C)>=4, actual native 0-based count",
              "expert_source_sha256": expert_sha, "native_export_sha256": sha256(blob).hexdigest(),
              "native_export_path": path.relative_to(BASE).as_posix(), "native_indicator_source_sha256": digest(check_source),
              "native_provenance_binding": binding,
              "official_function_semantics_url": "https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html",
              "initialization_observations": initialization,
              "post_warmup_turns_are_minus1_0_plus1": True,
              "checked_post_warmup_rows": len(checks), "turn_count": len(turns),
              "up_turn_count": sum(r["native_turn"] == 1 for r in turns),
              "down_turn_count": sum(r["native_turn"] == -1 for r in turns),
              "all_turns_match_exported_native_SAR_side_changes": all(r["turn_matches_native_SAR_side_change"] for r in checks),
              "all_nonturn_SAR_moves_monotonically": all(r["same_direction_SAR_monotonic"] is True for r in checks),
              "all_unambiguous_side_changes_match_native_turn": all(r["turn_matches_native_SAR_side_change"] for r in checks if not r["zero_side_requires_separate_equality_review"]),
              "all_unambiguous_nonturn_SAR_moves_monotonically": all(r["same_direction_SAR_monotonic"] is True for r in checks if not r["zero_side_requires_separate_equality_review"]),
              "equality_rows": [r for r in checks if r["zero_side_requires_separate_equality_review"]],
              "turns": turns, "expected_long_only_signal_lifecycle": actions, "still_held_before_native_terminal_model": holding,
              "native_three_input_last_SAR": rows[-1]["sar"],
              "scope_limits": ["Native three-input SAR/SARTURN outputs are audited; four-input system SAR numeric identity is not asserted.",
                               "The first SAR/SARTURN initialized value can be a price rather than a turn code and is excluded by actual native BARSCOUNT>=4.",
                               "Next exported session prices are candidates pending ledger comparison; no fill permission, cash, costs or returns are inferred.",
                               "Current watchlist selection and native terminal/signal censoring remain explicit historical biases."],
              "all_post_warmup_rows": checks, "returns_calculated": False}
    ledger_path = BASE / "native-results/S07/native-trades-all.xls"
    if ledger_path.exists():
        ledger_blob = ledger_path.read_bytes()
        table = list(csv.reader(io.StringIO(ledger_blob.decode("gb18030")), delimiter="\t"))
        native = [r for r in table[1:] if r and r[0] == code]
        observed = [(r[2][:10], r[3]) for r in native]
        expected = [(r["signal_date"], r["signal"]) for r in actions]
        result["native_ledger_sha256"] = sha256(ledger_blob).hexdigest()
        result["all_native_lifecycle_dates_and_types_match"] = observed == expected
        result["native_security_rows"] = len(native)
        result["ledger_row_price_checks"] = [{"signal_date": row[2][:10], "signal": row[3], "native_price": D(row[4]),
                                              "candidate_price": event.get("candidate_export_open", event.get("candidate_export_close")),
                                              "matches_export_price_rounded_to_native_2dp": D(row[4]) == event.get("candidate_export_open", event.get("candidate_export_close")).quantize(D("0.01"), rounding=ROUND_HALF_UP)}
                                             for row, event in zip(native, actions)] if observed == expected else []
    result["source_unchanged_after_read"] = (path.read_bytes() == blob and digest(check_source) == binding["source_sha256"] and digest(source) == expert_sha)
    OUT.mkdir(exist_ok=True)
    output = OUT / f"S07-{code}-native-turn-check.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k in {"security_code", "rows", "checked_post_warmup_rows", "turn_count", "up_turn_count", "down_turn_count", "all_turns_match_exported_native_SAR_side_changes", "all_nonturn_SAR_moves_monotonically", "native_three_input_last_SAR", "source_unchanged_after_read", "all_native_lifecycle_dates_and_types_match", "native_security_rows"}}, default=str))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="688322", choices=["300308", "300750", "601899", "603728", "605488", "688017", "688322"])
    audit(parser.parse_args().code)
