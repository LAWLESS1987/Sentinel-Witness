#!/usr/bin/env python3
"""R1 -- the live gate takes no decision from its caller.

2026-11-12, review round one. Confirmed by the outside reviewer, and true of
every commit of the hardening pass before this one:

THE DEFECT. guards.preconditions and covenant_trader.execute judged the config
dict and the state dict they were HANDED, and took three more things on the
caller's word: sealed_ok, guard_blocks and (since H8) the approval record.
covenant_trader.run_once hands in the truth. A Python caller that handed in
{"armed": true, "armed_until": <soon>, "daily_plan_required": false,
"seal_required": false, "min_sealed_signals": 0} with sealed_ok=True cleared
all of it and execute() went live. And each venue adapter's place() took
live=True from anyone.

WHAT IS HERE NOW. guards.live_gate is the one thing that makes an order live.
It reads trader_config.json and the state file ITSELF, asks the witness what
this process has seen sealed and what was approved, asks the venue about its
key, evaluates a buy's guards itself, and records a one-use permit. A config
or state handed in is only compared with disk. A venue adapter sends a live
order only against such a permit.

  A  the control: an order with every gate satisfied goes LIVE.
  B  a hostile caller's permissive dict places nothing -- with no config on
     disk, with a disarmed one, and with a fully gated one that the dict
     differs from in any single gate-relevant key.
  C  a state handed in that hides the day's orders places nothing.
  D  sealed_ok=True is not believed: no seal, a seal through an injected
     runner, an expired seal, a seal of another order -- nothing goes live.
  E  an approval record handed to execute() is ignored; covenant is asked.
  F  a buy: no portfolio, or a portfolio the gate's own guards block, or a
     block the caller adds -- nothing goes live; a healthy one does.
  G  calling a venue adapter directly with live=True sends nothing; nor does
     its lowest order-sending call; a permit is for one order, one venue, one
     use, two minutes.
  H  live_gate cannot be HANDED a clock, a seal, an approval or a path.
  I  a whole armed cycle through run_once places the approved order; the same
     cycle handed a permissive config in place of the one on disk places none.

WHAT THIS CANNOT SEE, AND WHAT R1 IS NOT. Code running inside this process
that reaches into guards._PERMITS, replaces guards.live_gate, or rewrites the
config file is not stopped by any of this: Python has no such boundary and the
files belong to the same OS user. (Two older suites use exactly that door --
they REPLACE the gate to study bookkeeping -- and say so.) The portfolio a buy
is judged on is still the caller's measurement. Stand-in venues and a stand-in
covenant only; nothing here reaches a venue, covenant or the real home folder.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import witness as W          # noqa: E402
import guards as G           # noqa: E402
import venues as V           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


try:
    from gated_fixture import Gated, Venue, order, until
except Exception as e:                                           # the code before the fix
    print("FAIL  the gated fixture cannot be loaded (%s: %s)" % (type(e).__name__, e))
    sys.exit(1)

TODAY = time.strftime("%Y-%m-%d")
O = order("XLM", "sell", 20.0)


def why(res, i=0):
    return res[i].get("blocked_by") or []


# What a hostile caller hands in: armed, and every other gate switched off.
PERMISSIVE = {"armed": True, "armed_until": until(), "daily_plan_required": False,
              "seal_required": False, "min_sealed_signals": 0, "rule5_require_significance": False,
              "min_order_usd": 5.0, "max_order_usd": 25.0, "max_orders_per_day": 2,
              "max_daily_notional_usd": 50.0}
EMPTY_DAY = {"day": TODAY, "orders_today": [], "sealed_signals": 30, "rule5": {"clears": True}}


def hostile_execute(cfg, st, venue, orders=None, **kw):
    keep = T.V.all_venues
    try:
        T.V.all_venues = lambda: [venue]
        return T.execute(cfg, st, orders or [dict(O)], True, [], **kw)
    finally:
        T.V.all_venues = keep


# ---- A: the control ---------------------------------------------------------------
with Gated(approved=[O]) as fx:
    v = Venue()
    res = fx.execute([dict(O)], [v])
    check("A1 with every gate satisfied -- config and state on disk, covenant pinned, sealed, "
          "approved, a trade-only key -- the order goes LIVE", v.live() == [True]
          and res[0]["status"] == "PLACED" and len(fx.state_on_disk()["orders_today"]) == 1,
          (v.live(), res[0]["status"], why(res)))


def _raises_value_error(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


with Gated(approved=[O]) as fx:
    v = Venue()
    fx.execute([dict(O)], [v])
    pid = v.calls[0]["permit"]
    check("A2 ...and the venue was handed the gate's permit; a stand-in does not consume it, so it "
          "is good ONCE more and never again",
          isinstance(pid, str) and G.consume_permit(pid, "fake", "XLM", "sell") == 20.0
          and _raises_value_error(lambda: G.consume_permit(pid, "fake", "XLM", "sell")))

# ---- B: the permissive dict ----------------------------------------------------------
keep_cfg, keep_state = G.CONFIG_PATH, G.TRADER_STATE
try:
    nowhere = tempfile.mkdtemp()
    G.CONFIG_PATH = os.path.join(nowhere, "trader_config.json")          # no such file
    G.TRADER_STATE = os.path.join(nowhere, "trader_state.json")
    v = Venue()
    res = hostile_execute(dict(PERMISSIVE), dict(EMPTY_DAY), v)
    check("B1 a permissive dict with NO config on disk: nothing goes live, and the reason is "
          "that there is no trader_config.json", v.live() == [False] and res[0]["status"] == "VALIDATED"
          and any("no trader_config.json on disk" in r for r in why(res)), (v.live(), why(res)))
    with open(G.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump({"armed": False}, fh)
    v = Venue()
    res = hostile_execute(dict(PERMISSIVE), dict(EMPTY_DAY), v)
    check("B2 the same dict with a DISARMED config on disk: nothing goes live; the dict is said "
          "to differ from the file, and the file is said to be disarmed", v.live() == [False]
          and any("differs from trader_config.json" in r and "armed" in r for r in why(res))
          and any(r.startswith("armed=false") for r in why(res)), (v.live(), why(res)))
finally:
    G.CONFIG_PATH, G.TRADER_STATE = keep_cfg, keep_state

changes = {
    "max_order_usd": 10.0, "max_daily_notional_usd": 40.0, "max_orders_per_day": 1,
    "min_order_usd": 1.0, "max_position_pct": 0.15, "min_cash_pct": 0.2,
    "seal_required": False, "min_sealed_signals": 0, "rule5_require_significance": False,
    "daily_plan_required": False, "allow_fiat_buys": True, "weekly_fiat_budget_usd": 50.0,
    "contribution_min_cash_pct": 0.5, "contribution_symbols": ["XLM"], "symbols": ["XLM"],
    "armed_until": until(7200),
    "rule5_waiver": {"waived": True, "by": "x", "at": "x", "words": "x"},
    "trading_scope": {"open_reserve_pct": 0.5, "by": "x", "at": "x", "words": "x"},
    "covenant_home": tempfile.mkdtemp(),
}
let = {}
for key, val in changes.items():
    with Gated(approved=[O]) as fx:
        v = Venue()
        fx.seal([dict(O)])
        res = hostile_execute(dict(fx.cfg, **{key: val}), fx.st, v)
        # execute()'s first pass judges the dict it was handed and may refuse on
        # the dict's own terms before the gate is asked (a waiver, a lowered
        # cap). Either way nothing may go live; and the gate, asked directly,
        # must name the key.
        said, permit = G.live_gate(dict(O), Venue(), cfg=dict(fx.cfg, **{key: val}), st=fx.st)
        named = [r for r in said if "differs from trader_config.json" in r and key in r]
        if v.live() != [False] or not named or permit is not None:
            let[key] = (v.live(), why(res), said)
check("B3 against a fully gated config on disk, a dict that differs from it in ANY ONE of %d "
      "gate-relevant keys places nothing through execute(), and the gate names the key: %s"
      % (len(changes), ", ".join(changes)), not let, let)
check("B3b ...and those, with `armed` (B2), are every key the gate compares",
      set(changes) | {"armed"} == set(getattr(G, "GATE_KEYS", ())),
      set(getattr(G, "GATE_KEYS", ())) ^ (set(changes) | {"armed"}))
with Gated(approved=[O]) as fx:
    v = Venue()
    fx.seal([dict(O)])
    res = hostile_execute(dict(fx.cfg), fx.st, v)
    check("B4 ...while the SAME dict as the file goes live (the refusals above are the difference, "
          "not the harness)", v.live() == [True], (v.live(), why(res)))
    v2 = Venue()
    res = hostile_execute(dict(fx.cfg, some_other_key="anything", loop_seconds=5), fx.st, v2,
                          orders=[order("XLM", "sell", 20.0)])
    check("B5 a difference in a key that is not a gate (loop_seconds, an unknown key) is not a "
          "refusal on that account", not any("differs from trader_config.json" in r for r in why(res)), why(res))

# ---- C: the state ---------------------------------------------------------------------
two = [{"sym": "ADA", "usd": 25.0, "side": "sell", "status": "PLACED"},
       {"sym": "SOL", "usd": 25.0, "side": "sell", "status": "PLACED"}]
with Gated(approved=[O], state={"orders_today": two}) as fx:
    v = Venue()
    fx.seal([dict(O)])
    res = hostile_execute(fx.cfg, dict(fx.st, orders_today=[]), v)
    check("C1 the day's two orders are on DISK; a caller handing in a state with none places "
          "nothing -- the state is said to differ, and the day's cap refuses from the file",
          v.live() == [False] and any("differs from the trader's state file" in r and "orders_today" in r
                                      for r in why(res))
          and any(r.startswith("per_day_cap") for r in why(res)), (v.live(), why(res)))
with Gated(approved=[O], state={"sealed_signals": 3, "rule5": {"clears": False, "why": "3 of 30"}}) as fx:
    v = Venue()
    fx.seal([dict(O)])
    res = hostile_execute(fx.cfg, dict(fx.st, sealed_signals=30, rule5={"clears": True}), v)
    check("C2 Rule 5 has 3 signals on DISK; a caller handing in a state with 30 places nothing, "
          "and Rule 5 refuses in its own words", v.live() == [False]
          and any(r.startswith("Rule 5") and "need 30" in r for r in why(res)), (v.live(), why(res)))

# ---- D: the seal ------------------------------------------------------------------------
with Gated(approved=[O]) as fx:
    v = Venue()
    res = fx.execute([dict(O)], [v], seal=False)                 # execute() is still told sealed_ok=True
    check("D1 sealed_ok=True with NOTHING sealed through the witness: nothing goes live",
          v.live() == [False] and any(r.startswith("seal:") for r in why(res)), (v.live(), why(res)))
    admitted = {"ok": True, "status": 200, "admission": "admitted", "tx_id": "t", "detail": "", "mined": ""}
    W.seal_decision_result(fx.cfg, {"orders": [dict(O)]}, runner=lambda *a: (True, dict(admitted), ""))
    v = Venue()
    res = fx.execute([dict(O)], [v], seal=False)
    check("D2 a seal obtained through an INJECTED runner (a caller's stand-in for covenant) is "
          "not remembered: nothing goes live", v.live() == [False], (v.live(), why(res)))
with Gated(approved=[O]) as fx:
    fx.seal([order("XLM", "sell", 25.0)])                        # covenant admitted a $25 sale
    v = Venue()
    res = fx.execute([dict(O)], [v], seal=False)                 # ...and a $20 one is sent
    check("D3 a seal of ANOTHER order (the same symbol and side, $25) does not cover this one ($20)",
          v.live() == [False] and any(r.startswith("seal:") for r in why(res)), (v.live(), why(res)))
with Gated(approved=[O]) as fx:
    fx.seal([dict(O)])
    keep_clock = W._clock
    try:
        W._clock = lambda: time.time() + W.SEAL_VALID_S + 60
        v = Venue()
        res = fx.execute([dict(O)], [v], seal=False)
    finally:
        W._clock = keep_clock
    check("D4 a seal older than %d minutes has lapsed: nothing goes live" % (W.SEAL_VALID_S // 60),
          v.live() == [False] and any(r.startswith("seal:") for r in why(res)), (v.live(), why(res)))
with Gated(approved=[O]) as fx:
    fx._runner_keep = fx._runner
    refused = lambda home, script, payload, timeout: (
        (True, {"ok": False, "admission": "refused", "detail": "judge refused"}, "")
        if script is W._SEAL_SCRIPT else fx._runner_keep(home, script, payload, timeout))
    W._run = refused
    fx.seal([dict(O)])
    v = Venue()
    res = fx.execute([dict(O)], [v], seal=False)
    check("D5 a record covenant REFUSED to admit is not a seal", v.live() == [False], (v.live(), why(res)))

# ---- E: the approval ----------------------------------------------------------------------
with Gated(approved=[]) as fx:                                    # covenant's plan proposes NOTHING
    forged = dict(fx.approval(), orders=[{"sym": "XLM", "side": "sell", "usd": 20.0}])
    v = Venue()
    res = fx.execute([dict(O)], [v], approval=forged)
    check("E1 an approval record HANDED to execute() that proposes the order is ignored; covenant "
          "is asked, its plan proposes none, and nothing goes live", v.live() == [False]
          and any("proposes NO orders" in r for r in why(res)) and "approval" in fx.asked,
          (v.live(), why(res), fx.asked))

# ---- F: a buy ---------------------------------------------------------------------------------
BUY = order("XLM", "buy", 20.0)
healthy = G.State(equity_now=1000.0, equity_peak=1000.0, equity_start_of_day=1000.0, closed_trades=[],
                  last_sold={}, positions={"XLM": 100.0}, cash=900.0)
poor = G.State(equity_now=1000.0, equity_peak=1000.0, equity_start_of_day=1000.0, closed_trades=[],
               last_sold={}, positions={"XLM": 100.0, "ADA": 850.0}, cash=50.0)
buy_cfg = {"allow_fiat_buys": True}
with Gated(approved=[BUY], cfg=buy_cfg) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v], portfolio=healthy)
    check("F1 a BUY with a healthy book (cash 90%, the coin at 10%) goes live", v.live() == [True],
          (v.live(), why(res)))
with Gated(approved=[BUY], cfg=buy_cfg) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v])
    check("F2 the same BUY with NO portfolio handed over: its guards cannot be evaluated, nothing "
          "goes live", v.live() == [False] and any("no portfolio" in r for r in why(res)), (v.live(), why(res)))
with Gated(approved=[BUY], cfg=buy_cfg) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v], portfolio=poor)           # guard_blocks handed in is []
    check("F3 the same BUY on a book with cash at 5%: the caller said guard_blocks=[], the gate "
          "evaluated the cash floor ITSELF and refused", v.live() == [False]
          and any("cash_floor" in r for r in why(res)), (v.live(), why(res)))
with Gated(approved=[BUY], cfg=buy_cfg, state={"last_sold": {"XLM": time.time() - 3600}}) as fx:
    v = Venue()
    lying = G.State(equity_now=1000.0, equity_peak=1000.0, equity_start_of_day=1000.0, closed_trades=[],
                    last_sold={}, positions={"XLM": 100.0}, cash=900.0)
    res = fx.execute([dict(BUY)], [v], portfolio=lying)
    check("F4 the coin was sold an hour ago ON DISK; a portfolio handed in with an empty last_sold "
          "does not lift the cooldown -- history is read from the file", v.live() == [False]
          and any("cooldown" in r for r in why(res)), (v.live(), why(res)))
with Gated(approved=[BUY], cfg=buy_cfg) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v], portfolio=healthy, may_buy=lambda sym: ["operator_says_no"])
    check("F5 a block the CALLER adds is honoured: a caller can add a refusal, never remove one",
          v.live() == [False] and any("operator_says_no" in r for r in why(res)), (v.live(), why(res)))

# ---- G: the venue called directly ---------------------------------------------------------------
TRADE_ONLY = ["query-funds", "query-open-trades", "query-closed-trades", "modify-trades", "close-trades"]


class Kraken(V.KrakenVenue):
    def __init__(self):
        super().__init__()
        self.sent = []
        self._pairs = {"XLM": {"pair": "XXLMZUSD", "symbol": "XLM", "altname": "XLMUSD", "wsname": "XLM/USD",
                               "lot_decimals": 8, "pair_decimals": 6, "ordermin": 1.0, "costmin": 0.5}}

    def has_credentials(self):
        return True

    def _creds(self):
        return "k", "c2VjcmV0"


class Coinbase(V.CoinbaseVenue):
    def __init__(self):
        super().__init__()
        self.sent = []

    def has_credentials(self):
        return True

    def meta(self, symbol):
        return {"product_id": f"{symbol}-USD", "base_increment": "0.00000001",
                "quote_increment": "0.0001", "base_min_size": 0.00001}

    def best_bid_ask(self, product_id):
        return 0.9999, 1.0001

    def _jwt(self, method, path):
        return "token"


class Robin(V.RobinhoodVenue):
    def __init__(self):
        super().__init__()
        self.sent = []

    def has_credentials(self):
        return True

    def meta(self, symbol):
        return {"pair": f"{symbol}-USD", "symbol": symbol, "ordermin": 0.1, "increment": "0.0001", "status": "tradable"}

    def _creds(self):
        raise AssertionError("the credential was reached")


sent = []
keep_http, keep_urlopen = V._http, V.urllib.request.urlopen
QTY = 20.0 / 1.01                                                # sized so its worst case is $20 (R2)


def fake_http(req, timeout=30):
    sent.append((req.get_method(), req.full_url, (req.data or b"").decode()))
    if req.full_url.endswith("/AddOrder"):
        return {"error": [], "result": {"txid": ["OABC"], "descr": {"order": "x"}}}
    if req.full_url.endswith("/GetApiKeyInfo"):
        return {"error": [], "result": {"permissions": TRADE_ONLY}}
    return {"success": True, "success_response": {"order_id": "oid"}}


def booked():
    """The order-placing requests that reached the (fake) wire: a Kraken
    AddOrder with no validate flag, or a POST to an orders endpoint that is
    not a preview."""
    return [s for s in sent if (s[1].endswith("/AddOrder") and "validate=true" not in s[2])
            or (s[0] == "POST" and s[1].rstrip("/").endswith("/orders"))]


def attempt(fn):
    try:
        fn()
        return "sent"
    except V.VenueRefused as e:
        return "refused: %s" % e
    except Exception as e:                                       # noqa: BLE001
        return "error: %s: %s" % (type(e).__name__, e)


try:
    V._http = fake_http
    V.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("the network was reached"))
    for cls in (Kraken, Coinbase, Robin):
        ven = cls()
        del sent[:]
        got = attempt(lambda: ven.place("XLM", "sell", QTY, live=True, ref_px=1.0))
        check("G1 %s.place(live=True) called directly, with no permit: refused, and NOTHING reaches "
              "the wire" % ven.name, got.startswith("refused") and "permit" in got and booked() == [], (got, sent))
        got = attempt(lambda: ven.place("XLM", "sell", QTY, live=True, ref_px=1.0, permit="f" * 32))
        check("G2 ...nor with a permit id the caller made up", got.startswith("refused") and booked() == [], (got, sent))

    k = Kraken()
    del sent[:]
    for label, pid, args in (
            ("for another SYMBOL", G._issue_permit("kraken", "ADA", "sell", 20.0), ("XLM", "sell")),
            ("for the other SIDE", G._issue_permit("kraken", "XLM", "buy", 20.0), ("XLM", "sell")),
            ("for another VENUE", G._issue_permit("coinbase", "XLM", "sell", 20.0), ("XLM", "sell"))):
        got = attempt(lambda: k.place(args[0], args[1], QTY, live=True, ref_px=1.0, permit=pid))
        check("G3 a real permit %s is not a permit for this order" % label,
              got.startswith("refused") and booked() == [], (got, sent))
    pid = G._issue_permit("kraken", "XLM", "sell", 20.0)
    first = attempt(lambda: k.place("XLM", "sell", QTY, live=True, ref_px=1.0, permit=pid))
    n_first = len(booked())
    second = attempt(lambda: k.place("XLM", "sell", QTY, live=True, ref_px=1.0, permit=pid))
    check("G4 a permit for exactly this order sends it ONCE (the control: the adapter can send) -- "
          "and the same permit a second time sends nothing", first == "sent" and n_first == 1
          and second.startswith("refused") and len(booked()) == 1, (first, second, sent))
    pid = G._issue_permit("kraken", "XLM", "sell", 20.0)
    G._PERMITS[pid]["expires"] = time.time() - 1
    del sent[:]
    got = attempt(lambda: k.place("XLM", "sell", QTY, live=True, ref_px=1.0, permit=pid))
    check("G5 a permit past its %d seconds sends nothing" % G.PERMIT_VALID_S,
          got.startswith("refused") and "expired" in got and booked() == [], (got, sent))

    del sent[:]
    low = {
        "kraken _private('AddOrder') with no validate": lambda: Kraken()._private(
            "AddOrder", {"pair": "XXLMZUSD", "type": "sell", "ordertype": "market", "volume": "10"}),
        "coinbase _call('POST', '/api/v3/brokerage/orders')": lambda: Coinbase()._call(
            "POST", "/api/v3/brokerage/orders", {"product_id": "XLM-USD"}),
        "robinhood _call('POST', '.../orders/')": lambda: Robin()._call(
            "POST", "/api/v1/crypto/trading/orders/", {"symbol": "XLM-USD"}),
    }
    got = {k_: attempt(fn) for k_, fn in low.items()}
    check("G6 each adapter's LOWEST order-sending call, made directly, sends nothing: "
          + "; ".join(low), all(x.startswith("refused") for x in got.values()) and booked() == [], (got, sent))
    del sent[:]
    got = attempt(lambda: Kraken().place("XLM", "sell", 10.0, live=False, ref_px=1.0))
    check("G7 ...while a DRY RUN needs no permit and is sent to the venue's validate endpoint",
          got == "sent" and len(sent) == 1 and "validate=true" in sent[0][2] and booked() == [], (got, sent))

    # end to end: the gate, then a REAL adapter over a fake wire
    KO = order("XLM", "sell", 20.0, venue="kraken")
    with Gated(approved=[KO]) as fx:
        k = Kraken()
        del sent[:]
        res = fx.execute([dict(KO)], [k])
        check("G8 end to end -- the gate, then the real Kraken adapter over a fake wire: its key is "
              "asked (GetApiKeyInfo), then ONE AddOrder is sent with no validate flag",
              res[0]["status"] == "PLACED" and len(booked()) == 1
              and any(s[1].endswith("/GetApiKeyInfo") for s in sent), (res[0]["status"], why(res), sent))
    with Gated(approved=[KO], cfg={"armed": False}) as fx:
        k = Kraken()
        del sent[:]
        res = fx.execute([dict(KO)], [k])
        check("G9 ...and the same with the config on disk DISARMED: one validate call, nothing booked",
              res[0]["status"] == "VALIDATED" and booked() == [] and len(sent) == 1
              and "validate=true" in sent[0][2], (res[0]["status"], sent))
finally:
    V._http, V.urllib.request.urlopen = keep_http, keep_urlopen

# ---- H: what the gate cannot be handed ------------------------------------------------------
params = set(inspect.signature(G.live_gate).parameters) if hasattr(G, "live_gate") else {"(no live_gate)"}
check("H1 guards.live_gate takes an order, a venue, a config and state TO COMPARE, a portfolio and "
      "blocks to ADD -- and nothing else: no now, no sealed_ok, no approval, no path",
      params == {"order", "venue", "cfg", "st", "portfolio", "caller_blocks"}, params)
check("H2 the config and state the gate reads are the files the trader reads",
      os.path.normcase(G.CONFIG_PATH) == os.path.normcase(T.CONFIG)
      and os.path.normcase(G.TRADER_STATE) == os.path.normcase(T.STATE), (G.CONFIG_PATH, T.CONFIG))
with Gated(approved=[O]) as fx:
    fx.seal([dict(O)])
    reasons, permit = G.live_gate(dict(O), Venue())
    check("H3 asked directly with nothing but the order and the venue, the gate judges disk and "
          "permits it", reasons == [] and isinstance(permit, str), reasons)
    reasons, permit = G.live_gate(dict(O), None)
    check("H4 ...and with no venue there is no permit", reasons and permit is None, reasons)


# ---- I: a whole cycle through run_once -----------------------------------------------------------
class BookVenue(Venue):
    def balances(self):
        return {"JASMY": 100.0, "XLM": 100.0, "USD": 800.0}


def cycle(fx, cfg):
    v = BookVenue()
    keep = {k_: getattr(T, k_) for k_ in ("node_status",)}
    keep_pre, keep_rc, keep_all = T.daily.prefetch, T.signal_ledger.record_cycle, T.V.all_venues
    out = io.StringIO()
    try:
        T.V.all_venues = lambda: [v]
        T.node_status = lambda ports, timeout=4: []
        T.daily.prefetch = lambda syms, source: {"JASMY": (1.0, [2.0] * 200, None, [], []),
                                                 "XLM": (1.0, [0.5] * 200, None, [], [])}
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 30, "wins": 30, "mean_after_costs": 0.02, "p_value": 0.001,
            "clears": True, "why": "stand-in"}
        with contextlib.redirect_stdout(out):
            T.run_once(cfg)
    finally:
        for k_, val in keep.items():
            setattr(T, k_, val)
        T.daily.prefetch, T.signal_ledger.record_cycle, T.V.all_venues = keep_pre, keep_rc, keep_all
    return v, out.getvalue()


SCOPE = {"open_reserve_pct": 0.5, "by": "operator", "at": "2026-11-12", "words": "stand-in"}
PLANNED = {"sym": "JASMY", "side": "sell", "usd": 25.0}
with Gated(approved=[PLANNED], cfg={"trading_scope": SCOPE},
           state={"last_seen_at": time.time() - 3600}) as fx:
    with open(fx.reserve, "w", encoding="utf-8") as fh:
        json.dump({"baseline": {"JASMY": 100.0, "XLM": 100.0}, "starting_total_usd": 1000.0}, fh)
    v, out = cycle(fx, T.load_config())
    check("I1 a whole ARMED cycle through run_once -- read, plan, seal, gate -- places the one "
          "approved order, live", [(c["sym"], c["live"]) for c in v.calls] == [("JASMY", True)]
          and "[PLACED" in out, ([(c["sym"], c["live"]) for c in v.calls], out[-600:]))
with Gated(approved=[PLANNED], cfg={"armed": False, "trading_scope": SCOPE},
           state={"last_seen_at": time.time() - 3600}) as fx:
    with open(fx.reserve, "w", encoding="utf-8") as fh:
        json.dump({"baseline": {"JASMY": 100.0, "XLM": 100.0}, "starting_total_usd": 1000.0}, fh)
    hostile = dict(T.load_config())
    hostile.update({"armed": True, "armed_until": until()})
    v, out = cycle(fx, hostile)
    check("I2 the same cycle with the file on disk DISARMED and run_once handed a config that says "
          "armed: the order is planned, sealed -- and goes to the dry run only",
          [(c["sym"], c["live"]) for c in v.calls] == [("JASMY", False)] and "[PLACED" not in out
          and "differs from trader_config.json" in out, ([(c["sym"], c["live"]) for c in v.calls], out[-600:]))

n = len(results)
print(f"\nR1 live gate: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
