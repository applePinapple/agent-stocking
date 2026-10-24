"""Synthetic execution fixtures; no market profits are represented."""
from datetime import date
from dataclasses import replace
from decimal import Decimal as D
import unittest

from agent_stocking.portfolio import (
    BuyIntent, OpenQuote, PortfolioDecision, PortfolioExecutionConfig, PortfolioOrderScope, PortfolioReplayInterrupted, SharedCashAccount,
    run_shared_portfolio,
)

SESSIONS=(date(2026,1,5),date(2026,1,6),date(2026,1,7),date(2026,1,8))

def config(**changes):
    base=PortfolioExecutionConfig(D("1000"),3,{s:1 for s in "ABCDEFG"},D("0"),D("0"),D("0"),D("0"),D("0"),1,"resize","cancel","synthetic_prices","verified_none_required","synthetic")
    return replace(base,**changes)

def quote(price="10",buy=True,sell=True):
    return OpenQuote(D(price),buy,sell)

def decision(day, buys=(), sells=()):
    return PortfolioDecision(SESSIONS[day],tuple(sells),tuple(BuyIntent(s,D(b),"synthetic test") for s,b in buys),"synthetic test")

class SharedPortfolioTests(unittest.TestCase):
    def test_scoped_hook_receives_actual_resized_shares_and_exact_phase_price(self):
        account=SharedCashAccount(config(),SESSIONS)
        scopes=[]
        def permit(scope):scopes.append(scope);return True
        account.submit(decision(0,[("A","600"),("B","600")]))
        account.execute_open(SESSIONS[1],{"A":quote(buy=None),"B":quote(buy=None)},order_authorizer=permit)
        self.assertEqual([(s.symbol,s.quantity,s.quantity_unit) for s in scopes],[("A",60,"shares"),("B",40,"shares")])
        self.assertTrue(all(s.execution_date==SESSIONS[1] and s.phase=="open" and s.side=="buy" and s.price==D(10) and s.price_basis=="synthetic_prices" for s in scopes))
        with self.assertRaises(AttributeError):scopes[0].quantity=1
        self.assertEqual(account.cash,D(0))

    def test_scoped_unknown_does_not_expand_generic_true_or_partially_fill(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200"),("B","200")]))
        def only_exact_A(scope):
            return True if (scope.symbol,scope.quantity,scope.price,scope.phase)==("A",20,D(10),"open") else None
        with self.assertRaises(ValueError):
            account.execute_open(SESSIONS[1],{"A":quote(),"B":quote()},order_authorizer=only_exact_A)
        self.assertEqual((account.cash,account.positions,account.fills,account.orders),(D(1000),{},[],[]))
        self.assertEqual(len(account.pending_decisions),1)
        other=SharedCashAccount(config(),SESSIONS);other.submit(decision(0,[("A","300")]))
        with self.assertRaises(ValueError):other.execute_open(SESSIONS[1],{"A":quote()},order_authorizer=only_exact_A)
        self.assertEqual(other.fills,[])

    def test_scoped_price_includes_slippage_and_must_not_reuse_wrong_phase(self):
        account=SharedCashAccount(config(slippage_bps=D(100)),SESSIONS)
        account.submit(decision(0,[("A","202")]))
        seen=[]
        def exact(scope):
            seen.append(scope)
            return True if (scope.price,scope.quantity,scope.phase)==(D("10.10"),20,"open") else None
        account.execute_open(SESSIONS[1],{"A":quote(buy=None)},order_authorizer=exact)
        self.assertEqual(seen[0].price,D("10.10"))
        self.assertEqual(account.fills[0].price,seen[0].price)
        held=dict(account.positions)
        with self.assertRaises(ValueError):account.terminal_close(SESSIONS[1],{"A":quote(sell=None)},respect_settlement=False,order_authorizer=exact)
        self.assertEqual(account.positions,held)

    def test_scoped_hook_not_called_for_ignored_denied_settlement_or_capacity(self):
        account=SharedCashAccount(config(max_positions=1,settlement_market_sessions=2),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("A","200"),("B","200")],["C","A"]))
        calls=[]
        def forbidden(scope):calls.append(scope);raise AssertionError("Nonexecutable order requested scoped permission")
        account.execute_open(SESSIONS[2],{s:quote(buy=None,sell=None) for s in "ABC"},order_authorizer=forbidden)
        self.assertEqual(calls,[])
        empty=SharedCashAccount(config(),SESSIONS);empty.submit(decision(0,[("A","200")]))
        empty.execute_open(SESSIONS[1],{"A":quote(buy=False)},order_authorizer=forbidden)
        self.assertEqual(calls,[])
        self.assertEqual(empty.fills,[])

    def test_scoped_terminal_exact_holdings_and_unknown_are_atomic(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200"),("B","100")]))
        account.execute_open(SESSIONS[1],{"A":quote(),"B":quote()})
        before=(account.cash,dict(account.positions),list(account.fills),list(account.orders))
        scopes=[]
        def partial(scope):scopes.append(scope);return True if scope.symbol=="A" else None
        with self.assertRaises(ValueError):
            account.terminal_close(SESSIONS[1],{"A":quote(sell=None),"B":quote(sell=None)},respect_settlement=False,order_authorizer=partial)
        self.assertEqual((account.cash,account.positions,account.fills,account.orders),before)
        self.assertEqual([(s.symbol,s.quantity,s.phase,s.side) for s in scopes],[("A",20,"close","sell"),("B",10,"close","sell")])
        account.terminal_close(SESSIONS[1],{"A":quote(sell=None),"B":quote(sell=None)},respect_settlement=False,order_authorizer=lambda s:True)
        self.assertEqual(account.positions,{})

    def test_scoped_authorizer_non_boolean_result_fails_closed(self):
        account=SharedCashAccount(config(),SESSIONS);account.submit(decision(0,[("A","200")]))
        with self.assertRaises(ValueError):account.execute_open(SESSIONS[1],{"A":quote()},order_authorizer=lambda s:1)
        self.assertEqual(account.fills,[])

    def test_interrupted_scoped_replay_preserves_only_prior_committed_audit_state(self):
        from types import SimpleNamespace
        class Rules:
            approval_status="synthetic_test"
            def require_confirmed(self):pass
        class Strategy:
            rules=Rules();max_positions=3
            def decide(self,c):
                buys=[("A","200")] if c.session==SESSIONS[0] else [("B","200")] if c.session==SESSIONS[1] else []
                index=SESSIONS.index(c.session)
                return SimpleNamespace(decision=decision(index,buys))
        def context(day,nav,held):return SimpleNamespace(session=day,nav=nav,held_symbols=held,price_basis="synthetic_prices")
        def witness(scope):return True if scope.symbol=="A" and scope.quantity==20 else None
        with self.assertRaises(PortfolioReplayInterrupted) as caught:
            run_shared_portfolio(config(),SESSIONS,{d:{"A":quote(),"B":quote()} for d in SESSIONS},{d:{"A":D(10),"B":D(10)} for d in SESSIONS},Strategy(),context,source_validation_status="synthetic_test",terminal_policy="mark",terminal_quotes=None,terminal_respects_settlement=True,order_authorizer=witness)
        audit=caught.exception.portfolio_snapshot
        self.assertEqual(audit.cash,D(800))
        self.assertEqual([(f.symbol,f.quantity) for f in audit.fills],[("A",20)])
        self.assertEqual(len(audit.orders),1)
        self.assertEqual(len(audit.equity_curve),2)
        self.assertEqual([(s,p.quantity) for s,p in audit.positions],[("A",20)])
        self.assertEqual((audit.failed_session,audit.failed_phase),(SESSIONS[2],"open"))
        self.assertEqual(caught.exception.failing_order_scope.symbol,"B")
        self.assertEqual(len(audit.pending_decisions),1)
        self.assertIsNotNone(caught.exception.__cause__)

    def test_cash_is_shared_and_never_multiplied_per_security(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","600"),("B","600")]))
        account.execute_open(SESSIONS[1],{"A":quote(),"B":quote()})
        self.assertEqual(account.positions["A"].quantity,60)
        self.assertEqual(account.positions["B"].quantity,40)
        self.assertEqual(account.cash,D("0.00"))
        self.assertEqual(account.equity({"A":D(10),"B":D(10)}),D("1000.00"))

    def test_capacity_denies_fourth_name(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[(s,"100") for s in "ABCD"]))
        account.execute_open(SESSIONS[1],{s:quote() for s in "ABCD"})
        self.assertEqual(set(account.positions),set("ABC"))
        self.assertEqual(account.orders[-1].reason,"portfolio_position_limit")

    def test_denied_sale_does_not_free_slot_for_replacement(self):
        account=SharedCashAccount(config(max_positions=1),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("B","200")],["A"]))
        account.execute_open(SESSIONS[2],{"A":quote(sell=False),"B":quote()})
        self.assertEqual(set(account.positions),{"A"})
        self.assertEqual([r.reason for r in account.orders[-2:]], ["explicit_permission_denied","portfolio_position_limit"])

    def test_rotation_sells_first_and_rebuys_same_stock_with_both_fees(self):
        account=SharedCashAccount(config(buy_fee_rate=D("0.01"),sell_fee_rate=D("0.02")),SESSIONS)
        account.submit(decision(0,[("A","1000")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("A","1000")],["A"]))
        account.execute_open(SESSIONS[2],{"A":quote()})
        self.assertEqual([r.side for r in account.fills],["buy","sell","buy"])
        self.assertEqual(sum(r.fee for r in account.fills),D("39.30"))
        self.assertGreaterEqual(account.cash,D(0))

    def test_fee_inclusive_budget_and_minimum_fee(self):
        account=SharedCashAccount(config(minimum_buy_fee=D("5")),SESSIONS)
        account.submit(decision(0,[("A","100")]))
        account.execute_open(SESSIONS[1],{"A":quote("33")})
        self.assertEqual(account.positions["A"].quantity,2)
        self.assertEqual(account.fills[0].notional+account.fills[0].fee,D("71.00"))

    def test_unknown_permission_aborts_before_any_batch_fill(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200"),("B","200")]))
        with self.assertRaises(ValueError):
            account.execute_open(SESSIONS[1],{"A":quote(),"B":quote(buy=None)})
        self.assertEqual(account.cash,D(1000))
        self.assertEqual(account.fills,[])

    def test_ignored_orders_do_not_require_permissions(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("A","200")],["B"]))
        account.execute_open(SESSIONS[2],{"A":quote(buy=None),"B":quote(sell=None)})
        self.assertEqual([r.reason for r in account.orders[-2:]],["no_long_position","already_held_no_pyramiding"])
        self.assertEqual(len(account.fills),1)

    def test_capacity_and_settlement_rejections_do_not_require_permissions(self):
        account=SharedCashAccount(config(max_positions=1,settlement_market_sessions=2),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("B","200")],["A"]))
        account.execute_open(SESSIONS[2],{"A":quote(sell=None),"B":quote(buy=None)})
        self.assertEqual([r.reason for r in account.orders[-2:]],["settlement_wait","portfolio_position_limit"])
        self.assertEqual(set(account.positions),{"A"})

    def test_rebuy_after_actual_sale_needs_permission_and_batch_is_atomic(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.submit(decision(1,[("A","200")],["A"]))
        before=(account.cash,dict(account.positions),list(account.fills),list(account.orders),account.pending_decisions)
        with self.assertRaises(ValueError):account.execute_open(SESSIONS[2],{"A":quote(buy=None,sell=True)})
        self.assertEqual((account.cash,account.positions,account.fills,account.orders,account.pending_decisions),before)
        account.execute_open(SESSIONS[2],{"A":quote()})
        self.assertEqual([r.side for r in account.fills],["buy","sell","buy"])

    def test_next_open_and_explicit_calendar_enforced(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        with self.assertRaises(ValueError):account.execute_open(SESSIONS[0],{"A":quote()})
        with self.assertRaises(ValueError):account.execute_open(date(2026,1,10),{"A":quote()})
        self.assertEqual(account.fills,[])

    def test_retry_keeps_original_signal_and_end_holdings_not_fake_closed(self):
        account=SharedCashAccount(config(failed_order_policy="retry_next_market_open"),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote(buy=False)})
        self.assertEqual(len(account.pending_decisions),1)
        account.execute_open(SESSIONS[2],{"A":quote("11")})
        self.assertEqual(account.fills[0].signal_date,SESSIONS[0])
        self.assertEqual(account.fills[0].execution_date,SESSIONS[2])
        self.assertIn("A",account.positions)
        with self.assertRaises(ValueError):account.equity({})

    def test_separate_account_capacity_five(self):
        account=SharedCashAccount(config(max_positions=5),SESSIONS)
        account.submit(decision(0,[(s,"100") for s in "ABCDEF"]))
        account.execute_open(SESSIONS[1],{s:quote() for s in "ABCDEF"})
        self.assertEqual(len(account.positions),5)

    def test_terminal_liquidation_is_explicit_permission_checked_and_separate(self):
        account=SharedCashAccount(config(),SESSIONS)
        account.submit(decision(0,[("A","200")]))
        account.execute_open(SESSIONS[1],{"A":quote()})
        account.terminal_close(SESSIONS[1],{"A":quote("12")},respect_settlement=True)
        self.assertIn("A",account.positions)
        self.assertEqual(account.orders[-1].reason,"terminal_settlement_wait")
        account.terminal_close(SESSIONS[1],{"A":quote("12")},respect_settlement=False)
        self.assertEqual(account.positions,{})
        self.assertEqual(account.fills[-1].reason,"configured_terminal_close_not_natural_exit")

    def test_integrated_replay_context_uses_single_actual_nav_and_causal_next_open(self):
        class Rules:
            approval_status="synthetic_test"
            def require_confirmed(self):pass
        class Strategy:
            max_positions=3
            rules=Rules()
            def decide(self,context):
                from types import SimpleNamespace
                orders=decision(0,[("A","600"),("B","600")]) if context.session==SESSIONS[0] else PortfolioDecision(context.session,(),(),"synthetic hold")
                return SimpleNamespace(decision=orders)
        from types import SimpleNamespace
        seen=[]
        def contexts(day,nav,held):
            seen.append((day,nav,held))
            return SimpleNamespace(session=day,nav=nav,held_symbols=held,price_basis="synthetic_prices")
        result=run_shared_portfolio(config(),SESSIONS[:2],{SESSIONS[1]:{"A":quote(),"B":quote()}},{SESSIONS[0]:{},SESSIONS[1]:{"A":D(11),"B":D(11)}},Strategy(),contexts,source_validation_status="synthetic_test",terminal_policy="mark",terminal_quotes=None,terminal_respects_settlement=True)
        self.assertEqual(result.equity_curve[-1].equity,D("1100.00"))
        self.assertEqual(result.equity_curve[-1].cash,D("0.00"))
        self.assertEqual(result.remaining_positions,(("A",60),("B",40)))
        self.assertEqual(seen[-1][1],D("1100.00"))
        self.assertEqual([f.execution_date for f in result.fills],[SESSIONS[1]]*2)

if __name__=="__main__":unittest.main()
