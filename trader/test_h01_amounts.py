#!/usr/bin/env python3
"""H1 -- an amount is a finite number above zero, in an order and in the record.

2026-11-12, hardening pass, item 1 of an independent review of this folder.

THE DEFECT. NaN fails every comparison. guards.preconditions read the order as
`float(order.get("usd") or 0.0)`: NaN is truthy, `nan < min_order_usd` is False
and `nan > room` is False, so an order of NaN dollars was neither under the
minimum nor over the cap. The day's record had the same hole from the other
side: `_spent` summed the rows, one NaN row made the total NaN, and
`nan >= max_daily_notional_usd` is False, so the day was never full. A negative
row gave the day back room it had already used. The buy budget, the weekly
budget and four of the circuit breakers compared the same way.

  A  an order whose amount, quantity or side is not well-formed is refused by
     guards.preconditions with a named reason; a well-formed one clears.
  B  a row in the day's record that is NaN, infinite, zero, negative or not a
     number makes the day UNKNOWN and both caps block; a clean row does not.
  C  the buy budget, the weekly budget and the fiat headroom block on the same.
  D  the circuit breakers block on a NaN book instead of passing it.
  E  covenant_trader.execute hands a malformed order to no venue at all.
  F  covenant_trader.gather treats a NaN balance as an unread venue and a NaN
     price as no price.
  G  a row that cannot be read stays in the week's record (and so blocks);
     plan() contributes nothing while the week is unknown.

WHAT THIS CANNOT SEE. It drives the functions with numbers this file made up.
It does not show that no other code path sums dollars without asking
guards.finite_positive; `grep -n "float(" guards.py covenant_trader.py` is the
list to read for that. Places nothing, opens no credential, calls no venue.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []
NAN, INF = float("nan"), float("inf")
# Read by name where the fix defines them, so this file also runs (and fails,
# check by check) against the code as it was before the fix.
BAD_AMOUNT = getattr(G, "BAD_AMOUNT", "order amount is not a finite number above zero")
BAD_SIDE = getattr(G, "BAD_SIDE", "order side is not exactly 'buy' or 'sell'")


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")
TODAY = time.strftime("%Y-%m-%d")
# Armed for one hour: later items of the same pass make an arming expire.
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))


def cfg(**over):
    c = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False,
         "seal_required": True, "min_sealed_signals": 0,
         "rule5_require_significance": False, "min_order_usd": 5.0,
         "max_order_usd": 25.0, "max_orders_per_day": 2,
         "max_daily_notional_usd": 50.0}
    c.update(over)
    return c


def reasons(order, st=None):
    return G.preconditions(order, cfg=cfg(),
                           st=st if st is not None else {"day": TODAY, "orders_today": []},
                           sealed_ok=True, guard_blocks=[], config_path=NO_CFG)


def gstate(**over):
    kw = dict(equity_now=1000.0, equity_peak=1000.0, equity_start_of_day=1000.0,
              closed_trades=[], last_sold={}, positions={"XLM": 100.0}, cash=900.0,
              orders_today=[], starting_total_usd=1000.0, bought_total_usd=0.0,
              fiat_buys_week=[])
    kw.update(over)
    return G.State(**kw)


# ---- A: the order ------------------------------------------------------------
good = {"side": "sell", "usd": 20.0, "sym": "XLM", "qty": 20.0}
check("A0 the fixture is clear: a well-formed $20 sell has no reason against it",
      reasons(good) == [], reasons(good))
bad_amounts = {"NaN": NAN, "+inf": INF, "-inf": -INF, "zero": 0.0, "negative": -5.0,
               "True": True, "the string '20'": "20", "None": None, "a list": [20.0]}
let = [k for k, v in bad_amounts.items()
       if not any(BAD_AMOUNT in r for r in reasons(dict(good, usd=v)))]
check("A1 an amount that is " + ", ".join(bad_amounts) + " is refused in those words",
      not let, let)
check("A1b ...NaN in particular, which cleared both caps before: the reason names it",
      any("nan" in r.lower() and BAD_AMOUNT in r for r in reasons(dict(good, usd=NAN))),
      reasons(dict(good, usd=NAN)))
check("A1c ...and an int amount is still an amount (10, not 10.0)",
      reasons(dict(good, usd=10)) == [], reasons(dict(good, usd=10)))
bad_sides = ["BUY", "Sell", "hold", None, "", 1]
let = [s for s in bad_sides if not any(BAD_SIDE in r for r in reasons(dict(good, side=s)))]
check("A2 a side that is not exactly 'buy' or 'sell' is refused: %r" % (bad_sides,), not let, let)
check("A2b ...'buy' is not refused on its side (it reaches the caps and the guards as before)",
      not any(BAD_SIDE in r for r in reasons(dict(good, side="buy"))))
let = [q for q in (NAN, INF, 0.0, -1.0, "1") if not any("quantity" in r for r in reasons(dict(good, qty=q)))]
check("A3 a quantity that is NaN, infinite, zero, negative or a string is refused", not let, let)
no_qty = {"side": "sell", "usd": 20.0, "sym": "XLM"}
check("A3b ...an order with no quantity at all (the sentinel path has none) is not refused for it",
      reasons(no_qty) == [], reasons(no_qty))

# ---- B: the day's record -------------------------------------------------------
pd = G.PerDayCap(max_orders=4, max_notional=50.0)
pt = G.PerTradeCap(max_usd=25.0, min_usd=5.0, max_orders=4, max_notional=50.0)
bad_rows = {"NaN": NAN, "+inf": INF, "zero": 0.0, "negative": -40.0, "True": True,
            "the string '10'": "10", "None": None}
let = []
for k, v in bad_rows.items():
    s = gstate(orders_today=[{"usd": 45.0, "side": "sell"}, {"usd": v, "side": "sell"}])
    if pd.check(s).allowed or pt.largest_allowed(s) is not None or pt.check(s).allowed:
        let.append(k)
check("B1 a row that is " + ", ".join(bad_rows) + " makes the day unknown: both caps block",
      not let, let)
s_ok = gstate(orders_today=[{"usd": 20.0, "side": "sell"}])
check("B1b ...and a clean $20 row does not: 1/4 orders, $30 still placeable",
      pd.check(s_ok).allowed and pt.check(s_ok).allowed
      and abs(pt.largest_allowed(s_ok) - 25.0) < 1e-9, (pd.check(s_ok), pt.check(s_ok)))
s_neg = gstate(orders_today=[{"usd": 45.0, "side": "sell"}, {"usd": -40.0, "side": "sell"}])
check("B2 the negative row does not hand back room: $45 placed and '-$40' is not '$5 used'",
      pt.largest_allowed(s_neg) is None, pt.largest_allowed(s_neg))
through = reasons(good, st={"day": TODAY, "orders_today": [{"usd": NAN, "side": "sell"}]})
check("B3 through guards.preconditions: a NaN row in today's record refuses the next order "
      "in both caps' names", any(r.startswith("per_day_cap:") for r in through)
      and any(r.startswith("per_trade_cap:") for r in through), through)
through_ok = reasons(good, st={"day": TODAY, "orders_today": [{"usd": 20.0, "side": "sell"}]})
check("B3b ...and a clean row in the same place does not", through_ok == [], through_ok)

# ---- C: the budgets ------------------------------------------------------------
bb = G.BuyBudget(0.50)
let = [k for k, v in {"NaN": NAN, "+inf": INF, "negative": -1.0, "True": True}.items()
       if bb.check(gstate(bought_total_usd=v)).allowed or bb.remaining(gstate(bought_total_usd=v)) is not None]
check("C1 a lifetime buy total that is NaN, infinite, negative or a bool blocks the buy budget",
      not let, let)
check("C1b ...NaN starting book too", not bb.check(gstate(starting_total_usd=NAN)).allowed)
check("C1c ...and real numbers do not: $100 spent of a $500 budget leaves $400",
      bb.check(gstate(bought_total_usd=100.0)).allowed
      and abs(bb.remaining(gstate(bought_total_usd=100.0)) - 400.0) < 1e-9)
wb = G.WeeklyBudget(budget=100.0)
now = time.time()
let = [k for k, row in {"NaN usd": {"at": now, "usd": NAN}, "negative usd": {"at": now, "usd": -60.0},
                        "NaN time": {"at": NAN, "usd": 60.0}, "no time": {"usd": 60.0},
                        "not a dict": 60.0}.items()
       if wb.check(gstate(fiat_buys_week=[{"at": now, "usd": 60.0}, row], now=now)).allowed]
check("C2 a week's record with a row that is NaN, negative, undated or not a row blocks the "
      "weekly budget", not let, let)
check("C2b ...and a clean week does not: $60 of $100",
      wb.check(gstate(fiat_buys_week=[{"at": now, "usd": 60.0}], now=now)).allowed)
check("C2c guards.fiat_buys_week_from says UNKNOWN (None) for the same rows, never a shorter list",
      G.fiat_buys_week_from({"fiat_buys": [{"at": now, "usd": NAN}]}, now) is None
      and G.fiat_buys_week_from({"fiat_buys": [{"at": NAN, "usd": 5.0}]}, now) is None
      and G.fiat_buys_week_from({"fiat_buys": [{"at": now, "usd": 5.0}]}, now) == [{"at": now, "usd": 5.0}])
fp = G.FiatBuyPermission(config_path=NO_CFG)
check("C3 the fiat headroom is unknown, and the guard blocks, when a sale row is NaN",
      fp.headroom(gstate(orders_today=[{"usd": NAN, "side": "sell"}])) is None
      and not fp.check(gstate(orders_today=[{"usd": NAN, "side": "sell"}])).allowed)
check("C3b ...and is the sale's own dollars when it is not",
      abs(fp.headroom(gstate(orders_today=[{"usd": 20.0, "side": "sell"}])) - 20.0) < 1e-9)

# ---- D: the circuit breakers ---------------------------------------------------
cases = {
    "max_drawdown / NaN now": (G.MaxDrawdown(0.25), gstate(equity_now=NAN), None),
    "max_drawdown / NaN peak": (G.MaxDrawdown(0.25), gstate(equity_peak=NAN), None),
    "daily_loss / NaN start": (G.DailyLossLimit(0.08), gstate(equity_start_of_day=NAN), None),
    "cash_floor / NaN cash": (G.CashFloor(0.10), gstate(cash=NAN), None),
    "concentration / NaN position": (G.ConcentrationCap(0.20), gstate(positions={"XLM": NAN}), "XLM"),
    "cooldown / NaN last sold": (G.CooldownPeriod(7), gstate(last_sold={"XLM": NAN}), "XLM"),
    "loss_streak / NaN result": (G.LossStreak(4, 30), gstate(closed_trades=[{"pnl": NAN, "closed_at": now}]), None),
}
let = [k for k, (g, s, sym) in cases.items() if g.check(s, sym).allowed]
check("D1 each circuit breaker blocks on a NaN it used to pass: " + "; ".join(cases), not let, let)
let = [g.name for g in (G.MaxDrawdown(0.25), G.DailyLossLimit(0.08), G.CashFloor(0.10),
                        G.ConcentrationCap(0.20), G.CooldownPeriod(7), G.LossStreak(4, 30))
       if not g.check(gstate(), "XLM").allowed]
check("D1b ...and each allows the ordinary book it allowed before", not let, let)


# ---- E: execute() ----------------------------------------------------------------
class FakeVenue:
    name = "fake"

    def __init__(self):
        self.calls = []

    def has_credentials(self):
        return True

    def place(self, sym, side, qty, live=False, **kw):
        self.calls.append((sym, side, qty, live))
        return {"descr": "fake", "txid": None}


def run_execute(order):
    fv = FakeVenue()
    keep_all, keep_state, keep_save = T.V.all_venues, T.STATE, T.save_state
    st = {"day": TODAY, "orders_today": []}
    try:
        T.V.all_venues = lambda: [fv]
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.save_state = lambda s: None
        res = T.execute(cfg(armed=False), st, [order], True, [])
    finally:
        T.V.all_venues, T.STATE, T.save_state = keep_all, keep_state, keep_save
    return res, fv, st


base = {"sym": "XLM", "side": "sell", "qty": 20.0, "usd": 20.0, "rule": "test", "at": {"fake": 100.0}}
let = []
for k, o in {"NaN qty": dict(base, qty=NAN), "NaN usd": dict(base, usd=NAN),
             "zero qty": dict(base, qty=0.0), "negative usd": dict(base, usd=-20.0),
             "inf qty": dict(base, qty=INF), "side BUY": dict(base, side="BUY"),
             "no symbol": dict(base, sym="")}.items():
    res, fv, st = run_execute(o)
    if fv.calls or not res or res[0]["status"] != "REFUSED" or st["orders_today"]:
        let.append((k, res[0]["status"] if res else None, fv.calls))
check("E1 a malformed order (NaN/zero/inf quantity, NaN/negative dollars, side 'BUY', no symbol) "
      "is REFUSED with its reason and reaches NO venue call, not even a dry run", not let, let)
res, fv, st = run_execute(dict(base))
check("E1b ...and a well-formed one does reach the venue's dry run (disarmed: live=False)",
      fv.calls == [("XLM", "sell", 20.0, False)] and res[0]["status"] == "VALIDATED",
      (fv.calls, res))


# ---- F: gather() ---------------------------------------------------------------
class BalVenue:
    def __init__(self, name, bal):
        self.name, self._bal = name, bal

    def has_credentials(self):
        return True

    def balances(self):
        return dict(self._bal)


def run_gather(venues, prices):
    keep_all, keep_pre = T.V.all_venues, T.daily.prefetch
    try:
        T.V.all_venues = lambda: venues
        T.daily.prefetch = lambda syms, source: {s: prices[s] for s in syms if s in prices}
        return T.gather({"symbols": []})
    finally:
        T.V.all_venues, T.daily.prefetch = keep_all, keep_pre


CLOSES = [1.0] * 200
pf = run_gather([BalVenue("a", {"XLM": 100.0, "USD": 50.0}), BalVenue("b", {"ADA": NAN, "USD": 10.0})],
                {"XLM": (1.0, CLOSES, None, [], []), "ADA": (1.0, CLOSES, None, [], [])})
check("F1 a venue reporting a NaN balance is treated as UNREAD: none of its numbers are used",
      [p["sym"] for p in pf["positions"]] == ["XLM"] and abs(pf["cash"] - 50.0) < 1e-9
      and pf["venues_read"] == ["a"] and any("b:" in n and "not a finite" in n for n in pf["venue_notes"]),
      pf)
pf2 = run_gather([BalVenue("a", {"XLM": 100.0, "ADA": 10.0, "USD": 50.0})],
                 {"XLM": (NAN, CLOSES, None, [], []), "ADA": (2.0, CLOSES, None, [], [])})
check("F2 a NaN price is NO PRICE: the holding is listed as unpriced, the total stays a number",
      [u["sym"] for u in pf2["unpriced"]] == ["XLM"] and abs(pf2["total"] - 70.0) < 1e-9, pf2)
pf3 = run_gather([BalVenue("a", {"XLM": 100.0, "USD": 50.0})], {"XLM": (1.0, CLOSES, None, [], [])})
check("F2b ...and an ordinary book is read as before: $100 of XLM and $50 cash",
      not pf3["unpriced"] and abs(pf3["total"] - 150.0) < 1e-9 and pf3["venues_read"] == ["a"], pf3)

# ---- G: the week's record ------------------------------------------------------
old = now - 30 * 86400
st = T.roll_day({"day": TODAY, "orders_today": [],
                 "fiat_buys": [{"at": old, "usd": 5.0}, {"at": NAN, "usd": 60.0},
                               {"at": now, "usd": 7.0}]})
check("G1 roll_day drops the row that is old and keeps the one whose time cannot be read",
      len(st["fiat_buys"]) == 2 and st["fiat_buys"][1] == {"at": now, "usd": 7.0}, st["fiat_buys"])
check("G1b ...so the guard's reader says the week is unknown rather than $7",
      G.fiat_buys_week_from(st, now) is None)


def run_plan(week_spent):
    positions = [{"sym": "XLM", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "UP", "at": {"fake": 100.0}}]
    held = {"XLM": 100.0}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: (dict(held), held, [])
        c = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0,
             "min_order_usd": 5.0, "max_orders_per_day": 2, "allow_fiat_buys": True,
             "weekly_fiat_budget_usd": 100.0}
        return T.plan(c, {"positions": positions, "cash": 900.0, "total": 1000.0,
                          "unpriced": [], "venue_notes": []}, week_spent=week_spent)
    finally:
        T.reserve_baseline = keep


o_unknown, n_unknown = run_plan(None)
o_known, n_known = run_plan(0.0)
check("G2 plan() with the week UNKNOWN contributes nothing and says the record cannot be read",
      not [o for o in o_unknown if o["side"] == "buy"]
      and any(n.startswith("R6 contribution") and "cannot be read" in n for n in n_unknown),
      (o_unknown, n_unknown))
check("G2b ...and with the week known to be empty it contributes as before",
      len([o for o in o_known if o["side"] == "buy"]) == 1, (o_known, n_known))

n = len(results)
print(f"\nH1 amounts: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
