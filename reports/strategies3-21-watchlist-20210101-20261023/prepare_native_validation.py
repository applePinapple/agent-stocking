"""Promote the explicitly accepted local variant, preserving its draft snapshot."""
from hashlib import sha256
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SNAPSHOT = BASE / "single-stock-next-open-draft"
OUT = BASE / "native-evaluation"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    old_path = SNAPSHOT / "B321DRAF-expert.txt"
    text = old_path.read_text(encoding="ascii")
    text = text.replace("PENDING DRAFT ONLY. NOT APPROVED STRATEGIES OR VERIFIED NATIVE OUTPUT.", "CONFIRMED LOCAL RULES; NATIVE COMPILE AND OUTPUT CHECKS STILL PENDING.")
    text = text.replace("Formula proposal B321DRAF", "Formula B321NAT")
    text = text.replace("Source departures and initialization proposals are listed in README.md.", "Confirmed source departures are listed in confirmed-rules.md.")
    text = text.replace("proposal", "selected_rule").replace("proposed", "selected")
    text = text.replace("native numerical identity remains unverified", "native output checks remain pending")
    text = text.replace("RSI_EMA_initialization", "native_initialization")
    formula_path = OUT / "B321NAT-expert.txt"
    formula_path.write_text(text, encoding="ascii")
    old = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    manifest = dict(old)
    manifest.update({
        "kind": "confirmed_tongdaxin_local_single_stock_long_only_next_open",
        "status": "confirmed_for_native_validation",
        "formula_name": "B321NAT",
        "formula_name_proposal": None,
        "confirmation_basis": "Parent task relays explicit user confirmation to use the Tongdaxin local variant and permission to complete local history. This records confirmation of the presented simple-variant settings, not original cross-platform identity.",
        "cross_platform_identity_required": False,
        "native_ui_operated_by_this_agent": False,
        "native_compile_tested": False,
        "native_signal_identity_tested": False,
        "native_backtests_run": 0,
        "returns_generated": False,
        "source_document_status_unchanged": True,
        "draft_snapshot": {"directory": SNAPSHOT.name, "formula_sha256": digest(old_path), "manifest_sha256": digest(SNAPSHOT / "manifest.json")},
        "formula_sha256": digest(formula_path),
        "formula_ascii_bytes": len(formula_path.read_bytes()),
        "formula_lf_normalized_bytes": len(formula_path.read_bytes().replace(b"\r\n", b"\n")),
        "formula_lf_normalized_sha256": sha256(formula_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "promotion_generator_sha256": digest(Path(__file__)),
        "source_rule_differences_path": "confirmed-rules.md",
        "official_semantics_path": "official-semantics.md",
        "signal_only_validation_path": "signal-causality-evidence.json",
        "project_test_log_path": "project-unittest.log",
        "not_a_formal_return_report_until_native_run_saved": True
    })
    exclusions = {
        6: "Supertrend recursive band/trend state algorithm is not included in the accepted simple formula set; no native run.",
        7: "SAR recursive initialization, reversals and AF conventions not included; no native run.",
        8: "Price-channel timing/channel execution variant not included in this accepted simple formula set; no native run.",
        11: "Two real tranches require staged order sizing and stage persistence; cannot substitute single all-cash entry.",
        13: "TPS requires 10/20/30/40 percent tranches in one account with prior-tranche price state; unsupported by this simple formula.",
        14: "Requires shared three-slot portfolio, cross-stock market-cap ranking, historical capitalization and turnover filters.",
        20: "Requires shared five-slot weekly ranking and explicit market-regime benchmark; cannot use independent stock accounts.",
        21: "TTM original Keltner and regression-momentum algorithm not included in the accepted native simple set."
    }
    manifest["strategies"] = []
    for row in old["strategies"]:
        n = row["sid"]
        new = dict(row)
        new["native_execution_authorized"] = n in old["supported_sid"]
        new["status"] = "confirmed_for_native_validation" if n in old["supported_sid"] else "N/A_not_run"
        new.pop("draft_status", None)
        new["confirmed_local_settings_and_source_departures"] = [s.replace("_proposal", "_selected") for s in new.pop("proposals_and_differences")]
        if n in exclusions:
            new["not_run_reason"] = exclusions[n]
            new["return_value"] = None
            new["include_in_return_ranking"] = False
        manifest["strategies"].append(new)
    settings = dict(manifest["execution_settings_to_review"])
    for key, value in settings.items():
        settings[key] = value.replace("_proposal", "_confirmed_local_variant") if isinstance(value, str) else value
    manifest["confirmed_settings_to_verify_in_native_report"] = settings
    manifest.pop("execution_settings_to_review", None)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "supported": len(manifest["supported_sid"]), "N_A": len(manifest["unsupported_sid"]), "formula_bytes": manifest["formula_ascii_bytes"], "native_compiled": False, "returns_generated": False}))


if __name__ == "__main__":
    main()
