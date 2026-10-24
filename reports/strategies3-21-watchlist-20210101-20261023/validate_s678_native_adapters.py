"""S6/7/8 SYNTHETIC signal checks. No quotations, orders or market returns.

S7's native SARTURN is an opaque native primitive. Its numeric initialization is
not cloned or certified here. A separate causal SAR reference exercises the saved
adapter, while root must compare actual native TURN output before native use.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest

BASE = Path(__file__).resolve().parent
FORMULAS = BASE / "native-evaluation" / "per-strategy"
OUT = BASE / "native-evaluation" / "additional-6-8"
SPEC = importlib.util.spec_from_file_location("existing_signal_model", BASE / "validate_native_signal_causality.py")
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)
EVIDENCE = {}


def causal_sar_reference(rows, period, step, maximum):
    """Independent causal fixture generator, NOT asserted to equal native SAR."""
    period = int(period)
    assert period >= 2
    turns = [0] * len(rows)
    if len(rows) < period:
        return turns
    up = rows[period - 1]["C"] >= rows[0]["C"]
    sar = min(row["L"] for row in rows[:period]) if up else max(row["H"] for row in rows[:period])
    extreme = max(row["H"] for row in rows[:period]) if up else min(row["L"] for row in rows[:period])
    acceleration = step / 100
    for i in range(period, len(rows)):
        candidate = sar + acceleration * (extreme - sar)
        if up:
            candidate = min(candidate, rows[i - 1]["L"], rows[i - 2]["L"])
            if rows[i]["L"] < candidate:
                up, sar, extreme, acceleration, turns[i] = False, extreme, rows[i]["L"], step / 100, -1
            else:
                sar = candidate
                if rows[i]["H"] > extreme:
                    extreme = rows[i]["H"]
                    acceleration = min(maximum / 100, acceleration + step / 100)
        else:
            candidate = max(candidate, rows[i - 1]["H"], rows[i - 2]["H"])
            if rows[i]["H"] > candidate:
                up, sar, extreme, acceleration, turns[i] = True, extreme, rows[i]["H"], step / 100, 1
            else:
                sar = candidate
                if rows[i]["L"] < extreme:
                    extreme = rows[i]["L"]
                    acceleration = min(maximum / 100, acceleration + step / 100)
    return turns


class AdapterModel(COMMON.FormulaModel):
    def __init__(self, text, *, origin=1, tick=0.01, sarn=10, supplied_turns=None):
        super().__init__(text, barscount_origin=origin)
        self.tick, self.sarn, self.supplied_turns = tick, sarn, supplied_turns

    def common_values(self, rows):
        self.rows, self.n = rows, len(rows)
        env = {key: [row[key] for row in rows] for key in ["C", "H", "L", "DATE", "WEEKDAY"]}
        env.update(PERIOD=5, MINDIFF=self.tick, SARN=self.sarn)
        for name, expression in self.common:
            env[name] = self.node(expression, env)
        return env

    def call(self, name, args):
        if name == "SARTURN":
            return self.supplied_turns[:self.n] if self.supplied_turns is not None else causal_sar_reference(self.rows, *args)
        return super().call(name, args)


def evaluate(sid, rows, **options):
    text = (FORMULAS / f"S{sid:02d}-expert.txt").read_text(encoding="ascii")
    model = AdapterModel(text, **options)
    env = model.common_values(rows)
    return model.selected(env, sid), env


def independent_channel(rows, origin, tick):
    buys, sells = [], []
    for i, row in enumerate(rows):
        ready = i + origin >= 6 and tick > 0
        in_span = 1210101 <= row["DATE"] <= 1261023
        if i < 5:
            b, s = False, False
        else:
            previous = rows[i - 5:i]
            b = ready and row["H"] >= max(x["H"] for x in previous) + tick
            s = ready and row["L"] <= min(x["L"] for x in previous) - tick
        buys.append(in_span and b and not s)
        sells.append(in_span and s)
    return tuple(buys), tuple(sells)


class S678AdapterTests(unittest.TestCase):
    def test_s8_exact_tick_previous_five_and_dual_side_exit(self):
        rows = [COMMON.record(date(2021, 1, i + 1), 100, 101, 99) for i in range(10)]
        rows[6] = COMMON.record(date(2021, 1, 7), 100, 101.01, 99)
        rows[7] = COMMON.record(date(2021, 1, 8), 100, 103, 97)
        for origin in (0, 1):
            actual, env = evaluate(8, rows, origin=origin)
            self.assertEqual(actual, independent_channel(rows, origin, 0.01))
            self.assertTrue(actual[0][6])
            self.assertFalse(actual[0][7])
            self.assertTrue(actual[1][7])
            self.assertEqual(env["CHUP"][6], 101.01)

    def test_s8_does_not_include_breakout_bar_in_channel(self):
        rows = [COMMON.record(date(2021, 1, i + 1), 100, 101, 99) for i in range(10)]
        rows[6] = COMMON.record(date(2021, 1, 7), 109, 110, 100)
        result, env = evaluate(8, rows)
        self.assertEqual(env["CHUP"][6], 101.01)
        self.assertTrue(result[0][6])
        self.assertEqual(env["CHUP"][7], 110.01)

    def test_s8_tick_not_hardcoded_or_nonpositive_fallback(self):
        rows = COMMON.synthetic_rows(700)
        for tick in (0.001, 0.01, 0.1):
            actual, _ = evaluate(8, rows, tick=tick)
            self.assertEqual(actual, independent_channel(rows, 1, tick))
        for tick in (0, -0.01):
            actual, _ = evaluate(8, rows, tick=tick)
            self.assertFalse(any(actual[0]) or any(actual[1]))

    def test_s7_only_native_turn_events_no_repeated_above_below_state(self):
        rows = [COMMON.record(date(2021, 1, 1) + timedelta(days=i), 100) for i in range(100)]
        turns = [0] * len(rows)
        turns[20], turns[40], turns[60] = 1, -1, 1
        actual, _ = evaluate(7, rows, supplied_turns=turns)
        for i in range(len(rows)):
            self.assertEqual(actual[0][i], turns[i] == 1 and 1210101 <= rows[i]["DATE"] <= 1261023)
            self.assertEqual(actual[1][i], turns[i] == -1 and 1210101 <= rows[i]["DATE"] <= 1261023)

    def test_period_sid_date_guards(self):
        for sid in (7, 8):
            rows = COMMON.synthetic_rows(700)
            text = (FORMULAS / f"S{sid:02d}-expert.txt").read_text(encoding="ascii")
            model = AdapterModel(text)
            env = model.common_values(rows)
            for wrong in (0, 3, 6, 7 if sid == 8 else 8, 21, 7.5):
                actual = model.selected(env, wrong)
                self.assertFalse(any(actual[0]) or any(actual[1]))
            # An explicitly non-daily environment must not produce events.
            model.n = len(rows)
            env["DLY"] = False
            actual = model.selected(env, sid)
            self.assertFalse(any(actual[0]) or any(actual[1]))

    def test_all_prior_signals_survive_future_perturbation_and_truncation(self):
        rows = COMMON.synthetic_rows(1600)
        cutoffs = (6, 10, 30, 199, 200, 500, 900, 1250)
        comparisons = 0
        for sid in (7, 8):
            for origin in (0, 1):
                for sarn in ((4,) if sid == 7 else (10,)):
                    options = dict(origin=origin, sarn=sarn)
                    original, _ = evaluate(sid, rows, **options)
                    if sid == 8:
                        self.assertEqual(original, independent_channel(rows, origin, 0.01))
                    self.assertTrue(any(original[0]) and any(original[1]))
                    for cutoff in cutoffs:
                        prefix, _ = evaluate(sid, rows[:cutoff], **options)
                        changed = [dict(row) for row in rows]
                        for i in range(cutoff, len(changed)):
                            close = 20 + i * 17 + 500 * (i % 2)
                            changed[i].update(C=close, H=close + 900, L=close - 10)
                        future_changed, _ = evaluate(sid, changed, **options)
                        for branch in (0, 1):
                            self.assertEqual(prefix[branch], original[branch][:cutoff])
                            self.assertEqual(future_changed[branch][:cutoff], original[branch][:cutoff])
                            comparisons += 2
        EVIDENCE["temporal_comparisons"] = comparisons
        EVIDENCE["fixture"] = "SYNTHETIC_NOT_MARKET_DATA"
        EVIDENCE["sar_numeric_identity"] = "not_tested; causal_reference_primitive_only; actual_native_SARTURN_comparison_required"


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(S678AdapterTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    OUT.mkdir(exist_ok=True)
    EVIDENCE.update(status="pass" if result.wasSuccessful() else "fail", tests_run=result.testsRun,
                    formula_files=[{"sid": sid, "path": f"../per-strategy/S{sid:02d}-expert.txt",
                                    "sha256": sha256((FORMULAS / f"S{sid:02d}-expert.txt").read_bytes()).hexdigest()}
                                   for sid in (7, 8)],
                    script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                    native_ui_operated=False, native_compile_tested=False, native_backtest_run=False,
                    returns_generated=False, limitations=["Native N=4 observed; public three-argument SAR(4,2,20) is selected, not claimed identical to the four-parameter system indicator.",
                    "Model native SAR numeric initialization is not claimed.", "Synthetic temporal evidence does not establish native execution permission."])
    (OUT / "S07-S08-signal-tests.json").write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
