"""Equivalent interval images and separate direction booleans, no crossformula.

All v1/v2 evidence is retained. This is a source-expression reduction, not an
ATR-channel replacement, shortened history, changed Supertrend or fitted rule.
Native operator arithmetic and capacity must still be checked in the client.
"""
from datetime import date
from decimal import Decimal as D
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import unittest

BASE = Path(__file__).resolve().parent
FORMULAS = BASE / "native-evaluation/per-strategy"
OUT = BASE / "native-evaluation/additional-6-8"


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


OLD = module("s06_old_math", "prepare_s06_interval_formula.py")
INPUTS = module("s06_native_inputs", "audit_s068_native_precision.py")
ADAPTERS = OLD.ADAPTERS
COMMON = OLD.COMMON
DEPTH = 128
NAMES = ["S06-standalone-v3-expert.txt", "S06-standalone-v3-indicator-check.txt"]


def compact_enclosures(rows, depth=128):
    upper, lower = OLD.band_inputs(rows)
    a, b, d, e, g, v = [], [], [], [], [], []
    for i in range(len(rows)):
        seeded = i < 10
        a.append(upper[i] if seeded else min(upper[:i + 1]))
        b.append(upper[i] if seeded else max(upper[:i + 1]))
        d.append(lower[i] if seeded else min(lower[:i + 1]))
        e.append(lower[i] if seeded else max(lower[:i + 1]))
        g.append(not seeded)
        v.append(True)
    for _ in range(depth):
        new = [[] for _ in range(6)]
        for i, row in enumerate(rows):
            if i < 10:
                state = upper[i], upper[i], lower[i], lower[i], False, True
            else:
                p, x, y = rows[i - 1]["C"], upper[i], lower[i]
                ua = x if b[i - 1] < p else min(x, max(a[i - 1], p))
                ub = x if a[i - 1] < p or b[i - 1] > x else b[i - 1]
                ld = y if d[i - 1] < y or e[i - 1] > p else d[i - 1]
                le = y if d[i - 1] > p else max(y, min(e[i - 1], p))
                ug = (v[i - 1] and row["C"] > ua) or (g[i - 1] and row["C"] >= ld)
                dv = (v[i - 1] and row["C"] <= ub) or (g[i - 1] and row["C"] < le)
                state = ua, ub, ld, le, ug, dv
            for array, value in zip(new, state):
                array.append(value)
        a, b, d, e, g, v = new
    return a, b, d, e, g, v


def sources(depth=128):
    lines = ["DLY:=PERIOD=5;", "SPAN:=DATE>=1210101 AND DATE<=1261023;",
             "N:=SUM(C=C,0);", "Z:=N<=10;", "NZ:=NOT(Z);", "P:=REF(C,1);",
             "R:=IF(N=1,H-L,MAX(H-L,MAX(ABS(H-P),ABS(L-P))));",
             "T:=SUM(R,10)/10;", "X:=(H+L)/2+3*T;", "Y:=(H+L)/2-3*T;",
             "A0:=IF(Z,X,LLV(X,0));", "B0:=IF(Z,X,HHV(X,0));",
             "D0:=IF(Z,Y,LLV(Y,0));", "E0:=IF(Z,Y,HHV(Y,0));", "G0:=NZ;", "V0:=C=C;"]
    for i in range(1, depth + 1):
        q, p = OLD.base36(i), OLD.base36(i - 1)
        lines += [
            f"A{q}:=IF(Z OR REF(B{p},1)<P,X,MIN(X,MAX(REF(A{p},1),P)));",
            f"B{q}:=IF(Z OR REF(A{p},1)<P OR REF(B{p},1)>X,X,REF(B{p},1));",
            f"D{q}:=IF(Z OR REF(D{p},1)<Y OR REF(E{p},1)>P,Y,REF(D{p},1));",
            f"E{q}:=IF(Z OR REF(D{p},1)>P,Y,MAX(Y,MIN(REF(E{p},1),P)));",
            f"G{q}:=NZ AND ((REF(V{p},1) AND C>A{q}) OR (REF(G{p},1) AND C>=D{q}));",
            f"V{q}:=Z OR (REF(V{p},1) AND C<=B{q}) OR (REF(G{p},1) AND C<E{q});",
        ]
    q = OLD.base36(depth)
    lines += [f"K:=G{q}!=V{q};",
              f"BUY6:=N>10 AND K AND REF(K,1) AND G{q} AND REF(V{q},1);",
              f"SELL6:=N>10 AND K AND REF(K,1) AND V{q} AND REF(G{q},1);"]
    expert = lines + ["ENTERLONG:DLY AND SPAN AND SID=6 AND BUY6 AND NOT(SELL6);",
                      "EXITLONG:DLY AND SPAN AND SID=6 AND SELL6;"]
    diagnostic = lines + ["UNKNOWN:SUM(SPAN AND N>10 AND (NOT(K) OR NOT(REF(K,1))),0);",
                          "BADINPUT:SUM(NOT(ISVALID(C)) OR NOT(ISVALID(H)) OR NOT(ISVALID(L)) OR H<L,0);",
                          f"DIRECTION:IF(K,IF(G{q},1,-1),0);",
                          f"UPPERLO:A{q};", f"UPPERHI:B{q};", f"LOWERLO:D{q};", f"LOWERHI:E{q};"]
    def compact(statements):
        names = [re.split(r":=|:", s, maxsplit=1)[0] for s in statements]
        assert len(names) == len(set(names)), "Every native definition must be unique"
        assert all('"' not in s for s in statements), "No crossformula calls"
        body = "\n".join("".join(statements[i:i + 2]) for i in range(0, len(statements), 2))
        body = body.replace(" AND ", "&&")
        return f"{{S06 STANDALONE V3 DEPTH {depth}; SAME INTERVAL IMAGES; NO CROSSFORMULA.}}\n" + body + "\n"
    return compact(expert), compact(diagnostic)


