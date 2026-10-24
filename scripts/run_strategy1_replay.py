"""Build an auditable Strategy 1 replay from explicit TDX screenshot observations.

Only supplied observations are used. This command does not collect data, infer
missing signals, export TDX data or reconstruct erased historical rockets.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import tomllib
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_stocking.strategy1 import (  # noqa: E402
    CashDividend,
    DailyObservation,
    Strategy1ClosedBatch,
    Strategy1Config,
    Strategy1DividendLedger,
    Strategy1Equity,
    Strategy1Fill,
    Strategy1Order,
    Strategy1Result,
    calculate_strategy1_metrics,
    replay_strategy1,
)

INPUT_COLUMNS = tuple(field.name for field in fields(DailyObservation))
BOOLEAN_COLUMNS = ("rocket", "close_limit_up", "open_buy_allowed", "sell_allowed")
PRICE_COLUMNS = ("open", "high", "low", "close", "profit_pct")
REPLAY_MODE = "current-surviving-rocket-replay"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _iso_date(value: str) -> date:
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"日期必须为 YYYY-MM-DD：{value!r}") from exc
    if result.isoformat() != value:
        raise ValueError(f"日期必须为 YYYY-MM-DD：{value!r}")
    return result


def _explicit_bool(value: str, column: str, line: int) -> bool:
    if value in ("true", "True", "1"):
        return True
    if value in ("false", "False", "0"):
        return False
    raise ValueError(f"CSV 第 {line} 行 {column} 必须明确为 true/false 或 1/0，不能缺省：{value!r}")


def load_observations(path: Path, *, allow_sparse_profit: bool = False) -> list[DailyObservation]:
    """Read required fields without truthiness conversions or missing defaults."""
    if type(allow_sparse_profit) is not bool:
        raise ValueError("allow_sparse_profit must be an explicit bool")
    observations: list[DailyObservation] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames
        if not header or any(not column for column in header) or len(header) != len(set(header)):
            raise ValueError("CSV 必须有唯一、非空的列名")
        missing = set(INPUT_COLUMNS) - set(header)
        if missing:
            raise ValueError("CSV 缺少必填列：" + ", ".join(sorted(missing)))
        for row in reader:
            line = reader.line_num
            if None in row:
                raise ValueError(f"CSV 第 {line} 行的值多于列名")
            values: dict[str, object] = {}
            for column in INPUT_COLUMNS:
                raw = row[column]
                if raw is None:
                    raise ValueError(f"CSV 第 {line} 行 {column} 为空；不得补造观测或成交许可")
                if not raw.strip():
                    if column == "profit_pct" and allow_sparse_profit:
                        values[column] = None
                        continue
                    raise ValueError(f"CSV 第 {line} 行 {column} 为空；不得补造观测或成交许可")
                values[column] = raw.strip()
            values["date"] = _iso_date(str(values["date"]))
            for column in PRICE_COLUMNS:
                if column == "profit_pct" and values[column] is None:
                    continue
                try:
                    number = Decimal(str(values[column]))
                except InvalidOperation as exc:
                    raise ValueError(f"CSV 第 {line} 行 {column} 不是十进制数") from exc
                if not number.is_finite():
                    raise ValueError(f"CSV 第 {line} 行 {column} 必须为有限十进制数")
                values[column] = number
            for column in BOOLEAN_COLUMNS:
                values[column] = _explicit_bool(str(values[column]), column, line)
            observation = DailyObservation(**values)
            if observations and observation.date <= observations[-1].date:
                raise ValueError(f"CSV 第 {line} 行日期必须严格递增且不能重复")
            observations.append(observation)
    if not observations:
        raise ValueError("CSV 没有实际观测行；不能生成回放收益")
    return observations


def _unique_json_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"证据JSON含重复键：{key}")
        value[key] = item
    return value


def _resolve_source(source: str, base: Path) -> Path:
    relative = Path(source)
    if not source.strip() or relative.anchor:
        raise ValueError(f"source 必须是相对证据清单目录的路径：{source!r}")
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"source 路径逃逸证据清单目录：{source!r}")
    if not resolved.is_file():
        raise ValueError(f"source 文件不存在或不是普通文件：{source!r}")
    return resolved


def _verify_sources(
    observations: Sequence[DailyObservation], evidence_manifest: Path,
    *, allow_sparse_profit: bool = False,
) -> list[dict[str, object]]:
    """Verify direct images or date-specific JSON plus every role's image."""
    base = evidence_manifest.resolve().parent
    image_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    records: dict[str, dict[str, object]] = {}
    digests: dict[str, str] = {}

    def register(source: str, kind: str, role: str, observation_date: date) -> tuple[Path, str]:
        resolved = _resolve_source(source, base)
        digest = digests.get(str(resolved))
        if digest is None:
            digest = _sha256(resolved)
            digests[str(resolved)] = digest
        if source not in records:
            records[source] = {"source": source, "path": str(resolved), "sha256": digest,
                               "kind": kind, "roles": [], "dates": [], "reference_count": 0}
        record = records[source]
        if record["kind"] != kind:
            raise ValueError(f"source 被用于不一致的证据类型：{source!r}")
        record["reference_count"] += 1
        if role not in record["roles"]:
            record["roles"].append(role)
        mapped_date = observation_date.isoformat()
        if mapped_date not in record["dates"]:
            record["dates"].append(mapped_date)
        return resolved, digest

    for item in observations:
        resolved = _resolve_source(item.source, base)
        if resolved.suffix.lower() != ".json":
            if allow_sparse_profit:
                raise ValueError(f"{item.date}: 稀疏P模式必须使用kind=tdx_daily_evidence的每日证据JSON")
            if resolved.suffix.lower() not in image_suffixes:
                raise ValueError(f"{item.date}: 旧source模式只接受直接截图，或明确kind的每日证据JSON")
            register(item.source, "direct_image", "legacy_all_fields", item.date)
            continue

        evidence_path, _ = register(item.source, "daily_evidence", "daily_mapping", item.date)
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"{item.date}: 无法读取每日证据JSON：{item.source}") from exc
        required = {"schema_version", "kind", "date", "price", "rocket", "profit"}
        if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - {"note"}:
            raise ValueError(f"{item.date}: 每日证据JSON必须明确price/rocket/profit三角色，不接受未知字段")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1 or payload["kind"] != "tdx_daily_evidence":
            raise ValueError(f"{item.date}: 未知每日证据JSON的kind或schema_version")
        if "note" in payload and (not isinstance(payload["note"], str) or not payload["note"].strip()):
            raise ValueError(f"{item.date}: 每日证据JSON的note必须是非空人工说明")
        if payload["date"] != item.date.isoformat():
            raise ValueError(f"{item.date}: 每日证据JSON日期与CSV行不一致")
        for role in ("price", "rocket", "profit"):
            reference = payload[role]
            if role == "profit" and reference is None and item.profit_pct is None:
                continue
            role_required = {"source", "sha256", "date"}
            if not isinstance(reference, dict) or not role_required.issubset(reference) or set(reference) - role_required - {"locator", "note"}:
                raise ValueError(f"{item.date}: {role}角色必须提供source/sha256/date图像证据")
            if reference["date"] != item.date.isoformat():
                raise ValueError(f"{item.date}: {role}角色日期与CSV行不一致")
            source, expected_digest = reference["source"], reference["sha256"]
            if not isinstance(source, str) or not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest):
                raise ValueError(f"{item.date}: {role}角色source或SHA-256格式无效")
            for optional in ("locator", "note"):
                if optional in reference and (not isinstance(reference[optional], str) or not reference[optional].strip()):
                    raise ValueError(f"{item.date}: {role}角色{optional}必须是非空人工说明")
            artifact, actual_digest = register(source, "role_image", role, item.date)
            if artifact.suffix.lower() not in image_suffixes:
                raise ValueError(f"{item.date}: {role}角色必须引用原图，不能递归引用其他JSON或未知文件")
            if actual_digest != expected_digest.lower():
                raise ValueError(f"{item.date}: {role}角色原图SHA-256不符：{source}")
    return [records[source] for source in sorted(records)]


