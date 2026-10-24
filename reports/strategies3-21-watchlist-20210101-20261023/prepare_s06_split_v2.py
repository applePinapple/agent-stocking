"""Create immutable S06 64+64 v2 sources with collision-free helper outputs.

V1 helper output QM collides with the internal base36 Q22 variable QM.
V1 files remain untouched. V2 changes only the five output labels/references.
"""
import importlib.util
import json
from hashlib import sha256
from pathlib import Path
import re
import unittest

BASE = Path(__file__).resolve().parent
FORMULAS = BASE / "native-evaluation/per-strategy"
OUT = BASE / "native-evaluation/additional-6-8"
spec = importlib.util.spec_from_file_location("s06_v1_generator", BASE / "prepare_s06_interval_formula.py")
V1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V1)
RENAMES = {"UO": "OUTUO", "UI": "OUTUI", "LO": "OUTLO", "LI": "OUTLI", "QM": "OUTQM"}
FILES = ["S06-S6BASE-v2-indicator.txt", "S06-split-v2-expert.txt", "S06-split-v2-indicator-check.txt"]


def definitions(text):
    body = re.sub(r"\{.*?\}", "", text, flags=re.S)
    return re.findall(r"(?:^|;)\s*([A-Z][A-Z0-9_]*)\s*(?::=|:)", body)


def generate():
    originals = V1.split_formula_sources(128, 64)
    helper, expert, diagnostic = originals
    for old, new in RENAMES.items():
        # Assignment := is excluded: only the visible output label changes.
        helper, count = re.subn(r"(?<![A-Z0-9_])" + old + r":(?![=])", new + ":", helper)
        assert count == 1, (old, "not one visible helper output")
        old_ref, new_ref = '"S6BASE.' + old + '"', '"S6BASE.' + new + '"'
        assert expert.count(old_ref) == diagnostic.count(old_ref) == 1
        expert, diagnostic = expert.replace(old_ref, new_ref), diagnostic.replace(old_ref, new_ref)
    sources = (helper, expert, diagnostic)
    for text in sources:
        names = definitions(text)
        assert len(names) == len(set(names)), "Duplicate variable/output name in v2"
    helper_names = definitions(helper)
    assert all(helper_names.count(name) == 1 for name in RENAMES.values())
    # The internal name family and global names cannot collide with OUT*.
    internal = set(definitions(originals[0])) - set(RENAMES)
    assert not internal.intersection(RENAMES.values())
    # Inverting label-only edits recovers each old source exactly. This checks
    # the mathematical statements, order, operators and all 128 layers.
    restored = list(sources)
    for old, new in RENAMES.items():
        restored[0] = re.sub(r"(?<![A-Z0-9_])" + new + r":(?![=])", old + ":", restored[0])
        for i in (1, 2):
            restored[i] = restored[i].replace('"S6BASE.' + new + '"', '"S6BASE.' + old + '"')
    assert tuple(restored) == originals
    return sources


class SplitV2Tests(unittest.TestCase):
    def test_v1_actual_helper_collision_is_detected(self):
        names = definitions(V1.split_formula_sources(128, 64)[0])
        self.assertEqual(names.count("QM"), 2)

    def test_every_v2_symbol_is_unique_and_imports_match_exports(self):
        helper, expert, diagnostic = generate()
        self.assertEqual(set(re.findall(r"(?:^|;)\s*(OUT[A-Z]+):(?![=])", re.sub(r"\{.*?\}", "", helper, flags=re.S))), set(RENAMES.values()))
        for text in (helper, expert, diagnostic):
            names = definitions(text)
            self.assertEqual(len(names), len(set(names)))
        for old, new in RENAMES.items():
            self.assertIn(new + ":", helper)
            self.assertIn('"S6BASE.' + new + '"', expert)
            self.assertIn('"S6BASE.' + new + '"', diagnostic)
            self.assertNotIn('"S6BASE.' + old + '"', expert)

    def test_v2_sources_stay_within_existing_editor_byte_capacity(self):
        for text in generate():
            self.assertLess(len(text.encode("ascii")) + text.count("\n"), 32768)


def main():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SplitV2Tests))
    if not result.wasSuccessful():
        return 1
    entries = []
    for name, source in zip(FILES, generate()):
        path = FORMULAS / name
        blob = source.encode("ascii")
        if path.exists():
            assert path.read_bytes() == blob, "Immutable v2 source would be changed"
        else:
            path.write_bytes(blob)
        entries.append({"path": path.relative_to(BASE).as_posix(), "sha256": sha256(blob).hexdigest(),
                        "bytes": len(blob), "definitions": len(definitions(source)), "all_definitions_unique": True})
    report = {"kind": "S06_64_plus_64_source_label_repair_not_new_strategy_math", "version": 2,
              "status": "source_checks_pass_native_compilation_and_diagnostics_still_required", "tests_run": result.testsRun,
              "old_native_failure": "V1 helper visible output QM duplicates internal Q22(base36 M) variable QM; old files retained",
              "helper_native_indicator_name_required": "S6BASE", "helper_output_renames": RENAMES,
              "inverse_label_substitution_restores_old_sources_byte_for_byte": True,
              "depth_preserved": 128, "helper_depth": 64, "main_additional_depth": 64,
              "sources": entries, "generator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
              "math_generator_sha256": sha256((BASE / "prepare_s06_interval_formula.py").read_bytes()).hexdigest(),
              "native_UI_operated_by_this_script": False, "returns_calculated": False}
    OUT.mkdir(exist_ok=True)
    (OUT / "S06-split-v2-source-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