class CompactModel(ADAPTERS.AdapterModel):
    def __init__(self, text):
        super().__init__(text.replace("&&", " AND "), origin=0)

    def call(self, name, args):
        if name in {"SUM", "HHV", "LLV"}:
            sequence, count = args
            if not isinstance(sequence, list):
                sequence = [sequence] * self.n
            if name == "SUM":
                return [sum(sequence[max(0, i - int(count) + 1):i + 1] if count else sequence[:i + 1]) for i in range(self.n)]
            if count == 0:
                return [(max if name == "HHV" else min)(sequence[:i + 1]) for i in range(self.n)]
        return super().call(name, args)


def model(rows, depth=128):
    engine = CompactModel(sources(depth)[0])
    env = engine.common_values(rows)
    return engine.selected(env, 6), env


class CompactTests(unittest.TestCase):
    def test_interval_image_equalities_include_all_boundary_candidates(self):
        for a in range(-3, 4):
            for b in range(a, 4):
                for p in range(-3, 4):
                    for x in range(-3, 4):
                        values = [D(i) / 2 for i in range(2 * a, 2 * b + 1)]
                        upper = [D(x) if x < t or p > t else t for t in values]
                        lower = [D(x) if x > t or p < t else t for t in values]
                        compact_upper = (x if b < p else min(x, max(a, p)), x if a < p or b > x else b)
                        compact_lower = (x if a < x or b > p else a, x if a > p else max(x, min(b, p)))
                        self.assertEqual(compact_upper, (min(upper), max(upper)))
                        self.assertEqual(compact_lower, (min(lower), max(lower)))

    def test_separate_direction_booleans_equal_every_old_encoded_state_case(self):
        for prev_g in (False, True):
            for prev_v in (False, True):
                for close in range(-3, 4):
                    for lo in range(-3, 4):
                        for hi in range(lo, 4):
                            code = int(prev_g) + 2 * int(prev_v)
                            old_g = (code >= 2 and close > lo) or (code % 2 > 0 and close >= lo)
                            old_v = (code >= 2 and close <= hi) or (code % 2 > 0 and close < hi)
                            self.assertEqual(old_g, (prev_v and close > lo) or (prev_g and close >= lo))
                            self.assertEqual(old_v, (prev_v and close <= hi) or (prev_g and close < hi))

    def test_compact_layer_state_and_parsed_source_equal_old_expansion(self):
        rows = COMMON.synthetic_rows(400)
        for depth in (1, 16, 64, 128):
            old = OLD.expanded_enclosures(rows, depth)
            compact = compact_enclosures(rows, depth)
            self.assertEqual(compact, old)
        _, env = model(rows, 128)
        suffix = OLD.base36(128)
        for name, state in zip(("A", "B", "D", "E", "G", "V"), compact):
            self.assertEqual(env[name + suffix], state)

    def test_source_prefix_and_future_changes_do_not_change_old_states(self):
        rows = COMMON.synthetic_rows(500)
        baseline, baseline_env = model(rows, 128)
        cutoff = 320
        prefix, prefix_env = model(rows[:cutoff], 128)
        changed = [dict(r) for r in rows]
        for i in range(cutoff, len(rows)):
            changed[i].update(C=10000 + i, H=20000 + i, L=1)
        altered, altered_env = model(changed, 128)
        for side in (0, 1):
            self.assertEqual(prefix[side], baseline[side][:cutoff])
            self.assertEqual(altered[side][:cutoff], baseline[side][:cutoff])
        for variable in ("A", "B", "D", "E", "G", "V"):
            name = variable + OLD.base36(128)
            self.assertEqual(prefix_env[name], baseline_env[name][:cutoff])
            self.assertEqual(altered_env[name][:cutoff], baseline_env[name][:cutoff])