def _check_sources_unchanged(records: Sequence[dict[str, object]], evidence_manifest: Path) -> None:
    base = evidence_manifest.resolve().parent
    for record in records:
        current = (base / str(record["source"])).resolve()
        if not current.is_relative_to(base) or str(current) != record["path"]:
            raise ValueError(f"source 路径在回放期间发生变化：{record['source']!r}")
        if not current.is_file() or _sha256(current) != record["sha256"]:
            raise ValueError(f"source 文件在回放期间发生变化：{record['source']!r}")


def load_cash_dividends(
    path: Path, evidence_manifest: Path,
) -> tuple[list[CashDividend], list[dict[str, object]]]:
    """Read explicit cash events and verify every declared original image."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_json_object)
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "kind", "events"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["kind"] != "cash_dividend_events" or not isinstance(payload["events"], list)):
        raise ValueError("Expected schema_version=1, kind=cash_dividend_events and events list")
    base = evidence_manifest.resolve().parent
    events: list[CashDividend] = []
    records: dict[str, dict[str, object]] = {}
    required = {"event_id", "record_date", "ex_date", "payment_date", "cash_per_share", "source", "sha256"}
    for item in payload["events"]:
        if not isinstance(item, dict) or not required.issubset(item) or set(item) - required - {"additional_sources", "note"}:
            raise ValueError("Cash dividend event requires explicit dates, per-share amount and source SHA256")
        if any(not isinstance(item[name], str) for name in ("record_date", "ex_date", "payment_date")):
            raise ValueError("Cash dividend dates must be ISO date strings")
        if not isinstance(item["cash_per_share"], str):
            raise ValueError("cash_per_share must be a decimal string, not a JSON number")
        try:
            amount = Decimal(item["cash_per_share"])
        except InvalidOperation as exc:
            raise ValueError("Invalid cash_per_share decimal string") from exc
        event = CashDividend(
            item["event_id"], _iso_date(item["record_date"]), _iso_date(item["ex_date"]),
            _iso_date(item["payment_date"]), amount, item["source"],
        )
        if "note" in item and (not isinstance(item["note"], str) or not item["note"].strip()):
            raise ValueError("Cash dividend note must be a nonempty string")
        extra = item.get("additional_sources", [])
        if not isinstance(extra, list):
            raise ValueError("additional_sources must be a list of source/sha256 image references")
        references = [{"source": item["source"], "sha256": item["sha256"]}] + extra
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"source", "sha256"}:
                raise ValueError("Dividend evidence requires source and sha256")
            source, expected = reference["source"], reference["sha256"]
            if not isinstance(source, str) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                raise ValueError("Invalid dividend source or SHA256")
            resolved = _resolve_source(source, base)
            if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                raise ValueError("Cash dividend source must be an original image")
            digest = _sha256(resolved)
            if digest != expected.lower():
                raise ValueError(f"Cash dividend source SHA256 mismatch: {source}")
            record = records.setdefault(source, {"source": source, "path": str(resolved), "sha256": digest,
                                                "kind": "cash_dividend_image", "roles": ["cash_dividend"],
                                                "dates": [], "reference_count": 0})
            if record["sha256"] != digest:
                raise ValueError(f"Dividend source changed during read: {source}")
            record["reference_count"] += 1
            for value in (event.record_date, event.ex_date, event.payment_date):
                if value.isoformat() not in record["dates"]:
                    record["dates"].append(value.isoformat())
        events.append(event)
    return events, [records[key] for key in sorted(records)]


def _merge_source_records(*groups: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for group in groups:
        for item in group:
            key = str(item["source"])
            if key not in records:
                records[key] = dict(item, roles=list(item["roles"]), dates=list(item["dates"]))
                continue
            old = records[key]
            if old["path"] != item["path"] or old["sha256"] != item["sha256"]:
                raise ValueError(f"Inconsistent evidence source: {key}")
            old["reference_count"] += item["reference_count"]
            for name in ("roles", "dates"):
                old[name] = list(dict.fromkeys(old[name] + item[name]))
    return [records[key] for key in sorted(records)]


def _verify_assembly_binding(
    evidence_manifest: Path, input_digest: str, observations: Sequence[DailyObservation],
    cash_dividends_path: Path | None,
) -> list[dict[str, object]]:
    """Bind inactive execution sentinels to exactly the successful guarded trace."""
    if evidence_manifest.suffix.lower() != ".json":
        return []
    try:
        payload = json.loads(evidence_manifest.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError:
        return []  # Legacy opaque manifests are still verified by their hash.
    if not isinstance(payload, dict) or payload.get("kind") != "local_tdx_screenshot_input_assembly":
        return []
    if (payload.get("status") != "confirmed" or payload.get("ready_for_formal_replay") is not True
            or payload.get("safe_trace_completed_all_bars") is not True
            or payload.get("corporate_action_coverage_verified") is not True):
        raise ValueError("Assembly is draft/incomplete; formal replay is not authorized by its source status")
    if payload.get("input_csv_sha256") != input_digest:
        raise ValueError("Assembly CSV differs from its safe decision trace")
    expected_calendar = {"first_date": str(observations[0].date), "last_date": str(observations[-1].date), "rows": len(observations)}
    if payload.get("trace_calendar") != expected_calendar:
        raise ValueError("Requested observations differ from the complete assembly trace calendar")
    engine = PROJECT_ROOT / "src" / "agent_stocking" / "strategy1.py"
    if payload.get("safe_trace_engine_sha256") != _sha256(engine):
        raise ValueError("Engine changed after the inactive-sentinel safety trace")
    binding = payload.get("corporate_action_binding")
    if not isinstance(binding, dict) or set(binding) != {"cash_dividends", "coverage"} or cash_dividends_path is None:
        raise ValueError("Formal assembly requires the explicitly bound cash-dividend JSON")
    base, records = evidence_manifest.resolve().parent, []
    for role, reference in binding.items():
        if not isinstance(reference, dict) or set(reference) != {"source", "sha256"}:
            raise ValueError("Invalid assembly corporate-action binding")
        path = _resolve_source(reference["source"], base)
        if _sha256(path) != reference["sha256"]:
            raise ValueError("Assembly corporate-action configuration hash changed")
        if role == "cash_dividends" and path != cash_dividends_path.resolve():
            raise ValueError("Passed cash-dividend JSON differs from the assembly trace")
        records.append({"source": reference["source"], "path": str(path), "sha256": reference["sha256"],
                        "kind": "assembly_corporate_action_configuration", "roles": [role],
                        "dates": [str(observations[0].date), str(observations[-1].date)], "reference_count": 1})
    proofs = payload.get("execution_field_proofs", [])
    if [row.get("date") for row in proofs] != [str(row.date) for row in observations]:
        raise ValueError("Assembly execution proofs do not cover the trace calendar")
    for row in proofs:
        for field in ("close_limit_up", "open_buy_allowed"):
            proof = row.get("fields", {}).get(field, {})
            if proof.get("decision_read") is True:
                if proof.get("status") != "confirmed" or proof.get("factual_native_confirmation") is not True:
                    raise ValueError("Assembly trace read an unconfirmed execution field")
            elif (proof.get("decision_read") is not False or proof.get("status") not in ("confirmed", "inactive_sentinel_proven_not_read")):
                raise ValueError("Assembly inactive execution sentinel lacks a complete safety proof")
    dependencies = payload.get("verified_inputs_and_sources")
    mappings = payload.get("daily_mappings")
    if not isinstance(dependencies, list) or not dependencies or not isinstance(mappings, list) or len(mappings) != len(observations):
        raise ValueError("Assembly evidence dependency or daily mapping manifest is incomplete")
    for reference in dependencies + mappings:
        path = _resolve_source(reference["source"], base)
        if _sha256(path) != reference["sha256"]:
            raise ValueError(f"Assembly source changed after safe trace: {reference['source']}")
        records.append({"source": reference["source"], "path": str(path), "sha256": reference["sha256"],
                        "kind": "assembly_verified_dependency", "roles": ["assembly_dependency"],
                        "dates": [], "reference_count": 1})
    return records


def _batch_net_return(batch: Strategy1ClosedBatch) -> Decimal:
    if batch.entry_cost <= 0:
        raise ValueError("买入批次含佣金成本必须大于0，不能计算净收益率")
    return batch.pnl / batch.entry_cost


def _write_csv(path: Path, record_type: type, records: Sequence[object]) -> None:
    columns = [field.name for field in fields(record_type)]
    # This derived report column does not change the engine's batch ledger.
    if record_type is Strategy1ClosedBatch:
        columns.append("net_return")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            if record_type is Strategy1ClosedBatch:
                row["net_return"] = _batch_net_return(record)
            writer.writerow({
                key: "" if value is None else _json_value(value)
                if isinstance(value, (Decimal, date, Path)) else value
                for key, value in row.items()
            })


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return output


def _money(value: object) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _percent(value: object) -> str:
    return "不适用" if value is None else f"{Decimal(str(value)) * 100:.4f}%"


def _reason(reason: str) -> str:
    return {
        "First close profit strictly above 45%; ideal close fill": "窗口内筹码获利首次 >45%，理想收盘买入",
        "Deferred buy at explicitly permitted next available open": "收盘封板后顺延，首次明确允许成交的开盘买入",
        "Close profit strictly below 40%; sell old shares and retain today's purchases": "筹码获利 <40%，卖出旧仓，保留当日新增股份",
    }.get(reason, reason)


def build_report(
    *, symbol: str, name: str, start: date, end: date,
    observations: Sequence[DailyObservation], result: Strategy1Result,
    summary: dict[str, object],
) -> str:
    metrics = summary["metrics"]
    assert isinstance(metrics, dict)
    source_check = summary["source_verification"]
    assert isinstance(source_check, dict)
    first, last = observations[0].date, observations[-1].date
    lines = [
        "# 策略1：当前可见小火箭历史复盘",
        "",
        f"**回测标的：{_cell(name)}（{_cell(symbol)}）**  ",
        f"**请求区间：{start} 至 {end}**  ",
        f"**实际数据区间：{first} 至 {last}，共 {len(observations)} 根日K线。**",
        "**价格口径：不复权（none），由本次执行参数显式确认。**",
        "",
        "本报告按用户批准的当前可见小火箭复盘口径计算。历史小火箭可能在后续下跌后消失，现存截图不能恢复已消失信号；因此存在信号删失和幸存者偏差，结果可能偏乐观，不能视为当时全部信号的完整历史回测。",
        "",
        "## 核心指标",
        "",
    ]
    lines += _table(("指标", "结果"), [
        ("初始资金", _money(metrics["initial_cash"]) + " 元"),
        ("期末总资产", _money(metrics["final_equity"]) + " 元"),
        ("总收益率", _percent(metrics["total_return"])),
        ("年化收益率", _percent(metrics["annualized_return"])),
        ("最大回撤", _percent(metrics["max_drawdown"])),
        ("买入 / 卖出成交笔数", f'{metrics["buy_count"]} / {metrics["sell_count"]}'),
        ("已结束买入批次数", metrics["closed_batch_count"]),
        ("已结束买入批次胜率", _percent(metrics["win_rate"])),
        ("累计佣金", _money(metrics["total_fees"]) + " 元"),
        ("其他费用（本次约定）", "0.00 元"),
        ("已平批次净损益（含佣金及分红）", _money(metrics["realized_pnl"]) + " 元"),
        ("未平批次净损益（含买入佣金及分红）", _money(metrics["unrealized_pnl"]) + " 元"),
        ("累计确认现金分红收入", _money(metrics["dividend_income"]) + " 元"),
        ("累计分红实际到账", _money(metrics["dividend_cash_received"]) + " 元"),
        ("期末应收分红", _money(metrics["ending_dividend_receivable"]) + " 元"),
        ("期末现金", _money(metrics["ending_cash"]) + " 元"),
        ("期末持股", f'{metrics["ending_quantity"]} 股'),
        ("期末持仓市值", _money(metrics["ending_market_value"]) + " 元"),
        ("标的首尾收盘价格涨幅", _percent(metrics["underlying_close_return"])),
        ("取消 / 到期 / 期末未完成机会", f'{metrics["cancelled_opportunities"]} / {metrics["expired_opportunities"]} / {metrics["pending_opportunities"]}'),
    ])
    lines += [
        "",
        "总收益率以全部初始100万元为分母。年化收益按实际首尾观测日的自然日间隔计算，起止同日时不计算。最大回撤基于初始资金及每日收盘净值。胜率按已卖出的买入批次统计，盈亏为零不算胜；它不是卖出订单胜率。标的价格涨幅只比较输入首尾收盘价，不含分红。",
        "",
        "## 全部成交记录",
        "",
        "金额、佣金和现金单位为人民币元；数量单位为股。卖出订单可能合并多个买入批次。",
        "",
    ]
    fill_rows = []
    for fill in result.fills:
        reason = _reason(fill.reason)
        if fill.rocket_date is not None:
            reason += f"；火箭日 {fill.rocket_date}，触发日 {fill.signal_date}"
        else:
            reason += f"；信号日 {fill.signal_date}"
        fill_rows.append((
            fill.sequence, fill.date, "买入" if fill.side == "buy" else "卖出",
            "开盘" if fill.session == "open" else "收盘", reason,
            format(fill.price, "f"), fill.quantity, _money(fill.notional),
            _money(fill.commission), _money(fill.cash_after), fill.holdings_after,
        ))
    if fill_rows:
        lines += _table(("序号", "日期", "方向", "时点", "原因 / 信号日期", "成交价", "数量", "成交额", "佣金", "成交后现金", "成交后持股"), fill_rows)
    else:
        lines.append("本次输入观测未产生买卖成交。")
    lines += ["", "完整字段及截图来源见 `trades.csv`；所有机会创建、触发、顺延、取消、到期和成交事件见 `orders.csv`。", "", "## 已结束买入批次盈亏", ""]
    if result.closed_batches:
        lines += _table(("买入序号", "卖出序号", "火箭日", "买入日", "卖出日", "数量", "买入总成本", "卖出成交额", "分摊卖出佣金", "已确认分红", "净盈亏", "批次净收益率"), [
            (batch.buy_sequence, batch.sell_sequence, batch.rocket_date, batch.entry_date,
             batch.exit_date, batch.quantity, _money(batch.entry_cost),
             _money(batch.exit_notional), _money(batch.exit_commission), _money(batch.dividend_income), _money(batch.pnl),
             _percent(_batch_net_return(batch)))
            for batch in result.closed_batches
        ])
    else:
        lines.append("没有已结束的买入批次。")
    lines += ["", "批次净收益率＝该批净盈亏÷该批含买入佣金的买入总成本；净盈亏已扣除分摊的卖出佣金，并包含归属该批、截至期末已除息确认的分红。分红不改写买入成本。批次收益率使用该批实际成本作分母，与使用全部初始100万元作分母的账户总收益率不同，不能直接将各批收益率相加作为账户收益率。`closed_batches.csv` 的 `net_return` 为小数比率，例如0.10表示10%。"]
    lines += ["", "## 期末未平仓批次", ""]
    if result.ending_lots:
        lines += _table(("买入序号", "火箭日", "买入日", "数量", "买入价", "含佣金成本", "已确认分红"), [
            (lot.buy_sequence, lot.rocket_date, lot.entry_date, lot.quantity,
             format(lot.entry_price, "f"), _money(lot.entry_cost), _money(lot.dividend_income))
            for lot in result.ending_lots
        ])
    else:
        lines.append("期末没有持仓。")
    lines += ["", "## 现金分红与应收", ""]
    if result.cash_dividend_events:
        source_hashes = {record["source"]: record["sha256"] for record in summary["cash_dividend_source_files"]}
        lines += _table(("事件", "登记日", "除息日", "支付日", "每股现金（元）", "原图", "SHA-256"), [
            (item.event_id, item.record_date, item.ex_date, item.payment_date,
             format(item.cash_per_share, "f"), item.source, source_hashes[item.source])
            for item in result.cash_dividend_events
        ])
        dividend_provenance = summary["provenance"]["cash_dividends"]
        lines += ["", f"事件JSON：`{dividend_provenance['path']}`；SHA-256：`{dividend_provenance['sha256']}`。"]
        lines += ["", "登记日完成全部交易后按收盘持仓锁定各批权益；除息前不提前增加净值，除息时转为应收，支付日开盘前转入可用现金。除息或支付日没有K线时，按原日历日期发生、在下一根开盘前依次处理，流水同时记录原日期和处理日期。登记后卖出的原批次仍领取；支付日新买股份不追领。", ""]
        phase_names = {"record_date_after_close": "收盘登记锁定", "ex_date_receivable_before_open": "除息确认应收", "cash_payment_before_open": "开盘前现金到账"}
        lines += _table(("事件", "原日期", "处理日", "阶段", "买入批次", "登记股数", "本行权益金额", "处理后现金", "处理后应收"), [
            (row.event_id, row.date, row.processed_on, phase_names[row.phase], row.buy_sequence,
             row.quantity, _money(row.amount), _money(row.cash_after), _money(row.receivable_after))
            for row in result.dividend_ledger
        ])
    else:
        lines.append("本次没有配置现金分红事件；引擎不推断未输入的事件。")
    lines += ["", "分红只按本次显式每股现金金额入账，不自动推断每10股单位、税率、登记日或到账日。按账户登记总股数计算分币合计，再按累计股数分配至原买入批次，避免各批独立舍入造成差额。期末已除息但未付款的应收计入总资产；尚未除息的登记权益不提前计收益。总资产＝现金＋股票市值＋应收分红；已平批次净损益＋未平批次净损益＝总资产－初始资金，未平批次损益包含该批已确认分红。完整流水见 `cash_dividends.csv`。"]
    lines += [
        "",
        "## 执行口径与覆盖核对",
        "",
        "- 初始资金1,000,000元；每个小火箭最多买入一次；每次总支出最多100,000元，包含买入佣金；已有持仓时可以继续新增买入。",
        "- 买入观察窗口包含火箭当天及之后3根实际日K线，共4根；停牌无K线时不计数。首次收盘筹码获利严格大于45%触发，买入前严格低于40%取消；等于阈值不触发。",
        "- 正常买卖采用用户约定的理想当日收盘价。收盘封涨停时，买入顺延到下一明确允许成交的开盘；连续3个完成的一字涨停日取消，包含触发日。成交判断使用输入显式字段，脚本不从OHLC推断许可。",
        "- 收盘筹码获利严格低于40%时卖出旧仓；当天新买股份保留，下一交易日收盘重新检查退出条件，不补造当日可卖数量。",
        "- 买卖佣金均为成交额的万分之1，每笔最低5元，按分四舍五入；其他费用本次按0元计算，滑点为0。买入200股起、超过部分按1股递增。",
        "- 期末持仓按最后观测日收盘价估值，不强制平仓，不预扣未发生的卖出费用。买入批次核算保留各批成本，合并卖出佣金按股数分摊并保持分币合计一致。",
        f"- 请求区间为 {start} 至 {end}；实际首日 {first}、末日 {last}、观测 {len(observations)} 行、当前可见火箭 {sum(item.rocket for item in observations)} 个。只使用请求区间内的输入行。",
        "- 日期首尾与行数核对不等于完整交易日历核对；本脚本未用外部交易日历补齐或推断缺失行，不据此保证每个应有交易日都已采集。若缺少日K线，4根窗口、退出日及每日回撤均会受影响。",
        "- 价格、已提供的获利比例和火箭来自本地通达信截图对应的输入记录。本次显式确认使用不复权价格；脚本不自动辨识或转换复权。现金分红仅处理显式事件JSON及其原图，完整事件配置和输入哈希见summary；送转及其他公司行为不支持，不能视为已处理。",
        "- 决策实际读取的收盘封板、开盘买入许可使用原生确认记录；经完整安全追踪证明未读取的字段可使用安全占位，这些占位并非市场事实，逐日依据见正式组装清单。卖出许可依据用户的理想收盘执行约定，不代表已核验真实市场成交许可。coverage中的显式字段声明仅涵盖决策实际需要的字段。",
        "- 资金不足、退出日不允许卖出等尚未约定分支由引擎报错终止，不擅自变更规则。",
        "- P采集模式：" + (
            "显式启用稀疏P；只有空仓且无有效观察/顺延机会、当天没有新火箭时允许P为空。必要P缺失立即按具体日期终止，不填0、不前填；全部日线仍用于净值和区间统计。"
            if summary["execution_assumptions"]["allow_sparse_profit"] else "默认完整P模式，每根日线必须提供P。"
        ),
        "",
        "## 来源文件校验",
        "",
        f"CSV共 {source_check['source_reference_count']} 条source引用、{source_check['verified_source_count']} 个不同CSV source路径；连同分红证据共核验 {source_check['verified_file_count']} 个实际文件，其中现金分红原图 {source_check['verified_cash_dividend_image_count']} 个，全部存在并记录SHA-256。校验范围包含CSV中请求区间之外的行。",
        "",
        "source按证据清单所在目录解析，只允许该目录内的相对文件路径。每日证据JSON分别引用price、rocket、profit原图，逐角色校验声明日期与CSV一致、预存SHA-256与原图一致；P有值必须有profit图，稀疏P模式必须使用每日证据JSON。该日期映射来自人工声明，不做OCR推断。输入CSV在读取前后及输出前指纹一致；证据清单、日期JSON及所有引用原图在输出前再次核验未变化。逐文件指纹和角色见 `source-verification.json`，该文件自身指纹见 `summary.json`。校验不等于验证截图内文字、交易日覆盖或公司行动口径。",
        "",
        "## 复现文件",
        "",
        "- `trades.csv`：全部成交及成交后现金、股数、截图来源。",
        "- `orders.csv`：全部交易机会及订单事件。",
        "- `daily_equity.csv`：每日现金、股票市值、应收分红、持股、净值与筹码获利比例。",
        "- `cash_dividends.csv`：各事件及原买入批次的登记、除息应收、现金到账独立流水。",
        "- `closed_batches.csv`：已结束买入批次、分摊后的净盈亏及 `net_return` 批次净收益率。",
        "- `source-verification.json`：核验CSV引用的全部source及每日证据JSON所引用的全部原图，记录角色、日期、引用次数、相对路径、真实路径和SHA-256；主清单可为JSON或JSONL。",
        "- `summary.json`：完整配置、核心指标、输入 / 策略 / 引擎 / 执行脚本 / 证据清单的SHA-256、版本及覆盖记录。",
        "",
        f"执行模式：`{REPLAY_MODE}`。本地历史复盘，不构成真实下单。",
        "",
    ]
    return "\n".join(lines)


def run_replay(
    *, input_path: Path, output_path: Path, symbol: str, name: str,
    start: date, end: date, evidence_manifest: Path, price_adjustment: str,
    allow_sparse_profit: bool = False, cash_dividends_path: Path | None = None,
) -> dict[str, object]:
    if type(allow_sparse_profit) is not bool:
        raise ValueError("allow_sparse_profit must be an explicit bool")
    if price_adjustment != "none":
        raise ValueError("本次执行必须显式确认 price_adjustment='none'（不复权）；当前不支持其他价格口径")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if not symbol.strip() or not name.strip():
        raise ValueError("必须明确提供证券代码和名称")
    if not evidence_manifest.is_file() or evidence_manifest.stat().st_size == 0:
        raise ValueError("必须提供非空的实际证据清单文件")
    if output_path.exists() and (not output_path.is_dir() or any(output_path.iterdir())):
        raise ValueError("输出路径必须不存在或为空目录，不能覆盖已有报告")
    input_digest = _sha256(input_path)
    manifest_digest = _sha256(evidence_manifest)
    all_observations = load_observations(input_path, allow_sparse_profit=allow_sparse_profit)
    if _sha256(input_path) != input_digest:
        raise ValueError("输入CSV在读取期间发生变化，请固定文件后重试")
    observations = [item for item in all_observations if start <= item.date <= end]
    if not observations:
        raise ValueError("请求区间内没有实际观测，不能生成回放收益")
    source_records = _verify_sources(all_observations, evidence_manifest, allow_sparse_profit=allow_sparse_profit)
    binding_records = _verify_assembly_binding(evidence_manifest, input_digest, observations, cash_dividends_path)
    source_records = _merge_source_records(source_records, binding_records)
    dividends: list[CashDividend] = []
    dividend_digest: str | None = None
    dividend_records: list[dict[str, object]] = []
    if cash_dividends_path is not None:
        dividend_digest = _sha256(cash_dividends_path)
        dividends, dividend_records = load_cash_dividends(cash_dividends_path, evidence_manifest)
        if _sha256(cash_dividends_path) != dividend_digest:
            raise ValueError("Cash dividend event JSON changed during read")
        source_records = _merge_source_records(source_records, dividend_records)
    config = Strategy1Config(
        initial_cash=Decimal("1000000.00"), buy_budget=Decimal("100000.00"),
        commission_rate=Decimal("0.0001"), min_commission=Decimal("5.00"),
        minimum_buy_quantity=200, quantity_step=1,
    )
    replay_options: dict[str, object] = {"allow_sparse_profit": allow_sparse_profit}
    if cash_dividends_path is not None:
        replay_options["cash_dividends"] = dividends
    result = replay_strategy1(observations, config, **replay_options)
    metrics = calculate_strategy1_metrics(result)
    module_path = PROJECT_ROOT / "src" / "agent_stocking" / "strategy1.py"
    strategy_path = PROJECT_ROOT / "docs" / "strategies" / "策略1.md"
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        project_version = tomllib.load(stream)["project"]["version"]
    source_verification: dict[str, object] = {
        "schema_version": 1,
        "status": "verified_existing_sources_with_sha256",
        "method": "csv_daily_roles_and_explicit_cash_dividend_images_relative_to_manifest_directory",
        "scope": "all_input_csv_rows_including_rows_outside_requested_range",
        "base_directory": str(evidence_manifest.resolve().parent),
        "input_sha256": input_digest,
        "evidence_manifest_sha256": manifest_digest,
        "source_reference_count": len(all_observations),
        "verified_source_count": len({item.source for item in all_observations}),
        "verified_file_count": len({record["path"] for record in source_records}),
        "verified_evidence_json_count": sum(record["kind"] == "daily_evidence" for record in source_records),
        "verified_role_image_count": sum(record["kind"] == "role_image" for record in source_records),
        "verified_direct_image_count": sum(record["kind"] == "direct_image" for record in source_records),
        "verified_cash_dividend_image_count": sum("cash_dividend" in record["roles"] for record in source_records),
        "cash_dividend_source_reference_count": sum(record["reference_count"] for record in dividend_records),
        "manifest_source_mapping": "not_assumed; each_csv_source_verified_directly",
        "files": source_records,
    }
    verification_bytes = (json.dumps(source_verification, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    verification_path = output_path / "source-verification.json"
    summary: dict[str, object] = {
        "report_type": REPLAY_MODE,
        "status": "completed_current_surviving_signal_replay",
        "symbol": symbol.strip(), "name": name.strip(),
        "requested_start_date": start, "requested_end_date": end,
        "config": asdict(config),
        "execution_assumptions": {
            "signal_basis": result.signal_basis,
            "source_basis": "local_tongdaxin_screenshots",
            "normal_fill": "ideal_same_close_as_explicitly_requested",
            "other_fees": Decimal("0.00"), "slippage": Decimal("0"),
            "ending_positions": "mark_at_final_observed_close_without_sale_or_future_fee",
            "corporate_actions": "explicit_cash_dividends_only; other_actions_unsupported",
            "cash_dividend_timing": "record_date_after_close; ex_date_receivable; payment_date_before_open",
            "cash_dividend_rounding": "account_total_half_up_cents_then_cumulative_quantity_allocation",
            "cash_dividend_tax_model": "none; explicit_cash_per_share_used_without_automatic_deductions",
            "price_adjustment": price_adjustment,
            "price_adjustment_label": "不复权",
            "price_adjustment_confirmation": "explicit_execution_parameter; not_automatically_detected_or_converted",
            "allow_sparse_profit": allow_sparse_profit,
            "profit_sampling_mode": "runtime_state_required_only" if allow_sparse_profit else "complete_every_bar",
        },
        "return_conventions": {
            "account_return_denominator": config.initial_cash,
            "closed_batch_net_return": "pnl / entry_cost; pnl includes recognized batch dividends; entry_cost includes buy commission",
            "closed_batch_net_return_csv_column": "net_return",
            "closed_batch_net_return_csv_unit": "decimal_ratio",
        },
        "coverage": {
            "input_rows": len(all_observations), "used_rows": len(observations),
            "excluded_outside_requested_range": len(all_observations) - len(observations),
            "actual_first_date": observations[0].date, "actual_last_date": observations[-1].date,
            "first_date_offset_calendar_days": (observations[0].date - start).days,
            "last_date_offset_calendar_days": (end - observations[-1].date).days,
            "strictly_increasing_unique_dates": True,
            "visible_rocket_count": sum(item.rocket for item in observations),
            "distinct_source_count": len({item.source for item in observations}),
            "all_required_observation_fields_explicit": True,
            "required_observation_fields_scope": "decision_required_only",
            "profit_observed_rows": sum(item.profit_pct is not None for item in observations),
            "profit_omitted_rows": sum(item.profit_pct is None for item in observations),
            "required_profit_missing_rows": 0,
            "omitted_profit_semantics": "allowed_only_when_no_position_or_unfinished_opportunity_at_close; no_imputation",
            "full_trading_day_coverage_verified": False,
            "coverage_limitation": "No reference trading calendar or expected-date list; bounds and row count alone do not establish completeness",
        },
        "metrics": metrics,
        "source_verification": {
            "status": source_verification["status"],
            "source_reference_count": len(all_observations),
            "verified_source_count": source_verification["verified_source_count"],
            "verified_file_count": source_verification["verified_file_count"],
            "verified_evidence_json_count": source_verification["verified_evidence_json_count"],
            "verified_role_image_count": source_verification["verified_role_image_count"],
            "verified_direct_image_count": source_verification["verified_direct_image_count"],
            "verified_cash_dividend_image_count": source_verification["verified_cash_dividend_image_count"],
            "all_sources_exist_and_hashed": True,
            "input_unchanged_after_read_and_before_output": True,
            "evidence_and_sources_unchanged_before_output": True,
            "details_file": "source-verification.json",
        },
        "ending_lots": [asdict(lot) for lot in result.ending_lots],
        "cash_dividend_events": [asdict(item) for item in result.cash_dividend_events],
        "cash_dividend_source_files": dividend_records,
        "dividend_entitlements": [asdict(item) for item in result.dividend_entitlements],
        "opportunities": [asdict(opportunity) for opportunity in result.opportunities],
        "provenance": {
            "input": {"path": str(input_path.resolve()), "sha256": input_digest},
            "strategy": {"path": str(strategy_path), "sha256": _sha256(strategy_path)},
            "engine": {"path": str(module_path), "sha256": _sha256(module_path)},
            "runner": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__))},
            "evidence_manifest": {"path": str(evidence_manifest.resolve()), "sha256": manifest_digest},
            "cash_dividends": None if cash_dividends_path is None else {
                "path": str(cash_dividends_path.resolve()), "sha256": dividend_digest,
            },
            "source_verification": {
                "path": str(verification_path.resolve()),
                "sha256": hashlib.sha256(verification_bytes).hexdigest(),
            },
        },
        "versions": {"project": project_version, "python": platform.python_version(), "runner_format": 3},
    }
    report = build_report(
        symbol=symbol, name=name, start=start, end=end,
        observations=observations, result=result, summary=summary,
    )
    _check_sources_unchanged(source_records, evidence_manifest)
    if _sha256(input_path) != input_digest:
        raise ValueError("输入CSV在回放期间发生变化，请固定文件后重试")
    if _sha256(evidence_manifest) != manifest_digest:
        raise ValueError("证据清单在回放期间发生变化，请固定文件后重试")
    if cash_dividends_path is not None and _sha256(cash_dividends_path) != dividend_digest:
        raise ValueError("Cash dividend event JSON changed during replay")
    output_path.mkdir(parents=True, exist_ok=True)
    verification_path.write_bytes(verification_bytes)
    _write_csv(output_path / "trades.csv", Strategy1Fill, result.fills)
    _write_csv(output_path / "orders.csv", Strategy1Order, result.orders)
    _write_csv(output_path / "daily_equity.csv", Strategy1Equity, result.equity)
    _write_csv(output_path / "closed_batches.csv", Strategy1ClosedBatch, result.closed_batches)
    _write_csv(output_path / "cash_dividends.csv", Strategy1DividendLedger, result.dividend_ledger)
    (output_path / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_value) + "\n", encoding="utf-8")
    (output_path / "report.md").write_text(report, encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从本地通达信截图观测CSV执行策略1当前可见信号复盘")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--price-adjustment", required=True, choices=("none",), help="必须显式确认不复权；当前仅支持none，不自动转换价格")
    parser.add_argument("--allow-sparse-profit", action="store_true", help="显式允许无逻辑需求日P留空；必须使用每日三角色证据JSON，必要P缺失即终止")
    parser.add_argument("--cash-dividends", type=Path, help="显式现金分红事件JSON；不自动推断金额、登记日、除息日或支付日")
    args = parser.parse_args(argv)
    try:
        summary = run_replay(
            input_path=args.input, output_path=args.output, symbol=args.symbol, name=args.name,
            start=args.start, end=args.end, evidence_manifest=args.evidence_manifest,
            price_adjustment=args.price_adjustment,
            allow_sparse_profit=args.allow_sparse_profit,
            cash_dividends_path=args.cash_dividends,
        )
    except (ValueError, OSError, csv.Error) as exc:
        parser.exit(2, f"错误：{exc}\n")
    print(json.dumps({"output": str(args.output.resolve()), "report_type": REPLAY_MODE, "metrics": summary["metrics"]}, ensure_ascii=False, indent=2, default=_json_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
