from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from . import __version__
from .config import RunConfig
from .models import Action, BacktestResult, EquityPoint, OrderEvent, Trade


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def calculate_metrics(result: BacktestResult) -> dict[str, Any]:
    if not result.equity:
        raise ValueError("cannot report a backtest without equity observations")
    initial, final = result.initial_cash, result.equity[-1].equity
    if initial <= 0:
        raise ValueError("initial cash must be positive")
    total_return = float(final / initial - 1)
    peak, max_drawdown = initial, Decimal(0)
    for point in result.equity:
        peak = max(peak, point.equity)
        max_drawdown = max(max_drawdown, (peak - point.equity) / peak)
    elapsed = (result.equity[-1].date - result.equity[0].date).days
    annualized: float | None = None
    if elapsed > 0:
        try:
            annualized = math.expm1(math.log(float(final / initial)) * 365.25 / elapsed) if final else -1.0
            if not math.isfinite(annualized):
                annualized = None
        except (OverflowError, ValueError):
            annualized = None
    fees = sum((t.commission + t.tax + t.transfer_fee for t in result.trades), Decimal(0))
    realized = Decimal(0)
    entry_cost: Decimal | None = None
    closed = wins = 0
    for trade in result.trades:
        costs = trade.commission + trade.tax + trade.transfer_fee
        if trade.side is Action.BUY:
            entry_cost = trade.notional + costs
        elif trade.side is Action.SELL:
            if entry_cost is None:
                raise ValueError("sell without corresponding buy in result")
            pnl = trade.notional - costs - entry_cost
            realized += pnl
            closed += 1
            wins += int(pnl > 0)
            entry_cost = None
    ending = result.equity[-1]
    unrealized = ending.equity - ending.cash - entry_cost if entry_cost is not None else Decimal(0)
    return {
        "start_date": result.equity[0].date,
        "end_date": ending.date,
        "bars": len(result.equity),
        "initial_cash": initial,
        "final_equity": final,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(max_drawdown),
        "underlying_close_return": float(ending.close / result.equity[0].close - 1),
        "transaction_count": len(result.trades),
        "round_trip_count": closed,
        "win_rate": wins / closed if closed else None,
        "total_fees": fees,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "ending_cash": ending.cash,
        "ending_quantity": ending.quantity,
        "rejected_orders": sum(o.status == "rejected" for o in result.orders),
        "expired_orders": sum(o.status == "expired" for o in result.orders),
    }


def _csv(path: Path, model: type, records: list) -> None:
    names = [field.name for field in fields(model)]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for record in records:
            writer.writerow(jsonable(asdict(record)))


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def write_report(
    result: BacktestResult, config: RunConfig, config_path: Path, output: Path,
    *, input_hashes: dict[str, str],
) -> dict[str, Any]:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"输出目录必须不存在或为空，避免覆盖已有结果：{output}")
    metrics = calculate_metrics(result)
    assumptions = [
        "单标的日线，仅做多，全仓买入、全部卖出；不加仓、不融资。",
        "收盘信号在下一条日线开盘尝试一次；拒单当日取消，末日信号过期。",
        "settlement_days 按输入日线条数计数；默认 1，日线内不进行买卖回转。",
        "按显式可买卖状态、成交量非零及滑点价格范围检查；不模拟逐笔排队、开盘容量或冲击成本。",
        "手续费按每笔每项四舍五入到分；滑点后的单价保留计算精度，不模拟最小报价单位。",
        "区间末持仓按收盘价估值，不强制卖出，不预扣未来卖出费用。",
        "仅支持已确认不复权且无需公司行动处理的区间；费率为配置的恒定值。",
        "年化采用首末日期自然日差与 365.25；最大回撤含初始资金基线；胜率仅统计已平仓往返。",
        "underlying_close_return 为标的首末收盘价涨幅，不含交易费用、股息或资金约束。",
    ]
    if config.source == "synthetic":
        assumptions.insert(0, "合成数据演示结果，不是用户策略或真实股票收益。")
    summary = {
        "schema_version": 1,
        "name": config.name,
        "symbol": config.symbol,
        "is_synthetic": config.source == "synthetic",
        "metrics": metrics,
        "config": config.raw,
        "provenance": {
            "program_version": __version__, "python_version": platform.python_version(),
            "code_sha256": code_hash(), "config_path": str(config_path.resolve()),
            "data_path": str(config.data_path), "input_sha256": input_hashes,
        },
        "assumptions": assumptions,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(jsonable(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    _csv(output / "trades.csv", Trade, result.trades)
    _csv(output / "orders.csv", OrderEvent, result.orders)
    _csv(output / "equity.csv", EquityPoint, result.equity)
    title = "合成数据演示回测" if config.source == "synthetic" else "策略历史回测"
    lines = [
        f"# {title}", "", config.name, "",
        f"标的：{config.symbol}；区间：{metrics['start_date']} 至 {metrics['end_date']}。", "",
        "| 指标 | 结果 |", "| --- | ---: |",
        f"| 初始资金 | {_money_text(metrics['initial_cash'])} |",
        f"| 期末总资产 | {_money_text(metrics['final_equity'])} |",
        f"| 总收益率 | {_percent(metrics['total_return'])} |",
        f"| 年化收益率 | {_percent(metrics['annualized_return'])} |",
        f"| 最大回撤 | {_percent(metrics['max_drawdown'])} |",
        f"| 标的收盘价涨幅 | {_percent(metrics['underlying_close_return'])} |",
        f"| 成交笔数 / 已平仓往返 | {metrics['transaction_count']} / {metrics['round_trip_count']} |",
        f"| 已平仓胜率 | {_percent(metrics['win_rate'])} |",
        f"| 总费用 | {_money_text(metrics['total_fees'])} |",
        f"| 期末持股数 | {metrics['ending_quantity']} |", "", "## 计算口径", "",
        *[f"- {assumption}" for assumption in assumptions], "",
        "完整配置、输入 SHA-256 与程序指纹见 `summary.json`；成交、拒单与每日资产见同目录 CSV。", "",
    ]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def _money_text(value: Decimal) -> str:
    return f"{value:.2f}"

