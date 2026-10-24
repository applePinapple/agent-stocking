from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from .indicators import ma
from .models import Action, Bar, Portfolio, Signal


class SignalFileStrategy:
    """Replay explicit close-time decisions; unspecified days mean HOLD."""

    def __init__(self, signals: Mapping[date, Signal]) -> None:
        self.signals = dict(signals)

    def on_close(self, history: Sequence[Bar], portfolio: Portfolio) -> Signal:
        return self.signals.get(history[-1].date, Signal(Action.HOLD))


class DemoMovingAverageCross:
    """Infrastructure demo only, never selected implicitly for a user strategy."""

    def __init__(self, fast: int, slow: int) -> None:
        if type(fast) is not int or type(slow) is not int or not 0 < fast < slow:
            raise ValueError("demo periods must satisfy 0 < fast < slow")
        self.fast = fast
        self.slow = slow

    def on_close(self, history: Sequence[Bar], portfolio: Portfolio) -> Signal:
        if len(history) < self.slow + 1:
            return Signal(Action.HOLD, "demo warm-up")
        # Recompute only the two completed windows needed for the decision.
        closes = [bar.close for bar in history[-self.slow - 1 :]]
        fast = ma(closes, self.fast)
        slow = ma(closes, self.slow)
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
            return Signal(Action.BUY, "DEMO: fast MA crossed above slow MA")
        if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
            return Signal(Action.SELL, "DEMO: fast MA crossed below slow MA")
        return Signal(Action.HOLD)


def load_signals(path: Path) -> dict[date, Signal]:
    """Read strict UTF-8 signal CSV: date,action,reason (reason optional)."""
    signals: dict[date, Signal] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {"date", "action"}.issubset(fields):
            raise ValueError(f"{path}: unique date and action columns are required")
        if set(fields) - {"date", "action", "reason"}:
            raise ValueError(f"{path}: unexpected signal columns")
        previous: date | None = None
        for row in reader:
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("column count does not match header")
                signal_date = date.fromisoformat(row["date"].strip())
                if previous is not None and signal_date <= previous:
                    raise ValueError("signal dates must be strictly increasing and unique")
                action = Action(row["action"].strip())
                signals[signal_date] = Signal(action, row.get("reason", "").strip())
                previous = signal_date
            except ValueError as exc:
                raise ValueError(f"{path}:{reader.line_num}: {exc}") from exc
    if not signals:
        raise ValueError(f"{path}: signal file is empty")
    return signals

