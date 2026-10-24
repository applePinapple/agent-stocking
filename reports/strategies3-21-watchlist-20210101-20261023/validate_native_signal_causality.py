"""Signal-only temporal checks on SYNTHETIC data; no market returns or orders.

Parses the saved TDX expression source instead of maintaining separate buy/sell
rules. The scalar/vector primitives are an explicit causal model, not the native
V7.71 implementation. Native initialization and execution are checked in the UI.
"""
import ast
from datetime import date, timedelta
from hashlib import sha256
import io
import json
import math
import operator
from pathlib import Path
import re
import sys
import unittest

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation"
FORMULA = OUT / "B321NAT-expert.txt"
SUPPORTED = [3, 4, 5, 9, 10, 12, 15, 16, 17, 18, 19]
EXCLUDED = [6, 7, 8, 11, 13, 14, 20, 21]
EVIDENCE = {}


def pointwise(fn, *args):
    arrays = [a for a in args if isinstance(a, list)]
    if not arrays:
        return fn(*args)
    size = len(arrays[0])
    assert all(len(a) == size for a in arrays)
    return [fn(*(a[i] if isinstance(a, list) else a for a in args)) for i in range(size)]


class FormulaModel:
    """Restricted expression interpreter with only past/current primitives."""
    def __init__(self, text, barscount_origin=1):
        self.origin = barscount_origin
        self.n = 0
        self.common, self.selection = [], []
        code = re.sub(r"\{.*?\}", "", text, flags=re.S)
        for statement in code.split(";"):
            if not statement.strip():
                continue
            name, expression = re.split(r":=|:", statement.strip(), maxsplit=1)
            expression = re.sub(r"\bAND\b", "and", expression)
            expression = re.sub(r"\bOR\b", "or", expression)
            expression = re.sub(r"(?<![<>!=])=(?!=)", "==", expression)
            parsed = ast.parse("(" + expression + ")", mode="eval").body
            target = self.selection if name in {"SUPPORTED", "RAWBUY", "RAWSELL", "ENTERLONG", "EXITLONG"} else self.common
            target.append((name, parsed))

    def node(self, value, env):
        if isinstance(value, ast.Constant):
            return value.value
        if isinstance(value, ast.Name):
            return env[value.id]
        if isinstance(value, ast.BinOp):
            ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
            return pointwise(ops[type(value.op)], self.node(value.left, env), self.node(value.right, env))
        if isinstance(value, ast.UnaryOp):
            ops = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
            return pointwise(ops[type(value.op)], self.node(value.operand, env))
        if isinstance(value, ast.BoolOp):
            result = self.node(value.values[0], env)
            fn = (lambda a, b: bool(a) and bool(b)) if isinstance(value.op, ast.And) else (lambda a, b: bool(a) or bool(b))
            for operand in value.values[1:]:
                result = pointwise(fn, result, self.node(operand, env))
            return result
        if isinstance(value, ast.Compare):
            assert len(value.ops) == 1
            ops = {ast.Eq: operator.eq, ast.Lt: operator.lt, ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge, ast.NotEq: operator.ne}
            return pointwise(ops[type(value.ops[0])], self.node(value.left, env), self.node(value.comparators[0], env))
        if isinstance(value, ast.Call):
            assert isinstance(value.func, ast.Name)
            # Eager arguments exercise zero-division protection inside IF branches.
            return self.call(value.func.id, [self.node(a, env) for a in value.args])
        raise ValueError("Unsupported AST: " + ast.dump(value))

    def call(self, name, args):
        if name in {"ABS", "MAX", "MIN", "NOT"}:
            fn = {"ABS": abs, "MAX": max, "MIN": min, "NOT": operator.not_}[name]
            return pointwise(fn, *args)
        if name == "IF":
            return pointwise(lambda condition, a, b: a if condition else b, *args)
        if name == "BARSCOUNT":
            return [i + self.origin for i in range(self.n)]
        if name == "REF":
            x, count = args
            assert int(count) == count and count >= 0
            return [x[max(0, i - int(count))] for i in range(self.n)]
        if name in {"EMA", "SMA"}:
            x, window = args[:2]
            weight = (2 / (window + 1)) if name == "EMA" else args[2] / window
            result = []
            for current in x:
                result.append(float(current) if not result else weight * current + (1 - weight) * result[-1])
            return result
        if name in {"MA", "AVEDEV", "HHV", "LLV"}:
            x, window = args
            assert int(window) == window and window > 0
            result = []
            for i in range(self.n):
                past = x[max(0, i - int(window) + 1): i + 1]
                if name in {"MA", "AVEDEV"}:
                    average = sum(past) / len(past)
                    result.append(average if name == "MA" else sum(abs(v - average) for v in past) / len(past))
                else:
                    result.append((max if name == "HHV" else min)(past))
            return result
        if name == "DATETODAY":
            def convert(value):
                encoded = int(value) + 19000000
                current = date(encoded // 10000, (encoded // 100) % 100, encoded % 100)
                return (current - date(1990, 12, 19)).days
            return pointwise(convert, args[0])
        raise ValueError("Unapproved primitive: " + name)

    def common_values(self, rows):
        self.n = len(rows)
        env = {key: [r[key] for r in rows] for key in ["C", "H", "L", "DATE", "WEEKDAY"]}
        env["PERIOD"] = 5
        for name, expression in self.common:
            env[name] = self.node(expression, env)
        return env

    def selected(self, common, sid):
        env = dict(common, SID=sid)
        self.n = len(env["C"])
        for name, expression in self.selection:
            env[name] = self.node(expression, env)
        return tuple(tuple(bool(x) for x in env[name]) for name in ["ENTERLONG", "EXITLONG"])

    def individual(self, name, env):
        self.n = len(env["C"])
        return self.node(dict(self.common)[name], env)


def record(day, close, high=None, low=None):
    return {"DATE": int(day.strftime("%Y%m%d")) - 19000000, "WEEKDAY": (day.weekday() + 1) % 7,
            "C": close, "H": close + 1 if high is None else high, "L": close - 1 if low is None else low}


def synthetic_rows(size=1600):
    rows, day = [], date(2020, 1, 2)
    while len(rows) < size:
        if day.weekday() < 5:
            i = len(rows)
            close = 100 + 0.14 * i + 5 * math.sin(i / 9) + 2 * math.sin(i / 3)
            phase = i % 61
            if phase < 5:
                close -= 2.5 * (phase + 1)
                rows.append(record(day, close, close + 1.9, close - 0.1))
            else:
                rows.append(record(day, close))
        day += timedelta(days=1)
    return rows


class SignalTemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FORMULA.read_text(encoding="ascii")
        cls.rows = synthetic_rows()
        fixture = {"kind": "SYNTHETIC_TEST_DATA_NOT_MARKET_QUOTES", "generation": "deterministic_trend_sine_and_pullback_fixture_v1; civil weekdays are not an exchange calendar", "rows": cls.rows}
        raw = json.dumps(fixture, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (OUT / "synthetic-signal-fixture.json").write_bytes(raw)
        EVIDENCE["fixture_sha256"] = sha256(raw).hexdigest()
        EVIDENCE["synthetic_bar_count"] = len(cls.rows)

    def test_future_perturbation_and_prefix_truncation(self):
        checks = []
        for origin in [0, 1]:
            model = FormulaModel(self.text, origin)
            common = model.common_values(self.rows)
            original = {sid: model.selected(common, sid) for sid in range(3, 22)}
            if origin == 1:
                counts = {str(sid): {"synthetic_buy_signals": sum(original[sid][0]), "synthetic_sell_signals": sum(original[sid][1])} for sid in SUPPORTED}
                EVIDENCE["synthetic_event_counts_NOT_market_results"] = counts
                self.assertTrue(all(v["synthetic_buy_signals"] > 0 for v in counts.values()), counts)
            for cut in [199, 200, 260, 500, 900, 1250]:
                changed = [dict(r) for r in self.rows]
                for i in range(cut, len(changed)):
                    # Radical, positive and valid OHLC suffix. Prefix stays byte-for-byte equal.
                    changed[i]["C"] = 5000 + i * 71 + 200 * math.sin(i)
                    changed[i]["H"] = changed[i]["C"] + 800
                    changed[i]["L"] = changed[i]["C"] - 700
                future = model.common_values(changed)
                truncated = model.common_values(self.rows[:cut])
                for sid in range(3, 22):
                    expected = tuple(series[:cut] for series in original[sid])
                    self.assertEqual(expected, tuple(series[:cut] for series in model.selected(future, sid)), (origin, cut, sid, "future"))
                    self.assertEqual(expected, model.selected(truncated, sid), (origin, cut, sid, "truncation"))
                    checks.append({"barscount_origin": origin, "prefix_bars": cut, "sid": sid, "future_changed": "pass", "future_removed": "pass"})
        EVIDENCE["temporal_cases"] = checks

    def test_unsupported_ids_are_no_signal(self):
        model = FormulaModel(self.text)
        common = model.common_values(self.rows)
        for sid in EXCLUDED + [3.5, 2, 22]:
            self.assertFalse(any(any(x) for x in model.selected(common, sid)), sid)
        EVIDENCE["unsupported_ids_are_no_signal_and_N_A"] = EXCLUDED

    def test_flat_and_zero_range_no_division_error(self):
        flat = [dict(r, C=100.0, H=100.0, L=100.0) for r in self.rows[:400]]
        for origin in [0, 1]:
            model = FormulaModel(self.text, origin)
            common = model.common_values(flat)
            for sid in range(3, 22):
                model.selected(common, sid)
            for sid in [4, 5, 9, 12, 16, 17, 18]:
                self.assertFalse(any(model.selected(common, sid)[0]), sid)
            self.assertTrue(all(x == 50 for x in common["RSIV"]))
            self.assertFalse(any(common["RSIOK"]))
            self.assertFalse(any(common["CCIOK"]))
            self.assertFalse(any(common["WROK"]))
        EVIDENCE["zero_denominator_eager_IF_checks"] = "pass_both_barscount_origins"

    def test_same_bar_conflict_exit_dominates(self):
        # 7 equal closes above their long average: both Double7 raw conditions true.
        rows = [dict(r, C=100.0, H=101.0, L=99.0) for r in self.rows[:400]]
        rows[-7:] = [dict(r, C=200.0, H=201.0, L=199.0) for r in rows[-7:]]
        model = FormulaModel(self.text)
        common = model.common_values(rows)
        self.assertTrue(common["B10"][-1] and common["S10"][-1])
        buy, sell = model.selected(common, 10)
        self.assertFalse(buy[-1])
        self.assertTrue(sell[-1])
        EVIDENCE["same_bar_conflict"] = "raw_buy_and_sell_true_only_exit_output_true"

    def test_exact_weekdays_across_calendar_boundaries(self):
        cases = [
            ("2021-02-25", "2021-02-26", "2021-03-01", True),
            ("2023-12-28", "2023-12-29", "2024-01-01", True),
            ("2024-02-29", "2024-03-01", "2024-03-04", True),
            ("2024-02-08", "2024-02-09", "2024-02-19", False),
            ("2024-03-06", "2024-03-08", "2024-03-11", False),
        ]
        results = []
        for thu, fri, mon, expected in cases:
            dates = [date.fromisoformat(x) for x in [thu, fri, mon]]
            preceding = dates[0] - timedelta(days=50)
            rows = [record(preceding + timedelta(days=i), 20) for i in range(30)]
            rows += [record(d, c) for d, c in zip(dates, [12, 11, 10])]
            model = FormulaModel(self.text)
            common = model.common_values(rows)
            self.assertEqual(expected, common["MONOK"][-1], (thu, fri, mon))
            self.assertEqual(expected, common["B18"][-1], (thu, fri, mon))
            results.append({"date_triplet": [thu, fri, mon], "expected_literal_weekday_match": expected, "result": "pass"})
        EVIDENCE["weekday_gap_cases_on_synthetic_rows"] = results

    def test_explicit_equal_boundaries_not_implicit_cross(self):
        model = FormulaModel(self.text)
        env = {"C": [1, 1, 1], "R200": [True] * 3, "MHIST": [-1, 0, 1]}
        self.assertEqual([False, False, False], model.individual("B4", env))
        env.update({"CCIOK": [True] * 3, "CCIV": [99, 100, 101]})
        self.assertEqual([False, False, False], model.individual("B5", env))
        env.update({"RSIOK": [True] * 3, "RSIV": [69, 70, 71]})
        self.assertEqual([False, False, True], model.individual("S12", env))
        EVIDENCE["explicit_equality_cases"] = "MACD_zero_gap_skips_CCI_prior100_skips_RSI_prior70_crosses"

    def test_future_calendar_plan_deletion(self):
        source = json.loads((BASE / "strategy19-calendar.json").read_text(encoding="utf-8-sig"))
        full_buy = {e["buy_tdx_date"] for e in source["events"]}
        full_sell = {e["sell_tdx_date"] for e in source["events"]}
        active_buy = set(source["active_buy_tdx_dates"])
        active_sell = set(source["active_sell_tdx_dates"])
        cursor, cutoff, count = date(2021, 1, 1), date(2026, 10, 23), 0
        while cursor <= cutoff:
            value = int(cursor.strftime("%Y%m%d")) - 19000000
            self.assertEqual((value in full_buy, value in full_sell), (value in active_buy, value in active_sell))
            cursor += timedelta(days=1)
            count += 1
        EVIDENCE["strategy19_future_plan_removal"] = {"past_civil_dates_checked": count, "result": "pass"}


def main():
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SignalTemporalTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    (OUT / "signal-causality-tests.log").write_text(log, encoding="utf-8")
    report = {
        "status": "pass" if result.wasSuccessful() else "fail",
        "kind": "SYNTHETIC_SIGNAL_ONLY_CAUSALITY_TEST_NOT_MARKET_BACKTEST",
        "formula_path": FORMULA.name,
        "formula_sha256": sha256(FORMULA.read_bytes()).hexdigest(),
        "tester_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "native_ui_operated": False,
        "native_compiled": False,
        "returns_or_orders_calculated": False,
        "real_quotes_read": False,
        "interpretation_limit": "Directly parses the saved formula using a causal scalar/vector model. Partial-window means, first-value EMA/SMA seed and REF beginning fill are explicit TEST model choices. Both BARSCOUNT origins are checked. This is not proof of native numerical identity, native fill semantics or market returns.",
        "evidence": EVIDENCE,
    }
    (OUT / "signal-causality-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(log)
    print(json.dumps({k: report[k] for k in ["status", "kind", "tests_run", "failures", "errors", "returns_or_orders_calculated"]}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
