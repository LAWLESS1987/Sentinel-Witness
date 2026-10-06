#!/usr/bin/env python3
"""SW2 -- the operator's trading scope and the rules it turns on, driven both ways.

2026-10-06. His words: "We already have a rule about those three. Everything
else is good to sell in trade." and "build on and edit the rules for all bur
the 3 we mentioned to begin operation i'm willing to take the risk".

  S1  the scope is a RECORD: only a complete one counts; anything else is the
      rule before it (half of every other coin reserved).
  S2  it opens every coin EXCEPT the hold-only three: a 0% reserve can never
      reach XRP, HBAR or LINK, whose frozen floors stay exactly as they were.
  S3  R3 is automated for open coins: below its 200-day line, a coin is sold
      toward its floor, one capped order at a time -- and never a hold-only one.
  S4  R1 is automated for open coins: cash under the floor sells the largest
      open coin above its line, only as much as the shortfall, inside the caps.
  S5  with no record, plan() is what it was: no R3, no R1 sells, the old note.

Stubs the floor file and the portfolio. Places nothing, opens no credential,
starts no covenant process.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:220]}"), flush=True)


SCOPE = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06",
         "words": "Everything else is good to sell in trade."}


def cfg(scope=True, **over):
    c = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0,
         "min_order_usd": 5.0, "max_orders_per_day": 2, "allow_fiat_buys": False,
         "weekly_fiat_budget_usd": 0.0}
    if scope:
        c["trading_scope"] = dict(SCOPE)
    c.update(over)
    return c


def pos(sym, qty, px, regime="UP"):
    return {"sym": sym, "qty": qty, "px": px, "val": qty * px, "regime": regime, "at": ["coinbase"]}


def run_plan(c, positions, cash, base=None):
    """plan() with the floor file stubbed: base defaults to what is held."""
    held = {p["sym"]: p["qty"] for p in positions}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: (dict(base if base is not None else held), held, [])
        total = sum(p["val"] for p in positions) + cash
        return T.plan(c, {"positions": positions, "cash": cash, "total": total,
                          "unpriced": [], "venue_notes": []})
    finally:
        T.reserve_baseline = keep


def sells(orders, sym=None):
    return [o for o in orders if o["side"] == "sell" and (sym is None or o["sym"] == sym)]


# ---- S1: only a complete record counts ------------------------------------------
check("S1a a complete scope is read", G.trading_scope({"trading_scope": SCOPE}) is not None)
bad = {
    "absent": {},
    "pct missing": {"trading_scope": {k: v for k, v in SCOPE.items() if k != "open_reserve_pct"}},
    "pct True": {"trading_scope": {**SCOPE, "open_reserve_pct": True}},
    "pct below 0": {"trading_scope": {**SCOPE, "open_reserve_pct": -0.1}},
    "pct above 1": {"trading_scope": {**SCOPE, "open_reserve_pct": 1.5}},
    "pct a string": {"trading_scope": {**SCOPE, "open_reserve_pct": "0"}},
    "no 'by'": {"trading_scope": {k: v for k, v in SCOPE.items() if k != "by"}},
    "blank 'words'": {"trading_scope": {**SCOPE, "words": " "}},
    "a list": {"trading_scope": [SCOPE]},
}
through = [k for k, c in bad.items() if G.trading_scope(c) is not None]
check("S1b every incomplete or mistyped scope is NO scope: " + ", ".join(bad), not through, through)
check("S1c no scope -> the rule before it: half of every other coin reserved",
      G.open_reserve_pct({}) == 0.50 and G.open_reserve_pct({"trading_scope": SCOPE}) == 0.0)

# ---- S2: the three are untouched ------------------------------------------------------
for sym in G.HOLD_ONLY:
    check(f"S2a {sym}: a 0% reserve still leaves NOTHING sellable at its frozen floor",
          G.sellable_units(100.0, 100.0, sym, pct=0.0) == 0.0)
check("S2b ...and above its floor exactly the excess, as before (pct is ignored for the three)",
      G.sellable_units(130.0, 100.0, "LINK", pct=0.0) == G.sellable_units(130.0, 100.0, "LINK", pct=0.5) == 30.0)
check("S2c an open coin at 0% is wholly sellable; at the old 50% only half",
      G.sellable_units(100.0, 100.0, "XLM", pct=0.0) == 100.0 and G.sellable_units(100.0, 100.0, "XLM", pct=0.5) == 50.0)
big = [pos("XRP", 1000.0, 3.0), pos("ADA", 10.0, 0.3)]
o_xrp, n_xrp = run_plan(cfg(), big, cash=1000.0)
check("S2d plan(): XRP far over the 20% cap and at its floor is still NOT sold with the scope on",
      not sells(o_xrp, "XRP"), o_xrp)
for sym in G.HOLD_ONLY:
    # ABOVE its frozen floor (100 held against 40), like LINK on 2026-10-06. At the
    # floor, the floor alone would stop the sale and this check would prove
    # nothing -- the mutation that let R3 reach the three stayed green that way.
    o_h, _ = run_plan(cfg(), [pos(sym, 100.0, 1.0, "DOWN"), pos("XLM", 1000.0, 1.0)], cash=500.0,
                      base={sym: 40.0, "XLM": 1000.0})
    check(f"S2e plan(): {sym} below its line and ABOVE its floor is NOT reduced by R3 -- "
          "R3 is for open coins only", not sells(o_h, sym), o_h)

# ---- S3: R3 -- below the line, reduced toward the floor ----------------------------------
book = [pos("XLM", 1000.0, 0.22), pos("TOSHI", 280000.0, 0.000125, "DOWN"), pos("ADA", 170.0, 0.27)]
o3, n3 = run_plan(cfg(), book, cash=1000.0)            # cash well above the floor: only R3 can fire
t = sells(o3, "TOSHI")
check("S3a an open coin below its 200-day line is sold, under its R3 name",
      len(t) == 1 and t[0]["rule"].startswith("R3"), o3)
check("S3b ...one order no larger than max_order_usd ($25), though the position is $35",
      t and abs(t[0]["usd"] - 25.0) < 1e-9 and abs(t[0]["qty"] * t[0]["px"] - t[0]["usd"]) < 1e-6, t)
check("S3c ...and nothing else is sold when cash is above the floor", len(sells(o3)) == 1, o3)
o3s, _ = run_plan(cfg(), [pos("WLD", 3.0, 1.0, "DOWN"), pos("XLM", 1000.0, 1.0)], cash=500.0)
check("S3d a $3 position below its line is left, not sold under the $5 minimum", not sells(o3s, "WLD"), o3s)
o3r, _ = run_plan(cfg(scope=False), book, cash=1000.0)
check("S3e WITHOUT a scope the same coin is not sold -- R3 runs only on the operator's record",
      not sells(o3r, "TOSHI"), o3r)

# ---- S4: R1 -- the cash sleeve ---------------------------------------------------------------
# Every open coin under the 20% cap, so the concentration trim cannot fire and
# only R1 can sell: XRP 300 (23%, hold-only, at its floor), XLM 200, ADA 180,
# SOL 170, ATOM 160, DOGE 150, NEAR 140 -- a $1,300 book with no cash.
book4 = [pos("XRP", 200.0, 1.5), pos("XLM", 800.0, 0.25), pos("ADA", 1800.0, 0.10),
         pos("SOL", 1.0, 170.0), pos("ATOM", 40.0, 4.0), pos("DOGE", 1500.0, 0.10),
         pos("NEAR", 70.0, 2.0)]
o4, n4 = run_plan(cfg(), book4, cash=0.0)
s4 = sells(o4)
check("S4a cash under the floor sells the LARGEST open coin above its line first (XLM, then ADA)",
      [o["sym"] for o in s4] == ["XLM", "ADA"] and all(o["rule"] == "R1 cash sleeve" for o in s4), s4)
check("S4b ...each order at most $25, and no more orders than the day allows (2)",
      len(s4) == 2 and all(o["usd"] <= 25.0 + 1e-9 for o in s4), s4)
check("S4c ...never XRP, though it is the largest position",
      not sells(o4, "XRP"), o4)
small = [p for p in book4 if p["sym"] != "XRP"]       # $1,000 of open coins, each under 20%
cash_8 = (100.0 - 8.0) / 0.9                          # floor 10% of (1000 + c) minus c = $8 short
o4b, _ = run_plan(cfg(), small, cash=cash_8)
s4b = sells(o4b)
check("S4d a small shortfall sells only the shortfall ($8), not a full $25, and only one order",
      len(s4b) == 1 and abs(s4b[0]["usd"] - 8.0) < 0.01, s4b)
o4c, _ = run_plan(cfg(), book4, cash=1000.0)
check("S4e cash above the floor: R1 sells nothing", not [o for o in sells(o4c) if o["rule"] == "R1 cash sleeve"], o4c)

# ---- S6: a trim the caps would refuse is cut so it can happen --------------------------------
heavy = [pos("XLM", 2000.0, 0.25), pos("ADA", 1000.0, 0.10), pos("DOGE", 1000.0, 0.10)]
o6, n6 = run_plan(cfg(), heavy, cash=200.0)              # XLM $500 of $900: far over 20%
x6 = [o for o in sells(o6, "XLM") if o["rule"] == "R1 concentration cap"]
check("S6a with the scope, an open coin's concentration trim is ONE order of at most $25 -- "
      "one the caps will let through", len(x6) == 1 and abs(x6[0]["usd"] - 25.0) < 1e-9, o6)
o6r, _ = run_plan(cfg(scope=False), heavy, cash=200.0)
x6r = [o for o in sells(o6r, "XLM") if o["rule"] == "R1 concentration cap"]
check("S6b without it, the trim is proposed at full size, as before",
      len(x6r) == 1 and x6r[0]["usd"] > 25.0, o6r)
o6x, _ = run_plan(cfg(), [pos("LINK", 300.0, 10.0), pos("XLM", 1000.0, 0.25)], cash=500.0, base={"LINK": 100.0, "XLM": 1000.0})
l6 = sells(o6x, "LINK")
check("S6c ...and a hold-only coin's trim is NOT cut: LINK above its floor keeps the behaviour it had",
      len(l6) == 1 and l6[0]["usd"] > 25.0, o6x)

# ---- S5: no record, no change ---------------------------------------------------------------
o5, n5 = run_plan(cfg(scope=False), book4, cash=0.0)
check("S5a without a scope, cash under the floor sells NOTHING (the rule before it)", not sells(o5), o5)
check("S5b ...and says so in the words it always used",
      any("NOT auto-selling the shortfall" in n for n in n5), n5)
check("S5c ...and with a scope it does not say that, because it is no longer true",
      not any("NOT auto-selling the shortfall" in n for n in n4), n4)

n, ok = len(results), sum(results)
print(f"\nSW2 operation: {ok}/{n} passed")
sys.exit(0 if ok == n else 1)
