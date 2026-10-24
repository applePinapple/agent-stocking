"""Extract accepted per-SID dependencies; emit sources only after equivalence.

All validation quotes are SYNTHETIC. No UI, market returns or orders are used.
"""
import ast
from collections import Counter
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import time

BASE = Path(__file__).resolve().parent
NATIVE = BASE / "native-evaluation"
OUT = NATIVE / "per-strategy"
SOURCE = NATIVE / "B321NAT-expert.txt"
SID_LIST = [3, 4, 5, 9, 10, 12, 15, 16, 17, 18, 19]
BASE_HASH = "6ae2099f90e671da420a16c0532f37c2aa33c47b85885570c84f7ce6f8b0c9ef"


def digest(blob):
    return sha256(blob).hexdigest()


def load_tester():
    path = BASE / "validate_native_signal_causality.py"
    spec = importlib.util.spec_from_file_location("native_signal_test_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def statement_map(text):
    code = re.sub(r"\{.*?\}", "", text, flags=re.S)
    pairs = []
    for part in code.split(";"):
        if not part.strip():
            continue
        name, _ = re.split(r":=|:", part.strip(), maxsplit=1)
        pairs.append((name, part.strip() + ";"))
    return pairs


def main():
    started = time.perf_counter()
    blob = SOURCE.read_bytes()
    assert digest(blob) == BASE_HASH, "Universal source changed: re-review before specialization."
    text = blob.decode("ascii")
    tester = load_tester()
    universal = tester.FormulaModel(text)
    expressions = dict(universal.common)
    statements = statement_map(text)
    order = [name for name, _ in statements]
    definitions = dict(statements)

    candidates = {}
    for sid in SID_LIST:
        needed = set()

        def visit(name):
            if name in needed:
                return
            assert name in expressions
            needed.add(name)
            for node in ast.walk(expressions[name]):
                if isinstance(node, ast.Name) and node.id in expressions:
                    visit(node.id)

        for root in ["DLY", "SPAN", f"B{sid}", f"S{sid}"]:
            visit(root)
        kept = [name for name in order if name in needed]
        body = "\n".join(definitions[name] for name in kept)
        specialized = (
            f"{{CONFIRMED LOCAL RULES; SPECIALIZED SID={sid}; NATIVE COMPILE PENDING.}}\n"
            f"{{BASE B321NAT SHA256 {BASE_HASH}}}\n"
            f"{{UI PARAMETER SID MIN=3 MAX=21 DEFAULT={sid}; ONLY THIS SID CAN SIGNAL.}}\n"
            "{Only unused dependencies removed. Fills remain next-period-open in evaluator.}\n"
            + body + "\n"
            + f"ENTERLONG:DLY AND SPAN AND SID={sid} AND B{sid} AND NOT(S{sid});\n"
            + f"EXITLONG:DLY AND SPAN AND SID={sid} AND S{sid};\n"
        )
        function_counts = Counter(node.func.id for name in kept for node in ast.walk(expressions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name))
        candidates[sid] = {"text": specialized, "variables": kept, "function_counts": dict(sorted(function_counts.items()))}

    rows = tester.synthetic_rows(1600)
    fixture_blob = json.dumps({"kind": "SYNTHETIC_TEST_DATA_NOT_MARKET_QUOTES", "rows": rows}, separators=(",", ":")).encode("utf-8")
    cut_points = [199, 200, 260, 500, 900, 1250]
    comparisons = []
    wrong_sid_checks = 0
    synthetic_signal_counts = {}
    for origin in [0, 1]:
        full_model = tester.FormulaModel(text, origin)
        full_common = full_model.common_values(rows)
        baseline = {sid: full_model.selected(full_common, sid) for sid in SID_LIST}
        small_models = {sid: tester.FormulaModel(candidates[sid]["text"], origin) for sid in SID_LIST}
        for sid in SID_LIST:
            model = small_models[sid]
            common = model.common_values(rows)
            actual = model.selected(common, sid)
            assert actual == baseline[sid], (sid, origin, "full_equivalence")
            assert any(actual[0]), (sid, origin, "empty_synthetic_entry_fixture")
            comparisons.append({"sid": sid, "barscount_origin": origin, "kind": "full1600_same_sid", "result": "pass"})
            if origin == 1:
                synthetic_signal_counts[str(sid)] = {"synthetic_only_buy_count": sum(actual[0]), "synthetic_only_sell_count": sum(actual[1])}
            for wrong in [n for n in range(3, 22) if n != sid] + [2, 22, 3.5]:
                assert not any(any(values) for values in model.selected(common, wrong)), (sid, wrong)
                wrong_sid_checks += 1

        for cut in cut_points:
            changed = [dict(row) for row in rows]
            for i in range(cut, len(changed)):
                changed[i]["C"] = 5000 + i * 71 + 200 * __import__("math").sin(i)
                changed[i]["H"] = changed[i]["C"] + 800
                changed[i]["L"] = changed[i]["C"] - 700
            universal_changed = full_model.common_values(changed)
            universal_prefix = full_model.common_values(rows[:cut])
            for sid in SID_LIST:
                model = small_models[sid]
                small_changed = model.common_values(changed)
                actual_changed = model.selected(small_changed, sid)
                assert actual_changed == full_model.selected(universal_changed, sid), (sid, origin, cut, "changed_full_equivalence")
                original_prefix = tuple(series[:cut] for series in baseline[sid])
                assert tuple(series[:cut] for series in actual_changed) == original_prefix, (sid, origin, cut, "future_perturbation")
                small_prefix = model.common_values(rows[:cut])
                actual_prefix = model.selected(small_prefix, sid)
                assert actual_prefix == full_model.selected(universal_prefix, sid), (sid, origin, cut, "truncated_equivalence")
                assert actual_prefix == original_prefix, (sid, origin, cut, "future_removal")
                comparisons.append({"sid": sid, "barscount_origin": origin, "prefix_bars": cut, "kind": "future_perturbation_and_truncation", "specialized_equals_universal_on_changed_full_and_prefix": "pass", "past_outputs_unchanged_after_change_and_removal": "pass"})

    # Do not expose runnable sources before every candidate has passed.
    assert digest(SOURCE.read_bytes()) == BASE_HASH, "Universal source changed during validation."
    OUT.mkdir(exist_ok=True)
    files = []
    for sid, candidate in candidates.items():
        path = OUT / f"S{sid:02}-expert.txt"
        path.write_text(candidate["text"], encoding="ascii")
        emitted = path.read_bytes()
        files.append({"sid": sid, "path": path.name, "sha256": digest(emitted), "physical_bytes": len(emitted), "lf_normalized_bytes": len(emitted.replace(b"\r\n", b"\n")), "lf_normalized_sha256": digest(emitted.replace(b"\r\n", b"\n")), "universal_source_sha256": BASE_HASH, "parameter": {"name": "SID", "minimum": 3, "maximum": 21, "default": sid}, "guard": f"SID={sid} required for both outputs", "kept_variable_count": len(candidate["variables"]), "kept_variables_in_original_order": candidate["variables"], "called_functions": candidate["function_counts"], "native_compile_tested": False, "native_execution_source_hash_must_be_recorded_per_run": True, "logical_equality_model_status": "pass"})

    evidence = {
        "status": "pass",
        "kind": "SYNTHETIC_SIGNAL_ONLY_EQUIVALENCE_AND_CAUSALITY_NOT_MARKET_BACKTEST",
        "source_sha256": BASE_HASH,
        "synthetic_rows": 1600,
        "synthetic_fixture_sha256": digest(fixture_blob),
        "barscount_origins": [0, 1],
        "full1600_sid_equivalence_cases": 22,
        "future_change_and_truncation_cases": 132,
        "changed_input_and_truncated_input_universal_equivalence_comparisons": 264,
        "past_prefix_causality_comparisons": 264,
        "wrong_sid_zero_signal_checks": wrong_sid_checks,
        "cuts": cut_points,
        "cases": comparisons,
        "synthetic_signal_counts_NOT_market_results": synthetic_signal_counts,
        "native_UI_operated": False,
        "orders_or_returns_computed": False,
        "limitations": "The same explicit causal primitive test model as prior tests is reused. Direct expression-source dependency extraction plus synthetic model equality does not replace native compile/sample checks. No claim of speed improvement until actual UI observation.",
    }
    evidence_path = OUT / "equivalence-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"status": "confirmed_for_native_compile_validation", "kind": "dependency_only_specialization_no_rule_or_parameter_change", "source_path": "../B321NAT-expert.txt", "source_sha256": BASE_HASH, "source_physical_bytes": len(blob), "generator_sha256": digest(Path(__file__).read_bytes()), "tester_path": "../../validate_native_signal_causality.py", "tester_sha256": digest((BASE / "validate_native_signal_causality.py").read_bytes()), "evidence_path": evidence_path.name, "evidence_sha256": digest(evidence_path.read_bytes()), "file_count": len(files), "files": files, "unsupported_sid": [6, 7, 8, 11, 13, 14, 20, 21], "unsupported_policy": "N/A_not_run_no_return_ranking", "runtime_attribution": "Keep formula name B321NAT if the UI requires; every actual run must record the active SXX source path and physical SHA256. Formula name alone no longer identifies source.", "native_compilation_and_run": "root_UI_owner_only; test_formula_after_each_source_replacement; runtime_SID_must_equal_source_guard", "speed_claim": "not_tested; only irrelevant expressions removed", "elapsed_seconds": round(time.perf_counter() - started, 3)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "files": [{"sid": f["sid"], "bytes": f["physical_bytes"], "variables": f["kept_variable_count"], "sha256": f["sha256"]} for f in files], "full_equivalence_cases": 22, "future_and_truncation_cases": 132, "wrong_sid_checks": wrong_sid_checks, "elapsed_seconds": manifest["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
