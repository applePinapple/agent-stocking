"""Read-only synthetic configuration type review; no market replay or returns."""
import importlib.util
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("portfolio_decimal_review", BASE / "portfolio_native_runner.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
cfg = json.loads((BASE / "portfolio14-20/native-runner/S20-rules-pending.json").read_text(encoding="utf-8"))
v = cfg.pop("proposed_values_for_review_only")
v.update(lot_sizes={c: 100 for c in R.EXPECTED}, minimum_buy_fee=1.005, minimum_sell_fee="0",
         settlement_market_sessions=1, insufficient_cash="resize", failed_order_policy="cancel",
         corporate_action_policy="explicitly_accepted_adjusted_price_model", fee_rounding="cent_half_up",
         terminal_respects_settlement=False, benchmark_indicator_mode="mathematical_mean")
cfg.update(status="confirmed", formal_returns_permitted=True, confirmed_values=v,
           confirmation={"supplementary_modeled_backtest": True, "S20_local_portfolio_rules": True},
           approval_evidence={"path": "SYNTHETIC_mock_approval_never_authorizes_market_replay", "sha256": R.digest(b"{}")})
with patch.object(R, "read_bound", return_value=b"{}"), patch.object(R, "approved_native_scope", return_value={}):
    try:
        execution, _, _ = R.validate_configuration(BASE, cfg, BASE / "synthetic-review-config.json", 20, [])
    except ValueError as exc:
        print(json.dumps({"synthetic_configuration_rejected": True, "reason": str(exc), "market_replay": False}))
    else:
        converted = execution.minimum_buy_fee
        print(json.dumps({"synthetic_configuration_rejected": False,
                          "input_JSON_number": "1.005", "converted_Decimal": str(converted),
                          "round_half_up_actual": str(converted.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                          "round_half_up_decimal_string": str(Decimal('1.005').quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                          "market_replay": False}))
