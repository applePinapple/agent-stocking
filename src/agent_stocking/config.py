from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import EngineConfig


@dataclass(frozen=True, slots=True)
class RunConfig:
    name: str
    data_path: Path
    data_format: str
    symbol: str
    volume_unit: str
    source: str
    strategy: dict[str, Any]
    signals_path: Path | None
    execution: EngineConfig
    raw: dict[str, Any]


def _object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    missing, unknown = fields - value.keys(), value.keys() - fields
    if missing or unknown:
        raise ValueError(f"{name}: missing={sorted(missing)}, unknown={sorted(unknown)}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _decimal(value: Any, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"execution.{name} must be explicitly supplied as a decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"execution.{name} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"execution.{name} must be finite")
    return result


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def load_config(path: Path) -> RunConfig:
    with path.open("r", encoding="utf-8-sig") as handle:
        raw = json.load(handle, object_pairs_hook=_unique_object, parse_float=Decimal)
    raw = _object(raw, "config", {"schema_version", "name", "data", "strategy", "execution"})
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("unsupported schema_version (expected 1)")
    name = _text(raw["name"], "name")
    strategy = raw["strategy"]
    if not isinstance(strategy, dict):
        raise ValueError("strategy must be an object")
    kind = strategy.get("kind")
    if kind == "pending":
        raise ValueError("策略尚未定义：请先提供并明确买卖、仓位和成交规则，再填写正式回测配置。")
    if kind == "ma_cross_demo":
        _object(strategy, "strategy", {"kind", "fast", "slow"})
        fast, slow = strategy["fast"], strategy["slow"]
        if type(fast) is not int or type(slow) is not int or not 0 < fast < slow:
            raise ValueError("demo periods must satisfy 0 < fast < slow")
    elif kind == "signals":
        _object(strategy, "strategy", {"kind", "path"})
        _text(strategy["path"], "strategy.path")
    else:
        raise ValueError(f"unsupported strategy kind: {kind!r}")
    data = _object(raw["data"], "data", {
        "path", "format", "symbol", "volume_unit", "source", "price_adjustment", "corporate_actions",
    })
    _text(data["path"], "data.path")
    symbol = _text(data["symbol"], "data.symbol")
    if data["format"] not in ("csv", "tdx_day"):
        raise ValueError("data.format must be csv or tdx_day")
    if data["volume_unit"] not in ("shares", "lots"):
        raise ValueError("data.volume_unit must be shares or lots")
    if data["format"] == "tdx_day" and data["volume_unit"] != "shares":
        raise ValueError("tdx_day volume is already shares")
    if data["source"] not in ("synthetic", "user_supplied"):
        raise ValueError("data.source must be synthetic or user_supplied")
    if data["price_adjustment"] != "none" or data["corporate_actions"] != "none":
        raise ValueError("仅支持明确不复权且区间内无需除权除息处理的数据；其他情况需先扩展公司行动处理。")
    if kind == "ma_cross_demo" and data["source"] != "synthetic":
        raise ValueError("MA demo is restricted to synthetic data")
    execution = _object(raw["execution"], "execution", {
        "initial_cash", "lot_size", "commission_rate", "min_commission", "sell_tax_rate",
        "transfer_fee_rate", "slippage_bps", "settlement_days", "require_trade_permissions",
    })
    decimal_names = {
        "initial_cash", "commission_rate", "min_commission", "sell_tax_rate",
        "transfer_fee_rate", "slippage_bps",
    }
    values = {key: _decimal(value, key) if key in decimal_names else value
              for key, value in execution.items()}
    engine = EngineConfig(**values)
    if data["source"] == "user_supplied" and not engine.require_trade_permissions:
        raise ValueError("正式数据必须 require_trade_permissions=true，并逐日提供可买/可卖状态。")
    base = path.resolve().parent
    return RunConfig(
        name=name, data_path=(base / data["path"]).resolve(), data_format=data["format"],
        symbol=symbol, volume_unit=data["volume_unit"], source=data["source"],
        strategy=strategy, signals_path=(base / strategy["path"]).resolve() if kind == "signals" else None,
        execution=engine, raw=raw,
    )

