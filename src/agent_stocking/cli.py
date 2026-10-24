from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import DecimalException
from pathlib import Path
from uuid import uuid4

from .config import load_config
from .data import load_csv, load_tdx_day, write_csv
from .engine import run_backtest
from .reporting import file_hash, write_report
from .strategies import DemoMovingAverageCross, SignalFileStrategy, load_signals


def _load_data(path: Path, fmt: str, volume_unit: str):
    return load_tdx_day(path) if fmt == "tdx_day" else load_csv(path, volume_unit=volume_unit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通达信策略本地日线回测（不连接交易账户）")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="运行合成数据的框架演示")
    demo.add_argument("--output", type=Path)
    backtest = sub.add_parser("backtest", help="按显式配置执行回测")
    backtest.add_argument("--config", type=Path, required=True)
    backtest.add_argument("--output", type=Path, required=True)
    convert = sub.add_parser("import-day", help="把本地通达信 .day 转为标准 CSV")
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate-data", help="验证单标的行情，保留原始文件")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--format", choices=("csv", "tdx_day"), default="csv")
    validate.add_argument("--volume-unit", choices=("shares", "lots"), default="shares")
    return parser


def _backtest(config_path: Path, output: Path) -> None:
    config_digest = file_hash(config_path)
    config = load_config(config_path)
    paths = {"config": config_path, "data": config.data_path}
    if config.signals_path is not None:
        paths["signals"] = config.signals_path
    hashes = {name: file_hash(path) for name, path in paths.items()}
    if hashes["config"] != config_digest:
        raise ValueError("配置文件在读取期间发生变化，请固定文件后重试。")
    bars = _load_data(config.data_path, config.data_format, config.volume_unit)
    if config.signals_path is not None:
        signals = load_signals(config.signals_path)
        missing = signals.keys() - {bar.date for bar in bars}
        if missing:
            raise ValueError(f"信号日期不在行情中：{', '.join(map(str, sorted(missing)))}")
        strategy = SignalFileStrategy(signals)
    else:
        strategy = DemoMovingAverageCross(config.strategy["fast"], config.strategy["slow"])
    result = run_backtest(bars, strategy, config.execution)
    # Avoid recording hashes for inputs that changed while the run was in progress.
    if any(file_hash(path) != hashes[name] for name, path in paths.items()):
        raise ValueError("输入文件在回测期间发生变化，请固定文件后重试。")
    summary = write_report(result, config, config_path, output, input_hashes=hashes)
    metrics = summary["metrics"]
    prefix = "合成数据演示" if summary["is_synthetic"] else "历史回测"
    print(f"{prefix}完成；总收益率 {metrics['total_return']:.2%}；最大回撤 {metrics['max_drawdown']:.2%}")
    print(f"结果目录：{output.resolve()}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo":
            root = Path(__file__).resolve().parents[2]
            output = args.output or root / "reports" / "demo"
            if args.output is None and output.exists() and any(output.iterdir()):
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                output = root / "reports" / f"demo-{stamp}-{uuid4().hex[:6]}"
            _backtest(root / "configs" / "demo.json", output)
        elif args.command == "backtest":
            _backtest(args.config, args.output)
        elif args.command == "import-day":
            if args.output.exists():
                raise ValueError(f"输出文件已存在，拒绝覆盖：{args.output}")
            bars = load_tdx_day(args.input)
            write_csv(bars, args.output)
            print(f"已导入 {len(bars)} 条日线：{args.output.resolve()}")
            print("可买卖状态仍为空；正式回测前须核对单位、复权、公司行动并补齐状态。")
        else:
            if args.format == "tdx_day" and args.volume_unit != "shares":
                raise ValueError("tdx_day 的 volume-unit 必须为 shares")
            bars = _load_data(args.input, args.format, args.volume_unit)
            complete = all(b.buy_allowed is not None and b.sell_allowed is not None for b in bars)
            print(f"行情校验通过：{len(bars)} 条，{bars[0].date} 至 {bars[-1].date}")
            print(f"可买卖状态：{'完整' if complete else '缺失，无法进行严格成交回测'}")
        return 0
    except (OSError, ValueError, DecimalException, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
