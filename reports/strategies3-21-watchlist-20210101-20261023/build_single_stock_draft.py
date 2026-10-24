"""Build pending TDX signal drafts. Never reads quotations or produces returns."""

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent.parent
OUT = BASE / "single-stock-next-open-draft"
SUPPORTED = [3, 4, 5, 9, 10, 12, 15, 16, 17, 18, 19]
EXCLUDED = [n for n in range(3, 22) if n not in SUPPORTED]


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def date_expr(prefix, dates):
    groups = defaultdict(list)
    for value in dates:
        groups[(value + 19000000) // 10000].append(value)
    lines, names = [], []
    for year, values in sorted(groups.items()):
        ident = f"{prefix}{year % 100}"
        names.append(ident)
        terms = [f"DATE={v}" for v in sorted(set(values))]
        chunks = [" OR ".join(terms[i:i + 4]) for i in range(0, len(terms), 4)]
        lines.append(ident + ":=(" + " OR\n".join(chunks) + ");")
    lines.append(prefix + ":=(" + " OR ".join(names) + ");")
    return "\n".join(lines)


def build():
    OUT.mkdir(exist_ok=True)
    calendar_path = BASE / "calendar.json"
    s19_path = BASE / "strategy19-calendar.json"
    calendar = json.loads(calendar_path.read_text(encoding="utf-8-sig"))
    s19 = json.loads(s19_path.read_text(encoding="utf-8-sig"))
    month_ends = [r["tdx_date"] for r in calendar["month_ends"]]
    buys19 = s19["active_buy_tdx_dates"]
    sells19 = s19["active_sell_tdx_dates"]
    assert len(month_ends) == 69
    assert len(buys19) == len(sells19) == 69
    assert buys19[0] == 1210125 and sells19[0] == 1210203
    assert 1261026 not in buys19 and 1261104 not in sells19

    formula = """{PENDING DRAFT ONLY. NOT APPROVED STRATEGIES OR VERIFIED NATIVE OUTPUT.}
{Formula proposal B321DRAF. UI parameter SID min=3 max=21 default=3.}
{Set LONG ENTRY AND LONG EXIT fills to NEXT PERIOD OPEN in evaluator.}
{Do not REF the final signals: that would add another bar of delay.}
{Unsupported SID 6/7/8/11/13/14/20/21 produces no signals and is N/A.}
{Source departures and initialization proposals are listed in README.md.}
"""
    formula += date_expr("MEND", month_ends) + "\n\n"
    formula += date_expr("D19B", buys19) + "\n\n"
    formula += date_expr("D19S", sells19) + "\n\n"
    formula += """{Common indicators: native numerical identity remains unverified.}
DLY:=PERIOD=5;
SPAN:=DATE>=1210101 AND DATE<=1261023;
R200:=BARSCOUNT(C)>=200;
R26:=BARSCOUNT(C)>=26;
R25:=BARSCOUNT(C)>=25;
R20:=BARSCOUNT(C)>=20;
R7:=BARSCOUNT(C)>=7;
R4:=BARSCOUNT(C)>=4;
R3:=BARSCOUNT(C)>=3;
R2:=BARSCOUNT(C)>=2;
MA200:=MA(C,200);
MA5:=MA(C,5);
E200:=EMA(C,200);
MDIF:=EMA(C,12)-EMA(C,26);
MDEA:=EMA(MDIF,9);
MHIST:=MDIF-MDEA;
TYP:=(H+L+C)/3;
CTMA:=MA(TYP,20);
CDEV:=AVEDEV(TYP,20);
CCIV:=(TYP-CTMA)/(0.015*IF(CDEV>0,CDEV,1));
CCIOK:=R20 AND CDEV>0;
DELTA:=C-REF(C,1);
RUP:=SMA(MAX(DELTA,0),2,1);
RDN:=SMA(ABS(DELTA),2,1);
RSIV:=IF(RDN>0,100*RUP/IF(RDN>0,RDN,1),50);
RSIOK:=R3 AND RDN>0;
IBDEN:=H-L;
IBSV:=(C-L)/IF(IBDEN>0,IBDEN,1);
IBOK:=IBDEN>0;
LOWB:=HHV(H,10)-2.5*MA(H-L,25);
WRHI:=HHV(H,2);
WRLO:=LLV(L,2);
WRDEN:=WRHI-WRLO;
WRV:=-100*(WRHI-C)/IF(WRDEN>0,WRDEN,1);
WROK:=R2 AND WRDEN>0;
WD:=WEEKDAY;
DAYNO:=DATETODAY(DATE);
MONOK:=R3 AND WD=1 AND REF(WD,1)=5 AND REF(WD,2)=4
AND DAYNO-REF(DAYNO,1)=3 AND REF(DAYNO,1)-REF(DAYNO,2)=1;

{SID3: monthly state, equality means neither action.}
B3:=R200 AND MEND AND C>MA200;
S3:=R200 AND MEND AND C<MA200;
{SID4: strict adjacent negative-to-positive / positive-to-negative.}
{200 bars is only the proposed EMA warmup, never an MA trend filter.}
B4:=R200 AND MHIST>0 AND REF(MHIST,1)<0;
S4:=R200 AND MHIST<0 AND REF(MHIST,1)>0;
{SID5: CCI20 is a proposed parameter, not fixed by original rule.}
B5:=CCIOK AND REF(CCIOK,1) AND CCIV>100 AND REF(CCIV,1)<100;
S5:=CCIOK AND CCIV<100;
{SID9: preserve <=5, simple MA200 and MA5; exit is state proposal.}
B9:=R200 AND RSIOK AND C>MA200 AND RSIV<=5;
S9:=R200 AND C>MA5;
{SID10: seven inclusive closes, ties allowed.}
B10:=R200 AND C>MA200 AND C=LLV(C,7);
S10:=R200 AND C=HHV(C,7);
{SID12: EMA200, four strictly decreasing RSI values starting below60.}
R4OK:=RSIOK AND REF(RSIOK,1) AND REF(RSIOK,2) AND REF(RSIOK,3);
B12:=R200 AND R4OK AND C>E200 AND REF(RSIV,3)<60
AND REF(RSIV,3)>REF(RSIV,2) AND REF(RSIV,2)>REF(RSIV,1)
AND REF(RSIV,1)>RSIV AND RSIV<10;
{Crossing equality proposal: prior <=70, current >70.}
S12:=R200 AND RSIOK AND REF(RSIOK,1) AND RSIV>70 AND REF(RSIV,1)<=70;
{SID15: three comparisons require four bars. MA means simple here.}
B15:=R200 AND C>MA200 AND C<MA5
AND H<REF(H,1) AND REF(H,1)<REF(H,2) AND REF(H,2)<REF(H,3)
AND L<REF(L,1) AND REF(L,1)<REF(L,2) AND REF(L,2)<REF(L,3);
S15:=R200 AND C>MA5;
{SID16: inclusive 25/10 windows are proposals. No MA200 filter.}
B16:=R25 AND IBOK AND C<LOWB AND IBSV<0.3;
S16:=R2 AND C>REF(H,1);
{SID17: negative Williams R, never native positive WR shorthand.}
B17:=WROK AND WRV<-90;
S17:=R2 AND (C>REF(H,1) OR (WROK AND WRV>-30));
{SID18: exact Monday/Fri/Thu dates; missing weekday skips entry.}
B18:=MONOK AND C<REF(C,1) AND REF(C,1)<REF(C,2);
S18:=R2 AND C>REF(H,1);
{SID19: static exchange-wide date inputs, never future quote counts.}
B19:=D19B;
S19:=D19S;

SUPPORTED:=SID=3 OR SID=4 OR SID=5 OR SID=9 OR SID=10 OR SID=12
OR SID=15 OR SID=16 OR SID=17 OR SID=18 OR SID=19;
RAWBUY:=(SID=3 AND B3) OR (SID=4 AND B4) OR (SID=5 AND B5)
OR (SID=9 AND B9) OR (SID=10 AND B10) OR (SID=12 AND B12)
OR (SID=15 AND B15) OR (SID=16 AND B16) OR (SID=17 AND B17)
OR (SID=18 AND B18) OR (SID=19 AND B19);
RAWSELL:=(SID=3 AND S3) OR (SID=4 AND S4) OR (SID=5 AND S5)
OR (SID=9 AND S9) OR (SID=10 AND S10) OR (SID=12 AND S12)
OR (SID=15 AND S15) OR (SID=16 AND S16) OR (SID=17 AND S17)
OR (SID=18 AND S18) OR (SID=19 AND S19);
{Conflict proposal: exit dominates, including when account is flat.}
{Position, cash, T+1 and failed orders remain evaluator responsibilities.}
ENTERLONG:DLY AND SPAN AND SUPPORTED AND RAWBUY AND NOT(RAWSELL);
EXITLONG:DLY AND SPAN AND SUPPORTED AND RAWSELL;
"""
    assert all(f"SID={n} AND B{n}" in formula for n in SUPPORTED)
    assert all(f"SID={n} AND B{n}" not in formula for n in EXCLUDED)
    assert "ENTERSHORT" not in formula and "EXITSHORT" not in formula
    for bad in ["REFX(", "BACKSET(", "ZIG(", "PEAK(", "TROUGH(", "XMA(", "REFDATE(", "CURRBARSCOUNT", "ISLASTBAR"]:
        assert bad not in formula, bad
    # Removing comments before checking prevents comments from being mistaken for code.
    code = re.sub(r"\{[^}]*\}", "", formula, flags=re.S)
    assert code.count("ENTERLONG:") == code.count("EXITLONG:") == 1
    assert all(19000000 + d <= 20261023 for d in month_ends + buys19 + sells19)
    (OUT / "B321DRAF-expert.txt").write_text(formula, encoding="ascii")

    details = {
        3: ["daily_close_to_next_open_matches_selected_source", "MA200_simple_proposal", "equal_MA_no_action"],
        4: ["long_only_cash_exit_replaces_original_short_reversal", "daily_timeframe_proposal", "EMA12_26_9_native_seed_unverified", "strict_sign_change_skips_zero", "200_bar_warmup_proposal"],
        5: ["CCI20_is_example_parameter_proposal", "TP_HLC3_and_0_015", "strict_prior_below100_entry_proposal", "zero_deviation_skips_CCI_signals", "next_open_timing_proposal"],
        9: ["RSI2_native_recursive_seed_unverified", "flat_RSI_skips_entry", "RSI_le5_preserved", "above_MA5_state_exit_proposal", "next_open_one_of_source_discussed_choices"],
        10: ["next_open_matches_selected_2016_source", "inclusive_7_closes_ties_preserved", "MA200_simple_proposal", "simultaneous_high_low_exit_priority_proposal"],
        12: ["source_same_close_changed_to_next_open", "EMA200_retained", "RSI2_native_recursive_seed_unverified", "prior_le70_crossing_proposal"],
        15: ["MA200_and_MA5_simple_proposal", "four_bars_three_comparisons_preserved", "next_open_timing_proposal"],
        16: ["source_same_close_entry_changed_to_next_open", "inclusive_25_10_window_proposal", "zero_range_skips_entry", "next_open_exit_proposal"],
        17: ["source_main_same_close_changed_to_next_open", "negative_Williams_R_preserved", "inclusive_2_window_proposal", "zero_range_skips_WR_branch", "prior_high_exit_still_valid_without_WR"],
        18: ["source_Monday_close_entry_changed_to_Tuesday_or_next_available_open", "exact_adjacent_Fri_Thu_calendar_gap", "missing_literal_weekday_skips_entry_proposal", "next_open_exit_proposal"],
        19: ["source_close_both_sides_changed_to_next_open", "China_exchange_calendar_adaptation", "static_69_buy_and_69_sell_signal_dates", "not_security_bar_ordinal", "2022_12_and_2023_12_sell_schedule_not_yet_published_at_buy"]
    }
    manifest = {
        "schema_version": 1,
        "kind": "pending_native_single_stock_long_only_next_open_signal_draft",
        "status": "pending_user_rule_confirmation_and_native_compile",
        "generated_date": "2026-10-24",
        "formula_name_proposal": "B321DRAF",
        "formula_type": "expert_system",
        "parameters": [{"name": "SID", "minimum": 3, "maximum": 21, "default": 3, "integer_required": True}],
        "evaluation_period_proposal": {"start": "2021-01-01", "end": "2026-10-23"},
        "native_ui_operated": False,
        "native_compile_tested": False,
        "native_signal_identity_tested": False,
        "native_backtests_run": 0,
        "returns_generated": False,
        "quotations_read": False,
        "supported_sid": SUPPORTED,
        "unsupported_sid": EXCLUDED,
        "unsupported_policy": "both outputs false; reporting status N/A; never display as zero return",
        "strategies": [{"sid": n, "draft_status": "pending" if n in SUPPORTED else "N/A", "native_execution_authorized": False, "proposals_and_differences": details.get(n, ["excluded_algorithm_or_staged_portfolio_not_confirmed"])} for n in range(3, 22)],
        "execution_settings_to_review": {
            "period": "daily_only_PERIOD5_semantics_need_native_check",
            "long_entry_price": "next_period_open",
            "long_exit_price": "next_period_open",
            "directions": "only_ENTERLONG_EXITLONG_outputs",
            "single_stock_accounts": "independent; never shared portfolio result",
            "capital_and_costs": "previous_approved_configuration_must_be_reread_from_native_report",
            "position": "all_available_cash_no_pyramiding_proposal_for_supported_single_entry_strategies",
            "repeated_signals": "must_verify_native_engine_ignores_entry_while_long_and_exit_while_flat",
            "same_bar_conflict": "exit_dominates_both_flat_and_long_proposal",
            "forced_end_close": "if_previous_approved_setting_used_report_separately_from_natural_exit",
            "limit_and_suspension": "previous_setting_does_not_prove_complete_execution_permission",
            "history": "load_maximum_available_prehistory; MA200_and_EMA200_use_BARSCOUNT_ge200; no_added_MA_filter_elsewhere"
        },
        "calendar_counts": {"month_end": len(month_ends), "strategy19_buy": len(buys19), "strategy19_sell": len(sells19)},
        "calendar_sell_schedule_unknown_at_buy_months": s19.get("sell_schedule_not_yet_published_at_buy_months", []),
        "checks": {
            "selector_contains_only_supported_sid": True,
            "unsupported_sid_has_no_selected_buy_or_sell_branch": True,
            "no_short_outputs": True,
            "no_listed_future_quote_functions": True,
            "zero_denominators_guarded_inside_division": True,
            "formula_is_ascii": True,
            "native_compile_and_meaning_not_proved_by_static_checks": True
        },
        "required_native_checks": ["SID_parameter_bounds_and_fractional_input", "PERIOD5_daily", "DATE_encoding", "WEEKDAY_and_DATETODAY_across_month_year_leap_day", "BARSCOUNT_0_vs1_start_and_full200_bar_guard", "RSI_EMA_seed_and_missing_values", "CCI_AVEDEV_and_zero_deviation", "MACD_zero_sign_boundary", "calendar_anchor_actual_dates", "same_day_signal_conflict", "no_pyramiding_and_rejected_orders", "future_quote_perturbation_preserves_past_signals_and_orders"],
        "input_hashes": [{"path": str(p.relative_to(PROJECT)).replace("\\", "/"), "sha256": file_hash(p)} for p in [calendar_path, s19_path] + sorted((PROJECT / "docs/strategies").glob("策略*.md"))],
        "generator_sha256": file_hash(Path(__file__)),
        "formula_sha256": file_hash(OUT / "B321DRAF-expert.txt"),
        "formula_ascii_bytes": len(formula.encode("ascii")),
        "runtime": {"python": __import__("sys").version},
        "official_function_reference": "https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html"
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "formula_bytes": manifest["formula_ascii_bytes"], "supported_sid": SUPPORTED, "N_A_sid": EXCLUDED, "calendar_counts": manifest["calendar_counts"], "returns_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    build()
