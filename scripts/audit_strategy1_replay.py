"""Independently reconcile an existing replay's CSV ledgers; never run a strategy."""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

D = Decimal
CENT = D("0.01")


def money(value):
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def same(actual, expected, context):
    if D(str(actual)) != D(str(expected)):
        raise ValueError(f"{context}: actual={actual}, expected={expected}")


def audit(folder: Path):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    metrics, config = summary["metrics"], summary["config"]
    trades = rows(folder / "trades.csv")
    equity = rows(folder / "daily_equity.csv")
    dividends = rows(folder / "cash_dividends.csv")
    batches = rows(folder / "closed_batches.csv")
    quotes = {row["date"]: row for row in rows(Path(summary["provenance"]["input"]["path"]))}
    events = {item["event_id"]: item for item in summary["cash_dividend_events"]}
    cash, receivable, holdings = D(config["initial_cash"]), D(0), {}
    trades_by_day, dividend_by_day = {}, {}
    for index, trade in enumerate(trades, 1):
        same(trade["sequence"], index, "trade sequence")
        trades_by_day.setdefault(trade["date"], []).append(trade)
    for row in dividends:
        dividend_by_day.setdefault(row["processed_on"], []).append(row)
    registered, recognized, paid = {}, {}, {}
    peak, drawdown, max_exposure = cash, D(0), D(0)
    fees = D(0)
    for point in equity:
        selected = point["date"]
        quote = quotes[selected]
        phases = dividend_by_day.get(selected, [])
        for row in phases:
            if row["phase"] == "record_date_after_close":
                continue
            event, sequence = events[row["event_id"]], row["buy_sequence"]
            key = (event["event_id"], sequence)
            if key not in registered:
                raise ValueError(f"Unregistered dividend entitlement: {key}")
            same(row["amount"], registered[key], f"dividend amount {key}")
            if row["phase"] == "ex_date_receivable_before_open":
                if key in recognized or row["date"] != event["ex_date"]:
                    raise ValueError(f"Invalid dividend recognition: {key}")
                recognized[key] = D(row["amount"])
                receivable += D(row["amount"])
            elif row["phase"] == "cash_payment_before_open":
                if key not in recognized or key in paid or row["date"] != event["payment_date"]:
                    raise ValueError(f"Invalid dividend payment: {key}")
                paid[key] = D(row["amount"])
                cash += D(row["amount"])
                receivable -= D(row["amount"])
            else:
                raise ValueError(f"Unknown dividend phase: {row['phase']}")
            same(row["cash_after"], cash, f"dividend cash {key}")
            same(row["receivable_after"], receivable, f"dividend receivable {key}")
        for trade in trades_by_day.get(selected, []):
            quantity, price = int(trade["quantity"]), D(trade["price"])
            notional = money(price * quantity)
            commission = money(max(D(config["min_commission"]), notional * D(config["commission_rate"])))
            same(trade["notional"], notional, f"notional {trade['sequence']}")
            same(trade["commission"], commission, f"fee {trade['sequence']}")
            same(price, quote["open" if trade["session"] == "open" else "close"], f"fill price {trade['sequence']}")
            fees += commission
            if trade["side"] == "buy":
                if quantity < int(config["minimum_buy_quantity"]) or (quantity - int(config["minimum_buy_quantity"])) % int(config["quantity_step"]):
                    raise ValueError("Invalid purchase quantity")
                if notional + commission > D(config["buy_budget"]):
                    raise ValueError("Purchase exceeds fee-inclusive budget")
                next_notional = money(price * (quantity + int(config["quantity_step"])))
                next_fee = money(max(D(config["min_commission"]), next_notional * D(config["commission_rate"])))
                if next_notional + next_fee <= D(config["buy_budget"]):
                    raise ValueError("Purchase did not maximize permitted shares within budget")
                holdings[trade["sequence"]] = trade
                cash -= notional + commission
            elif trade["side"] == "sell":
                eligible = [key for key, lot in holdings.items() if lot["date"] < selected]
                same(quantity, sum(int(holdings[key]["quantity"]) for key in eligible), "T+1 eligible sale")
                for key in eligible:
                    del holdings[key]
                cash += notional - commission
            else:
                raise ValueError("Unknown trade side")
            same(trade["cash_after"], cash, f"fill cash {trade['sequence']}")
            same(trade["holdings_after"], sum(int(lot["quantity"]) for lot in holdings.values()), "fill holdings")
            if cash < 0:
                raise ValueError("Negative available cash")
        for event in events.values():
            if event["record_date"] != selected:
                continue
            relevant = [row for row in phases if row["event_id"] == event["event_id"] and row["phase"] == "record_date_after_close"]
            expected_qty = sum(int(lot["quantity"]) for lot in holdings.values())
            same(sum(int(row["quantity"]) for row in relevant), expected_qty, "dividend registered quantity")
            expected_total = money(D(event["cash_per_share"]) * expected_qty)
            same(sum((D(row["amount"]) for row in relevant), D(0)), expected_total, "dividend registered total")
            for row in relevant:
                key = (event["event_id"], row["buy_sequence"])
                zero_holding_row = not holdings and row["buy_sequence"] == "" and int(row["quantity"]) == 0 and D(row["amount"]) == 0
                if key in registered or (row["buy_sequence"] not in holdings and not zero_holding_row) or row["date"] != selected:
                    raise ValueError("Invalid dividend registration")
                if not zero_holding_row:
                    same(row["quantity"], holdings[row["buy_sequence"]]["quantity"], "dividend registered batch")
                same(row["cash_after"], cash, "registration cash")
                same(row["receivable_after"], receivable, "registration receivable")
                registered[key] = D(row["amount"])
        quantity = sum(int(lot["quantity"]) for lot in holdings.values())
        market_value = money(quantity * D(quote["close"]))
        net_assets = cash + market_value + receivable
        for field, expected in (("quantity", quantity), ("cash", cash), ("close", quote["close"]), ("market_value", market_value), ("dividend_receivable", receivable), ("equity", net_assets)):
            same(point[field], expected, f"daily {selected}/{field}")
        peak = max(peak, net_assets)
        drawdown = max(drawdown, (peak - net_assets) / peak)
        max_exposure = max(max_exposure, market_value / net_assets)
    by_sequence = {row["sequence"]: row for row in trades}
    sales = {}
    for batch in batches:
        buy, sell = by_sequence[batch["buy_sequence"]], by_sequence[batch["sell_sequence"]]
        same(batch["quantity"], buy["quantity"], "closed batch quantity")
        same(batch["entry_cost"], D(buy["notional"]) + D(buy["commission"]), "closed entry cost")
        same(batch["dividend_income"], sum((amount for (event, sequence), amount in recognized.items() if sequence == batch["buy_sequence"]), D(0)), "closed dividend attribution")
        same(batch["pnl"], D(batch["exit_notional"]) - D(batch["exit_commission"]) - D(batch["entry_cost"]) + D(batch["dividend_income"]), "closed pnl")
        if batch["exit_date"] != sell["date"] or batch["entry_date"] != buy["date"]:
            raise ValueError("Closed batch dates mismatch")
        sales.setdefault(batch["sell_sequence"], []).append(batch)
    for sequence, allocated in sales.items():
        sell = by_sequence[sequence]
        for field, trade_field in (("quantity", "quantity"), ("exit_notional", "notional"), ("exit_commission", "commission")):
            same(sum((D(row[field]) for row in allocated), D(0)), sell[trade_field], "sale allocations")
    realized = sum((D(row["pnl"]) for row in batches), D(0))
    open_cost = sum((D(lot["notional"]) + D(lot["commission"]) for lot in holdings.values()), D(0))
    open_income = sum((amount for (event, sequence), amount in recognized.items() if sequence in holdings), D(0))
    unrealized = market_value - open_cost + open_income
    checks = {"final_equity": net_assets, "ending_cash": cash, "ending_quantity": quantity, "ending_market_value": market_value,
              "ending_dividend_receivable": receivable, "dividend_income": sum(recognized.values(), D(0)),
              "dividend_cash_received": sum(paid.values(), D(0)), "total_fees": fees, "realized_pnl": realized,
              "unrealized_pnl": unrealized, "max_drawdown": drawdown,
              "total_return": net_assets / D(config["initial_cash"]) - 1}
    for field, expected in checks.items():
        same(metrics[field], expected, f"summary {field}")
    same(realized + unrealized, net_assets - D(config["initial_cash"]), "account pnl reconciliation")
    winners = [D(row["pnl"]) for row in batches if D(row["pnl"]) > 0]
    losers = [D(row["pnl"]) for row in batches if D(row["pnl"]) < 0]
    return {"status": "passed", "method": "Independent CSV arithmetic; no strategy replay or engine imports",
            "daily_rows": len(equity), "trade_rows": len(trades), "closed_batches": len(batches),
            "reconciled": checks,
            "additional_statistics": {"profit_factor": sum(winners) / -sum(losers) if losers else None,
                                      "average_win_loss_ratio": (sum(winners) / len(winners)) / (-sum(losers) / len(losers)) if winners and losers else None,
                                      "max_close_stock_weight": max_exposure},
            "input_file_hashes": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in
                                  ("summary.json", "trades.csv", "daily_equity.csv", "closed_batches.csv", "cash_dividends.csv")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report_dir)
    if args.output.exists():
        raise ValueError("Audit output already exists; refusing overwrite")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "daily_rows", "trade_rows", "closed_batches", "additional_statistics")}, default=str))


if __name__ == "__main__":
    main()
