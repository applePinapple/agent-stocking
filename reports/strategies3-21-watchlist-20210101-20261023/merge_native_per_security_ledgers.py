"""Validate then concatenate native per-security exports without rewriting rows.

Run only after the UI owner confirms all seven exports are complete. No market
data, returns, fees or trade fields are calculated or substituted.
"""
import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXPECTED_CODES = {"300308", "300750", "601899", "603728", "605488", "688017", "688322"}
SUPPORTED = {3, 4, 5, 9, 10, 12, 15, 16, 17, 18, 19}


def digest(blob):
    return sha256(blob).hexdigest()


def within_batch(path):
    result = path.resolve()
    if not result.is_relative_to(BASE.resolve()):
        raise ValueError(f"Path leaves this report batch: {result}")
    return result


def parse_table(blob, label):
    text = blob.decode("gb18030", errors="strict")
    rows = [row for row in csv.reader(io.StringIO(text), delimiter="\t") if row and any(row)]
    if not rows:
        raise ValueError(f"{label}: empty export")
    header = rows[0]
    for number, row in enumerate(rows[1:], 2):
        if len(row) != len(header):
            raise ValueError(f"{label}: row {number} has {len(row)} columns, expected {len(header)}")
    return header, rows[1:]


def read_summary(path):
    blob = path.read_bytes()
    header, rows = parse_table(blob, str(path))
    code_col = header.index("品种代码")
    total_col = header.index("总次数")
    expected = {}
    combined = None
    for row in rows:
        code = row[code_col]
        if code in EXPECTED_CODES:
            if code in expected:
                raise ValueError(f"Duplicate summary code {code}")
            if not row[total_col].isdigit():
                raise ValueError(f"Non-integer native total count for {code}")
            expected[code] = int(row[total_col])
        elif code == "------":
            if combined is not None:
                raise ValueError("Duplicate combined summary row")
            combined = int(row[total_col])
        elif code.isdigit():
            raise ValueError(f"Unexpected summary code {code}")
    if set(expected) != EXPECTED_CODES:
        raise ValueError(f"Summary code set mismatch: {set(expected)}")
    if combined is None or sum(expected.values()) != combined:
        raise ValueError("Native combined count differs from sum of seven native per-code counts")
    return blob, expected, combined


