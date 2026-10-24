"""Exact-certified finite expansion of a causal local Supertrend recurrence.

No native self-reference, loops, DLLs or future functions. Each layer propagates
an enclosure of every possible prior upper/lower band and trend. Collapsed trend
states are exact; native diagnostic UNKNOWN must be zero on every requested
security before a formal native evaluation. Increase expansion depth otherwise.
This is a defined local SMA-TR10 / multiplier3 variant, not cTrader identity.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import unittest

BASE = Path(__file__).resolve().parent
OUT = BASE / "native-evaluation" / "additional-6-8"
FORMULAS = BASE / "native-evaluation" / "per-strategy"
SPEC = importlib.util.spec_from_file_location("s678_adapter_tests", BASE / "validate_s678_native_adapters.py")
ADAPTERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTERS)
COMMON = ADAPTERS.COMMON


def band_inputs(rows):
    tr, upper, lower = [], [], []
    for i, row in enumerate(rows):
        previous = rows[i - 1]["C"] if i else row["C"]
        tr.append(max(row["H"] - row["L"], abs(row["H"] - previous), abs(row["L"] - previous)))
        # Full ten-bar sum / 10; pre-window values are finite but not signal-eligible.
        atr = sum(tr[max(0, i - 9):i + 1]) / 10
        midpoint = (row["H"] + row["L"]) / 2
        upper.append(midpoint + 3 * atr)
        lower.append(midpoint - 3 * atr)
    return upper, lower


def recursive_reference(rows):
    upper, lower = band_inputs(rows)
    final_upper, final_lower, is_up = [], [], []
    for i, row in enumerate(rows):
        if i < 10:
            u, l, up = upper[i], lower[i], False
        else:
            previous_close = rows[i - 1]["C"]
            u = upper[i] if upper[i] < final_upper[-1] or previous_close > final_upper[-1] else final_upper[-1]
            l = lower[i] if lower[i] > final_lower[-1] or previous_close < final_lower[-1] else final_lower[-1]
            up = not row["C"] < l if is_up[-1] else row["C"] > u
        final_upper.append(u)
        final_lower.append(l)
        is_up.append(up)
    return final_upper, final_lower, is_up


def expanded_enclosures(rows, depth):
    upper, lower = band_inputs(rows)
    umin, umax, lmin, lmax, may_up, may_down = [], [], [], [], [], []
    for i in range(len(rows)):
        seeded = i < 10
        umin.append(upper[i] if seeded else min(upper[:i + 1]))
        umax.append(upper[i] if seeded else max(upper[:i + 1]))
        lmin.append(lower[i] if seeded else min(lower[:i + 1]))
        lmax.append(lower[i] if seeded else max(lower[:i + 1]))
        may_up.append(not seeded)
        may_down.append(True)
    for _ in range(depth):
        next_state = [[] for _ in range(6)]
        for i, row in enumerate(rows):
            if i < 10:
                state = upper[i], upper[i], lower[i], lower[i], False, True
            else:
                previous_close = rows[i - 1]["C"]
                a, b, x = umin[i - 1], umax[i - 1], upper[i]
                overlap = previous_close <= x and b >= previous_close and a <= x
                ulo = max(a, previous_close) if overlap else x
                uhi = (x if a < previous_close or b > x else min(b, x)) if overlap else x
                a, b, x = lmin[i - 1], lmax[i - 1], lower[i]
                overlap = x <= previous_close and b >= x and a <= previous_close
                llo = (x if a < x or b > previous_close else max(a, x)) if overlap else x
                lhi = min(b, previous_close) if overlap else x
                up = (may_down[i - 1] and row["C"] > ulo) or (may_up[i - 1] and row["C"] >= llo)
                down = (may_down[i - 1] and row["C"] <= uhi) or (may_up[i - 1] and row["C"] < lhi)
                state = ulo, uhi, llo, lhi, up, down
            for array, value in zip(next_state, state):
                array.append(value)
        umin, umax, lmin, lmax, may_up, may_down = next_state
    return umin, umax, lmin, lmax, may_up, may_down


def formula_source(depth):
    lines = [
        "{S06 LOCAL SUPERTREND: SMA TRUE RANGE 10; MULTIPLIER 3; SEED BEARISH.}",
        "{CLOSED DAILY BAR; LONG ONLY; NEXT-OPEN FILL IN EVALUATOR; SID=6.}",
        f"{{EXACT INTERVAL EXPANSION DEPTH {depth}; NOT AN APPROXIMATE ATR CHANNEL.}}",
        "{BEFORE FORMAL RUN: SAME-HISTORY DIAGNOSTIC UNKNOWN MUST BE ZERO FOR ALL 7.}",
        "DLY:=PERIOD=5;", "SPAN:=DATE>=1210101 AND DATE<=1261023;",
        "NC:=SUM(C=C,0);", "SEED:=NC<=10;", "PC:=REF(C,1);",
        "TRV:=IF(NC=1,H-L,MAX(H-L,MAX(ABS(H-PC),ABS(L-PC))));",
        "ATRV:=SUM(TRV,10)/10;", "BU:=(H+L)/2+3*ATRV;", "BL:=(H+L)/2-3*ATRV;",
        "UL00:=IF(SEED,BU,LLV(BU,0));", "UH00:=IF(SEED,BU,HHV(BU,0));",
        "LL00:=IF(SEED,BL,LLV(BL,0));", "LH00:=IF(SEED,BL,HHV(BL,0));",
        "QT00:=IF(SEED,2,3);",
    ]
    for layer in range(1, depth + 1):
        q, p = f"{layer:02d}", f"{layer - 1:02d}"
        lines.extend([
            f"OV{q}:=IF(PC<=BU AND REF(UH{p},1)>=PC AND REF(UL{p},1)<=BU,1,0)+2*IF(BL<=PC AND REF(LH{p},1)>=BL AND REF(LL{p},1)<=PC,1,0);",
            f"UL{q}:=IF(SEED,BU,IF(MOD(OV{q},2)>0,MAX(REF(UL{p},1),PC),BU));",
            f"UH{q}:=IF(SEED,BU,IF(MOD(OV{q},2)>0,IF(REF(UL{p},1)<PC OR REF(UH{p},1)>BU,BU,MIN(REF(UH{p},1),BU)),BU));",
            f"LL{q}:=IF(SEED,BL,IF(OV{q}>=2,IF(REF(LL{p},1)<BL OR REF(LH{p},1)>PC,BL,MAX(REF(LL{p},1),BL)),BL));",
            f"LH{q}:=IF(SEED,BL,IF(OV{q}>=2,MIN(REF(LH{p},1),PC),BL));",
            f"QT{q}:=IF(SEED,2,((REF(QT{p},1)>=2 AND C>UL{q}) OR (MOD(REF(QT{p},1),2)>0 AND C>=LL{q}))+2*((REF(QT{p},1)>=2 AND C<=UH{q}) OR (MOD(REF(QT{p},1),2)>0 AND C<LH{q})));",
        ])
    last = f"{depth:02d}"
    lines.extend([
        f"EXACT6:=QT{last}=1 OR QT{last}=2;",
        f"BUY6:=NC>10 AND EXACT6 AND REF(EXACT6,1) AND QT{last}=1 AND REF(QT{last},1)=2;",
        f"SELL6:=NC>10 AND EXACT6 AND REF(EXACT6,1) AND QT{last}=2 AND REF(QT{last},1)=1;",
    ])
    common = "\n".join(lines) + "\n"
    expert = common + "ENTERLONG:DLY AND SPAN AND SID=6 AND BUY6 AND NOT(SELL6);\nEXITLONG:DLY AND SPAN AND SID=6 AND SELL6;\n"
    diagnostic = common + ("{LATEST UNKNOWN MUST BE ZERO OVER THE EVALUATION SPAN.}\n"
        "UNKNOWN:SUM(SPAN AND NC>10 AND (NOT(EXACT6) OR NOT(REF(EXACT6,1))),0);\n"
        "BADINPUT:SUM(NOT(ISVALID(C)) OR NOT(ISVALID(H)) OR NOT(ISVALID(L)) OR H<L,0);\n"
        f"DIRECTION:IF(EXACT6,IF(QT{last}=1,1,-1),0);\n"
        f"UPPERLO:UL{last};\nUPPERHI:UH{last};\nLOWERLO:LL{last};\nLOWERHI:LH{last};\n")
    def compact(source):
        prefixes = {"UL":"A", "UH":"B", "LL":"D", "LH":"E", "QT":"Q", "OV":"J"}
        for old, new in {"SEED":"Z", "PC":"P", "BU":"X", "BL":"Y", "NC":"N", "TRV":"R", "ATRV":"T", "EXACT6":"K"}.items():
            source = re.sub(r"\b" + old + r"\b", new, source)
        source = re.sub(r"\b(UL|UH|LL|LH|QT|OV)([0-9]+)\b", lambda match: prefixes[match[1]] + base36(int(match[2])), source)
        source = re.sub(r"\{.*?\}", "", source, flags=re.S)
        source = source.replace(" AND ", "&&")
        statements = [statement.strip() + ";" for statement in source.split(";") if statement.strip()]
        names = [re.split(r":=|:", statement, maxsplit=1)[0] for statement in statements]
        assert len(names) == len(set(names)), "Duplicate native variable/output name"
        # Two statements per physical line leave room for Windows CRLF expansion.
        body = "\n".join("".join(statements[i:i + 2]) for i in range(0, len(statements), 2))
        return f"{{S06 DEPTH {depth}; DIAGNOSTIC UNKNOWN MUST BE ZERO; SEE ADDITIONAL README.}}\n" + body + "\n"
    return compact(expert), compact(diagnostic)


def base36(value):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if not value:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def split_formula_sources(depth, split=64):
    """Same computation partitioned across a visible native helper indicator."""
    assert 1 <= split < depth
    expert, diagnostic = formula_source(depth)
    strip = lambda source: re.sub(r"\{.*?\}", "", source, flags=re.S)
    statements = [item.strip() + ";" for item in strip(expert).split(";") if item.strip()]
    globals_set = {"DLY", "SPAN", "N", "Z", "P", "R", "T", "X", "Y"}
    global_statements, early_states, late_states, final_statements = [], [], [], []
    for statement in statements:
        name = re.split(r":=|:", statement, maxsplit=1)[0]
        if name in globals_set:
            global_statements.append(statement)
        elif re.fullmatch(r"[ABDEQJ][0-9A-Z]{1,2}", name):
            index = int(name[1:], 36)
            (early_states if index <= split else late_states).append(statement)
        else:
            final_statements.append(statement)
    suffix = base36(split)
    exports = {"UO": "A" + suffix, "UI": "B" + suffix, "LO": "D" + suffix,
               "LI": "E" + suffix, "QM": "Q" + suffix}
    aliases = [f'{internal}:="S6BASE.{exported}";' for exported, internal in exports.items()]

    def compose(comment, items):
        return "{" + comment + "}\n" + "\n".join("".join(items[i:i + 2]) for i in range(0, len(items), 2)) + "\n"

    helper = compose("S06 VISIBLE HELPER; REGISTER AS TECHNICAL INDICATOR S6BASE", global_statements + early_states + [f"{exported}:{internal};" for exported, internal in exports.items()])
    main_common = global_statements + aliases + late_states + final_statements[:-2]
    main = compose("S06 EXPERT; REQUIRES S6BASE HELPER WITH IDENTICAL HISTORY/PERIOD/FQ", main_common + final_statements[-2:])
    diagnostic_outputs = [statement.strip() + ";" for statement in strip(diagnostic).split(";") if statement.strip() and
                          re.split(r":=|:", statement.strip(), maxsplit=1)[0] in {"UNKNOWN", "BADINPUT", "DIRECTION", "UPPERLO", "UPPERHI", "LOWERLO", "LOWERHI"}]
    split_diagnostic = compose("S06 DIAGNOSTIC; REQUIRES S6BASE; UNKNOWN AND BADINPUT MUST BE ZERO", main_common + diagnostic_outputs)
    # Removing visible helper outputs and replacing imports by their definitions
    # exactly recovers the original source statements, not a second formula.
    assert global_statements + early_states + late_states + final_statements == statements
    return helper, main, split_diagnostic


def model_formula(rows, depth=32):
    expert, _ = formula_source(depth)
    class EnclosureFormulaModel(ADAPTERS.AdapterModel):
        def __init__(self, text, **options):
            super().__init__(text.replace("&&", " AND "), **options)

        def call(self, name, args):
            if name == "MOD":
                return COMMON.pointwise(lambda a, b: a % b, *args)
            if name in {"SUM", "HHV", "LLV"}:
                sequence, count = args
                if not isinstance(sequence, list):
                    sequence = [sequence] * self.n
                if name == "SUM":
                    return [sum(sequence[max(0, i - int(count) + 1):i + 1] if count else sequence[:i + 1]) for i in range(self.n)]
                if count == 0:
                    return [(max if name == "HHV" else min)(sequence[:i + 1]) for i in range(self.n)]
            return super().call(name, args)
    model = EnclosureFormulaModel(expert)
    env = model.common_values(rows)
    return model.selected(env, 6), env


class SupertrendIntervalTests(unittest.TestCase):
    def test_scalar_interval_images_include_all_boundary_candidates(self):
        for a in range(-2, 3):
            for b in range(a, 3):
                for p in range(-2, 3):
                    for x in range(-2, 3):
                        upper_overlap = p <= x and b >= p and a <= x
                        ulo = max(a, p) if upper_overlap else x
                        uhi = (x if a < p or b > x else min(b, x)) if upper_overlap else x
                        lower_overlap = x <= p and b >= x and a <= p
                        llo = (x if a < x or b > p else max(a, x)) if lower_overlap else x
                        lhi = min(b, p) if lower_overlap else x
                        for half in range(a * 2, b * 2 + 1):
                            candidate = half / 2
                            actual_upper = x if x < candidate or p > candidate else candidate
                            actual_lower = x if x > candidate or p < candidate else candidate
                            self.assertLessEqual(ulo, actual_upper)
                            self.assertGreaterEqual(uhi, actual_upper)
                            self.assertLessEqual(llo, actual_lower)
                            self.assertGreaterEqual(lhi, actual_lower)

    def test_128_layer_saved_expression_on_real_raw_688322(self):
        manifest = json.loads((BASE / "after-download" / "input-manifest.json").read_text(encoding="utf-8-sig"))
        item = next(item for item in manifest["securities"] if item["security_code"] == "688322")
        content = (BASE / "after-download" / item["snapshot_path"]).read_bytes()
        self.assertEqual(sha256(content).hexdigest(), item["snapshot_sha256"])
        rows = []
        for offset in range(0, len(content), 32):
            d, _o, h, l, c, *_ = struct.unpack_from("<IIIIIfII", content, offset)
            rows.append(COMMON.record(date(d // 10000, (d // 100) % 100, d % 100), c / 100, h / 100, l / 100))
        actual, env = model_formula(rows, 128)
        reference = recursive_reference(rows)[2]
        for i in range(10, len(rows)):
            if 1210101 <= rows[i]["DATE"] <= 1261023:
                self.assertTrue(env["K"][i] and env["K"][i - 1])
                self.assertEqual(actual[0][i], reference[i] and not reference[i - 1])
                self.assertEqual(actual[1][i], not reference[i] and reference[i - 1])

    def test_unique_names_and_split_preserves_exact_definitions(self):
        expert, _ = formula_source(128)
        names = re.findall(r"(?:^|;)\s*([A-Z0-9]+)\s*(?::=|:)", re.sub(r"\{.*?\}", "", expert, flags=re.S))
        self.assertEqual(len(names), len(set(names)))
        helper, main, diagnostic = split_formula_sources(128, 64)
        self.assertIn('A1S:="S6BASE.UO";', main)
        self.assertIn("UO:A1S;", helper)
        self.assertIn("BADINPUT:", diagnostic)
        self.assertLess(len(helper) + helper.count("\n"), 32768)
        self.assertLess(len(main) + main.count("\n"), 32768)

    def test_interval_encloses_true_recursive_states(self):
        rows = COMMON.synthetic_rows(500)
        truth = recursive_reference(rows)
        for depth in (1, 4, 16, 32):
            state = expanded_enclosures(rows, depth)
            for i in range(len(rows)):
                self.assertLessEqual(state[0][i], truth[0][i])
                self.assertGreaterEqual(state[1][i], truth[0][i])
                self.assertLessEqual(state[2][i], truth[1][i])
                self.assertGreaterEqual(state[3][i], truth[1][i])
                self.assertTrue(state[4 if truth[2][i] else 5][i])

    def test_saved_formula_matches_independent_enclosure_and_true_turns(self):
        rows = COMMON.synthetic_rows(700)
        truth = recursive_reference(rows)
        state = expanded_enclosures(rows, 64)
        result, env = model_formula(rows, 64)
        for key, values in zip(("A1S", "B1S", "D1S", "E1S"), state[:4]):
            self.assertEqual(env[key], values)
        self.assertEqual(env["Q1S"], [int(up) + 2 * int(down) for up, down in zip(state[4], state[5])])
        for i in range(1, len(rows)):
            exact = state[4][i] != state[5][i]
            previous_exact = state[4][i - 1] != state[5][i - 1]
            eligible = i >= 10 and 1210101 <= rows[i]["DATE"] <= 1261023 and exact and previous_exact
            self.assertEqual(result[0][i], eligible and truth[2][i] and not truth[2][i - 1])
            self.assertEqual(result[1][i], eligible and not truth[2][i] and truth[2][i - 1])
        self.assertTrue(any(result[0]) and any(result[1]))

    def test_ambiguous_states_are_exposed_not_invented(self):
        rows = [COMMON.record(date(2021, 1, 1), 100, 110, 90) for _ in range(200)]
        state = expanded_enclosures(rows, 16)
        # A steady sequence preserves an unknown historical direction after the
        # finite dependency horizon; the diagnostic must reveal it.
        self.assertTrue(any(up and down for up, down in zip(state[4][40:], state[5][40:])))

    def test_prefix_and_future_perturbations_preserve_all_history(self):
        rows = COMMON.synthetic_rows(700)
        baseline, _ = model_formula(rows, 128)
        self.assertTrue(any(baseline[0]) and any(baseline[1]))
        for cutoff in (10, 350, 500):
            prefix, _ = model_formula(rows[:cutoff], 128)
            changed = [dict(row) for row in rows]
            for i in range(cutoff, len(rows)):
                changed[i].update(C=2000 + i * 4, H=4000 + i * 4, L=1)
            modified, _ = model_formula(changed, 128)
            for branch in (0, 1):
                self.assertEqual(prefix[branch], baseline[branch][:cutoff])
                self.assertEqual(modified[branch][:cutoff], baseline[branch][:cutoff])


def raw_snapshot_diagnostics(depth):
    manifest_path = BASE / "after-download" / "input-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    output = []
    for item in manifest["securities"]:
        path = BASE / "after-download" / item["snapshot_path"]
        content = path.read_bytes()
        assert sha256(content).hexdigest() == item["snapshot_sha256"]
        rows = []
        for offset in range(0, len(content), 32):
            d, _o, h, l, c, *_ = struct.unpack_from("<IIIIIfII", content, offset)
            day = date(d // 10000, (d // 100) % 100, d % 100)
            rows.append(COMMON.record(day, c / 100, h / 100, l / 100))
        state = expanded_enclosures(rows, depth)
        truth = recursive_reference(rows)
        uncertain = [rows[i]["DATE"] for i in range(10, len(rows)) if rows[i]["DATE"] >= 1210101 and
                     (state[4][i] == state[5][i] or state[4][i - 1] == state[5][i - 1])]
        errors = sum(not (state[0][i] <= truth[0][i] <= state[1][i] and state[2][i] <= truth[1][i] <= state[3][i] and state[4 if truth[2][i] else 5][i]) for i in range(len(rows)))
        output.append({"code": item["security_code"], "unadjusted_input_sha256": sha256(content).hexdigest(),
                       "unresolved_direction_count": len(uncertain), "first_unresolved_dates": uncertain[:10],
                       "enclosure_reference_errors": errors,
                       "scope": "UNADJUSTED_LOCAL_QUOTE_SIGNAL_CHECK_ONLY_NOT_FRONT_ADJUSTED_NATIVE_EVALUATION_NOT_RETURNS"})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=128)
    args = parser.parse_args()
    assert 2 <= args.depth <= 512
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SupertrendIntervalTests))
    if not result.wasSuccessful():
        return 1
    expert, diagnostic = formula_source(args.depth)
    FORMULAS.mkdir(exist_ok=True)
    (FORMULAS / "S06-expert.txt").write_text(expert, encoding="ascii", newline="\n")
    (FORMULAS / "S06-indicator-check.txt").write_text(diagnostic, encoding="ascii", newline="\n")
    helper, split_expert, split_diagnostic = split_formula_sources(args.depth, args.depth // 2)
    for filename, source in (("S06-S6BASE-indicator.txt", helper), ("S06-split-expert.txt", split_expert), ("S06-split-indicator-check.txt", split_diagnostic)):
        (FORMULAS / filename).write_text(source, encoding="ascii", newline="\n")
    OUT.mkdir(exist_ok=True)
    evidence = {"status": "model_tests_pass_native_compile_and_front_adjusted_UNKNOWN_checks_required",
                "depth": args.depth, "model_tests_run": result.testsRun,
                "formula_sha256": sha256((FORMULAS / "S06-expert.txt").read_bytes()).hexdigest(),
                "formula_bytes": (FORMULAS / "S06-expert.txt").stat().st_size,
                "formula_assignment_output_count": len(re.findall(r"(?:^|;)\s*([A-Z0-9]+)\s*(?::=|:)", re.sub(r"\{.*?\}", "", expert, flags=re.S))),
                "diagnostic_sha256": sha256((FORMULAS / "S06-indicator-check.txt").read_bytes()).hexdigest(),
                "generator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "raw_quote_diagnostics": raw_snapshot_diagnostics(args.depth),
                "proof_scope": "Each interval encloses every possible recursive state. Exactly one possible trend equals the recursive model. A nonzero native UNKNOWN prohibits claiming a complete formal signal set.",
                "native_ui_operated": False, "native_compile_tested": False, "native_backtest_run": False,
                "returns_generated": False, "future_data_check": "prefix_and_perturbation_tests_pass; no end-of-series condition in expert outputs"}
    evidence["capacity_fallback_files"] = [{"path": "../per-strategy/" + filename, "sha256": sha256((FORMULAS / filename).read_bytes()).hexdigest(),
                                            "bytes": (FORMULAS / filename).stat().st_size}
                                           for filename in ("S06-S6BASE-indicator.txt", "S06-split-expert.txt", "S06-split-indicator-check.txt")]
    (OUT / "S06-interval-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"depth": args.depth, "bytes": evidence["formula_bytes"], "raw_quote_diagnostics": evidence["raw_quote_diagnostics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
