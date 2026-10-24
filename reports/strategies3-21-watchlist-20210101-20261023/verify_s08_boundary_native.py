"""Read-only observed native S8CHECK booleans versus the saved S08 ledger.

No tolerance is fitted. The native comparison outputs themselves are retained;
cash, fees, permission and returns are not re-simulated.
"""
import argparse
import csv
from decimal import Decimal as D, ROUND_HALF_UP
from hashlib import sha256
import io
import json
from pathlib import Path

import audit_s068_native_precision as COMMON

BASE = Path(__file__).resolve().parent
SCALE = D(1048576)
FIELDS = ["FQ", "BC", "TICKX", "HX", "LX", "UPX", "DNX", "BH", "SL", "BUY8", "SELL8", "HMX", "LMX"]
SCALED = {"TICKX", "HX", "LX", "UPX", "DNX", "HMX", "LMX"}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--code", required=True, choices=COMMON.CODES)
    args = parser.parse_args()
    source = BASE / "native-evaluation/per-strategy/S08-boundary-indicator-check.txt"
    path = Path(args.input).resolve()
    blob = path.read_bytes()
    provenance_path = BASE / "native-inputs/export-provenance.jsonl"
    records = [json.loads(s) for s in provenance_path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    binding = [r for r in records if r.get("path") == path.relative_to(BASE).as_posix()
               and r.get("sha256") == sha256(blob).hexdigest()]
    assert binding, "Actual native boundary-export provenance required"
    binding = binding[-1]
    assert binding.get("source", binding.get("native_indicator_source")) == source.relative_to(BASE).as_posix()
    assert binding["source_sha256"] == digest(source) and binding.get("code", binding.get("security")) == args.code
    table = list(csv.reader(io.StringIO(blob.decode("gb18030")), delimiter="\t"))
    assert args.code in table[0][0]
    hi = next(i for i, r in enumerate(table) if any(s.strip().endswith(".TICKX") for s in r))
    head = [s.strip() for s in table[hi]]
    prefix = next(s[:-len("TICKX")] for s in head if s.endswith(".TICKX"))
    indices = {k: head.index(prefix + k) for k in FIELDS}
    rows = []
    for line in table[hi + 1:]:
        if not line or "/" not in line[0]:
            continue
        row = {"date": line[0].strip().replace("/", "-")}
        for key, index in indices.items():
            text = line[index].strip()
            value = D(text) if text else None
            row[key] = value / SCALE if value is not None and key in SCALED else value
        assert row["FQ"] == 1 and row["BC"] == len(rows)
        assert row["TICKX"].quantize(D("0.01"), rounding=ROUND_HALF_UP) == D("0.01")
        if row["BC"] >= 6:
            assert all(row[k] in (0, 1) for k in ["BH", "SL", "BUY8", "SELL8"])
            assert row["BUY8"] == int(bool(row["BH"]) and not row["SL"])
            assert row["SELL8"] == row["SL"]
        else:
            assert row["BUY8"] in (None, 0) and row["SELL8"] in (None, 0)
        rows.append(row)
    inputs, input_binding, hashes = COMMON.load(args.code)
    hashes.update({path: digest(path), source: digest(source), Path(__file__): digest(Path(__file__))})
    run_manifest = BASE / "run-manifest.json"
    run_blob = run_manifest.read_bytes()
    client_config = json.loads(run_blob.decode("utf-8-sig"))["client_configuration"]
    assert client_config["force_end_close_included"] is True
    assert client_config["entry_price"] == client_config["exit_price"] == "次周期开盘价"
    assert client_config["price_adjustment"] == "前复权"
    hashes[run_manifest] = sha256(run_blob).hexdigest()
    assert len(rows) == len(inputs) and [r["date"] for r in rows] == [r["date"] for r in inputs]
    assert all(row["HX"] == original["high"] and row["LX"] == original["low"] for row, original in zip(rows, inputs))
    actions = COMMON.channel_lifecycle(inputs, [{"buy": bool(r["BUY8"]), "sell": bool(r["SELL8"])} for r in rows])
    ledger_path = BASE / "native-results/S08/native-trades-all.xls"
    ledger_blob = ledger_path.read_bytes()
    hashes[ledger_path] = sha256(ledger_blob).hexdigest()
    native = [r for r in list(csv.reader(io.StringIO(ledger_blob.decode("gb18030")), delimiter="\t"))[1:] if r and r[0] == args.code]
    observed = [(r[2][:10], r[3]) for r in native]
    ordinary_arithmetic_disagreements = []
    for r in rows:
        if r["BC"] < 6:
            continue  # Retain native unavailable prefix; it cannot trade.
        decimal_bh, decimal_sl = r["HX"] >= r["UPX"], r["LX"] <= r["DNX"]
        if decimal_bh != bool(r["BH"]) or decimal_sl != bool(r["SL"]):
            ordinary_arithmetic_disagreements.append({**r, "exact_decoded_Decimal_BH": decimal_bh,
                                                       "exact_decoded_Decimal_SL": decimal_sl})
    result = {"kind": "actual_readonly_native_boolean_channel_observations_not_a_financial_replay",
              "security_code": args.code, "native_indicator_name": prefix[:-1],
              "native_input_binding": binding, "original_high_precision_input_binding": input_binding,
              "explicit_native_client_configuration": client_config,
              "rows": len(rows), "actual_native_boolean_lifecycle_matches_original_S08_ledger": actions == observed,
              "native_expected_actions": actions, "original_native_ledger_actions": observed,
              "exact_decoded_Decimal_operator_disagreement_rows": ordinary_arithmetic_disagreements,
              "notable_605488_original_audit_dates": [r for r in rows if r["date"] in {"2022-05-30", "2022-06-09", "2022-09-02"}],
              "sources": COMMON.source_records(hashes), "returns_calculated": False,
              "limits": ["The local client directly outputs comparison booleans; no epsilon, price rounding or replacement arithmetic is inserted into the strategy.",
                         "Observed booleans establish this source/history/FQ run only; undocumented internal precision or tolerance is not generalized.",
                         "OHLCV does not establish liquidity, daily price-limit permission, cash, fees or execution feasibility."]}
    assert COMMON.unchanged(hashes) and path.read_bytes() == blob
    result["source_hashes_unchanged_before_output"] = True
    out = BASE / "native-evaluation/additional-6-8" / f"S08-{args.code}-actual-native-boundaries.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"code": args.code, "rows": len(rows), "lifecycle_matches": actions == observed,
                      "observed_operator_disagreement_rows": len(ordinary_arithmetic_disagreements)}))


if __name__ == "__main__":
    main()