def inspect_security(path, code, total):
    blob = path.read_bytes()
    lines = blob.splitlines(keepends=True)
    if not lines or not lines[0].endswith(b"\n"):
        raise ValueError(f"{path}: header lacks completed native line ending")
    header_bytes = lines[0]
    body = b"".join(lines[1:])
    if body and not body.endswith(b"\n"):
        raise ValueError(f"{path}: last record is unterminated; source may still be saving; no separator will be fabricated")
    header, rows = parse_table(blob, str(path))
    indexes = {name: header.index(name) for name in ["品种代码", "时间", "信号", "交易量(股/手)"]}
    if len(rows) != total * 2:
        raise ValueError(f"{code}: {len(rows)} native rows, expected {total}*2={total*2}; no rows will be filled in")
    buy_count = close_count = 0
    dates = []
    quantities = []
    for number, row in enumerate(rows, 1):
        if row[indexes["品种代码"]] != code:
            raise ValueError(f"{path}: row {number} belongs to another security")
        signal = row[indexes["信号"]]
        if signal == "买开":
            buy_count += 1
        elif signal in {"卖平", "平盘"}:
            close_count += 1
        else:
            raise ValueError(f"{code}: unexpected native long-only signal label {signal!r}")
        try:
            quantity = Decimal(row[indexes["交易量(股/手)"]])
        except InvalidOperation as exc:
            raise ValueError(f"{code}: invalid native quantity") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError(f"{code}: nonpositive/nonfinite native quantity")
        quantities.append(row[indexes["交易量(股/手)"]])
        dates.append(datetime.fromisoformat(row[indexes["时间"]]))
    if buy_count != total or close_count != total:
        raise ValueError(f"{code}: native buy={buy_count}, close={close_count}, expected {total} each")
    if dates != sorted(dates):
        raise ValueError(f"{code}: native rows are not chronological; source order will not be silently changed")
    evidence = {
        "security_code": code,
        "path": str(path.relative_to(BASE)),
        "sha256": digest(blob),
        "bytes": len(blob),
        "native_summary_total_count": total,
        "native_row_count": len(rows),
        "native_buy_rows": buy_count,
        "native_close_rows_including_terminal_flatten": close_count,
        "native_first_time": dates[0].isoformat(sep=" ") if dates else None,
        "native_last_time": dates[-1].isoformat(sep=" ") if dates else None,
        "header_bytes_sha256": digest(header_bytes),
        "body_bytes_sha256": digest(body),
        "body_bytes": len(body),
        "quantity_field_policy": "validate positive Decimal only; preserve exact source strings",
        "financial_field_policy": "no return/fee/turnover recalculation or substitution",
        "input_is_native_per_security_UI_export": True,
    }
    return blob, header_bytes, body, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, help="S16, S17, etc.")
    parser.add_argument("--formula-source", required=True, help="Actual run source, relative to this batch directory")
    args = parser.parse_args()
    if not args.strategy.startswith("S") or not args.strategy[1:].isdigit():
        raise ValueError("Strategy must be S followed by its supported number")
    sid = int(args.strategy[1:])
    if sid not in SUPPORTED:
        raise ValueError("Unsupported/N/A strategy cannot be merged as a result")
    strategy = f"S{sid:02}"
    run_dir = within_batch(BASE / "native-results" / strategy)
    security_dir = within_batch(run_dir / "per-security")
    summary_path = within_batch(run_dir / "native-summary.xls")
    source_path = within_batch(BASE / args.formula_source)
    output_path = within_batch(run_dir / "native-trades-all.xls")
    manifest_path = within_batch(run_dir / "native-trades-all.derivation.json")
    for code in sorted(EXPECTED_CODES):
        if not (security_dir / f"native-trades-{code}.xls").is_file():
            raise ValueError(f"Seven complete native sources required; missing {code}")
    source_blob = source_path.read_bytes()
    source_index = json.loads((BASE / "native-evaluation/per-strategy/manifest.json").read_text(encoding="utf-8-sig"))
    source_record = next(row for row in source_index["files"] if row["sid"] == sid)
    if digest(source_blob) != source_record["sha256"]:
        raise ValueError("Actual run source does not match accepted per-SID source hash")
    summary_blob, expected, combined = read_summary(summary_path)
    reads, bodies, sources, common_header = [], [], [], None
    row_cursor = 1
    byte_cursor = None
    for code in sorted(EXPECTED_CODES):
        path = within_batch(security_dir / f"native-trades-{code}.xls")
        blob, header, body, info = inspect_security(path, code, expected[code])
        if common_header is None:
            common_header, byte_cursor = header, len(header)
        elif header != common_header:
            raise ValueError(f"{code}: raw native header bytes differ; no header normalization performed")
        info.update({"merged_data_row_first_1based": row_cursor if info["native_row_count"] else None, "merged_data_row_last_1based": row_cursor + info["native_row_count"] - 1 if info["native_row_count"] else None, "merged_body_byte_offset_0based": byte_cursor, "merged_body_byte_length": len(body)})
        row_cursor += info["native_row_count"]
        byte_cursor += len(body)
        reads.append((path, blob))
        bodies.append(body)
        sources.append(info)
    merged = common_header + b"".join(bodies)
    merged_header, merged_rows = parse_table(merged, "derived merged export")
    if len(merged_rows) != combined * 2:
        raise ValueError("Merged count differs from native combined total*2")
    # Verify each copied body byte-for-byte before any output appears.
    for body, source in zip(bodies, sources):
        start = source["merged_body_byte_offset_0based"]
        if merged[start:start+len(body)] != body:
            raise AssertionError("Byte-preserving range verification failed")
    for path, original in reads + [(summary_path, summary_blob), (source_path, source_blob)]:
        if path.read_bytes() != original:
            raise ValueError(f"Source changed while being inspected: {path}")
    if output_path.exists() and output_path.read_bytes() != merged:
        raise ValueError("Different destination already exists; preserve it and choose a reviewed recovery action")
    timestamp = datetime.now().astimezone().isoformat()
    manifest = {
        "schema_version": 1,
        "created_at_system_local": timestamp,
        "strategy": strategy,
        "status": "derived_byte_concatenation_sources_and_counts_validated",
        "derivation": "exact concatenation of seven native per-security export bodies with one unchanged first header",
        "is_direct_combined_window_native_export": False,
        "source_native_exports_preserved_read_only": True,
        "new_trades_generated": False,
        "returns_fees_or_trade_fields_recomputed": False,
        "encoding": "GB18030 native bytes; no transcoding",
        "code_order": sorted(EXPECTED_CODES),
        "internal_order": "unchanged within each native source file",
        "header_policy": "all seven raw header lines byte-identical; first retained once",
        "body_policy": "entire original body bytes copied, including native blank lines and line endings",
        "summary": {"path": str(summary_path.relative_to(BASE)), "sha256": digest(summary_blob), "native_combined_total_count": combined, "per_security_total_counts": expected},
        "runtime_formula_source": {"path": str(source_path.relative_to(BASE)), "sha256": digest(source_blob), "sid": sid, "attribution_basis": "UI owner supplied actual run source argument; matched accepted per-SID manifest"},
        "sources": sources,
        "output": {"path": str(output_path.relative_to(BASE)), "sha256": digest(merged), "bytes": len(merged), "native_rows_preserved": len(merged_rows), "columns": merged_header},
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "scope": "Source/header/code/row-count/label/quantity-positivity/order and byte-provenance checks only. Does not assert fill-price, permission, fee or return audit passed.",
    }
    temporary = within_batch(run_dir / "native-trades-all.derivation.tmp")
    temporary.write_bytes(merged)
    if digest(temporary.read_bytes()) != digest(merged):
        raise ValueError("Temporary output readback hash failed")
    os.replace(temporary, output_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"strategy": strategy, "status": manifest["status"], "sources": len(sources), "native_rows": len(merged_rows), "sha256": digest(merged), "direct_combined_native_export": False, "financial_fields_recomputed": False}))


if __name__ == "__main__":
    main()