def main():
    tests = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompactTests))
    if not tests.wasSuccessful():
        return 1
    real_checks, depth_counts = [], []
    for code in INPUTS.CODES:
        rows, binding, hashes = INPUTS.load(code)
        invalid = [i for i, r in enumerate(rows) if any(not r[k].is_finite() for k in ("close", "high", "low")) or r["high"] < r["low"]]
        assert not invalid, "Finite valid native OHLC is required by the interval-image proof"
        inputs = [{"C": r["close"], "H": r["high"], "L": r["low"]} for r in rows]
        original = OLD.expanded_enclosures(inputs, 128)
        compact = compact_enclosures(inputs, 128)
        assert original == compact, "Changed interval bounds or possible direction sets"
        truth = OLD.recursive_reference(inputs)
        window = [i for i, r in enumerate(rows) if INPUTS.START <= r["date"] <= INPUTS.END and i >= 10]
        exact = [g != v for g, v in zip(compact[4], compact[5])]
        unknown = [i for i in window if not exact[i] or not exact[i - 1]]
        assert not unknown
        assert all(compact[0][i] <= truth[0][i] <= compact[1][i] and compact[2][i] <= truth[1][i] <= compact[3][i] and compact[4][i] == truth[2][i] for i in window)
        if code == "688322":
            formula_rows = [COMMON.record(date.fromisoformat(r["date"]), r["close"], r["high"], r["low"]) for r in rows]
            actual, env = model(formula_rows, 128)
            suffix = OLD.base36(128)
            for name, state in zip(("A", "B", "D", "E", "G", "V"), compact):
                assert env[name + suffix] == state
            for i in window:
                assert actual[0][i] == (truth[2][i] and not truth[2][i - 1])
                assert actual[1][i] == (not truth[2][i] and truth[2][i - 1])
        real_checks.append({"code": code, "rows": len(rows), "native_binding": binding,
                            "old128_compact128_all_six_state_arrays_exactly_equal": True,
                            "model_UNKNOWN": 0, "model_BADINPUT": len(invalid),
                            "all_certified_requested_directions_equal_full_recursion": True,
                            "actual_saved_source_interpreted_on_all_rows": code == "688322",
                            "source_hashes_unchanged": INPUTS.unchanged(hashes)})
        counts = {"code": code}
        for depth in (64, 80, 81, 82):
            state = compact_enclosures(inputs, depth)
            certain = [g != v for g, v in zip(state[4], state[5])]
            counts[str(depth)] = sum(not certain[i] or not certain[i - 1] for i in window)
        depth_counts.append(counts)
        print(json.dumps(real_checks[-1]), flush=True)
    entries = []
    for name, text in zip(NAMES, sources(128)):
        blob = text.encode("ascii")
        path = FORMULAS / name
        if path.exists():
            assert path.read_bytes() == blob, "Immutable v3 source would change"
        else:
            path.write_bytes(blob)
        entries.append({"path": path.relative_to(BASE).as_posix(), "sha256": sha256(blob).hexdigest(),
                        "bytes": len(blob), "definitions": len(re.findall(r"(?:^|;)\s*([A-Z][A-Z0-9_]*)\s*(?::=|:)", re.sub(r"\{.*?\}", "", text, flags=re.S)))})
    report = {"kind": "independent_exact_interval_image_source_reduction_not_native_certification", "version": 3,
              "status": "model_proofs_pass_native_compile_and_runtime_diagnostic_still_required",
              "depth": 128, "crossformula_references": 0, "synthetic_proof_tests": tests.testsRun,
              "change": "Equivalent interval-image formulas and separate mayUp/mayDown booleans; no strategy, ATR, initialization, history or execution rule changes",
              "sources": entries, "real_native_input_checks": real_checks,
              "insufficient_depth_model_UNKNOWN_counts": depth_counts,
              "native_UI_UNKNOWN": None, "native_UI_BADINPUT": None,
              "native_runtime_arithmetic_identity": "Not asserted: native operators/initialization still require direct diagnostic and output comparison",
              "generator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
              "math_predecessor_sha256": sha256((BASE / "prepare_s06_interval_formula.py").read_bytes()).hexdigest(),
              "native_UI_operated_by_this_script": False, "returns_calculated": False}
    OUT.mkdir(exist_ok=True)
    (OUT / "S06-standalone-v3-source-proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sources": entries, "proof_tests": tests.testsRun, "native_UI_status": "not_tested", "returns_calculated": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
