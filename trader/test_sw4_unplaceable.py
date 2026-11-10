#!/usr/bin/env python3
"""SW4 -- an order the cap will refuse does not take a slot or count as cash.

2026-11-09. Found in the first plan-only cycle after SW3, on the operator's
real book. The plan proposed selling the LINK held above its frozen floor under
the concentration cap. That is designed: hold-only means the floor is
untouchable, and what sits above it is tradeable. For the hold-only three the
trim is deliberately NOT cut to one capped order, so it is proposed at full
size and guards.preconditions refuses it as over max_order_usd, every day.

THE DEFECT was what that never-placed order did to the rest of the plan:

  * it used one of the day's order slots, so one fewer coin that CAN be sold
    was planned;
  * its dollars were subtracted from the cash shortfall, so the plan said the
    cash floor was "covered" by a sale that would not happen, and the R1 cash
    sleeve did not run;
  * the same dollars counted as cash on hand for the R6 contribution, which
    could then plan a BUY with new money while cash was really under the floor.

The operator chose (2026-11-09, "Yes, fix that") to stop the order distorting
the plan, and NOT to start cutting the three's trims to $25: LINK is still
never sold by this. The order is still proposed, shown and sealed, now marked
`unplaceable` with the reason.

  U1  the hold-only trim over the cap is still proposed, and marked.
  U2  it takes no slot: two placeable sells are planned beside it.
  U3  it is not cash: the shortfall is not called covered.
  U4  it is not cash for R6 either: no buy while cash is under the floor.
  U5  a hold-only trim INSIDE the cap is unmarked and counts, as before.
  U6  with no scope record, plan() is what it was.
  U7  the gate does refuse the marked order, so "unplaceable" is true.

WHAT THIS CANNOT SEE. One population: plan() under a trading_scope record,
with stubbed floors and a stubbed book. With no scope record the older note
still adds up trims the cap would refuse (U6 pins that it is unchanged, not
that it is right). Places nothing, opens no credential.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:400]}"), flush=True)


SCOPE = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06",
         "words": "Everything else is good to sell in trade."}
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")


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


def run_plan(c, positions, cash, base):
    held = {p["sym"]: p["qty"] for p in positions}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: (dict(base), held, [])
        total = sum(p["val"] for p in positions) + cash
        return T.plan(c, {"positions": positions, "cash": cash, "total": total,
                          "unpriced": [], "venue_notes": []})
    finally:
        T.reserve_baseline = keep


def by(orders, sym):
    return [o for o in orders if o["sym"] == sym]


# The shape of 2026-11-09: LINK is most of the book and well above its frozen
# floor; one open coin is below its line, two are above; there is no cash.
# Total $1,330, so the 10% cash floor is $133 and the 20% cap is $266.
# LINK: $1,000 held, floor 400 units -> $600 above the floor, far over $25.
BOOK = [pos("LINK", 1000.0, 1.0), pos("JASMY", 30.0, 1.0, "DOWN"),
        pos("XLM", 150.0, 1.0), pos("ADA", 150.0, 1.0)]
BASE = {"LINK": 400.0, "JASMY": 30.0, "XLM": 150.0, "ADA": 150.0}
orders, notes = run_plan(cfg(), BOOK, cash=0.0, base=BASE)
link = by(orders, "LINK")

# ---- U1 ------------------------------------------------------------------------
check("U1a the hold-only trim above the floor is still proposed (LINK is not hidden)",
      len(link) == 1 and link[0]["side"] == "sell" and abs(link[0]["usd"] - 600.0) < 1e-6, link)
check("U1b ...at exactly what sits above the frozen floor, never into it",
      link and abs(link[0]["qty"] - 600.0) < 1e-6, link)
check("U1c ...and marked unplaceable, with the reason in words",
      link and isinstance(link[0].get("unplaceable"), str) and "25.00" in link[0]["unplaceable"], link)
check("U1d a note says so in the plan", any("LINK" in n and "no order slot" in n for n in notes), notes)

# ---- U2 ------------------------------------------------------------------------
placeable = [o for o in orders if not o.get("unplaceable")]
check("U2a the marked order takes no slot: TWO placeable sells are planned beside it",
      len(placeable) == 2 and all(o["side"] == "sell" for o in placeable), orders)
check("U2b ...the coin below its line under R3, then the cash sleeve under R1",
      [o["rule"][:2] for o in placeable] == ["R3", "R1"] and placeable[0]["sym"] == "JASMY", placeable)
check("U2c ...each inside the order cap", all(o["usd"] <= 25.0 + 1e-9 for o in placeable), placeable)
check("U2d no open coin is marked", not any(o.get("unplaceable") for o in orders if o["sym"] != "LINK"), orders)

# ---- U3 ------------------------------------------------------------------------
cash_notes = [n for n in notes if n.startswith("R1 cash floor")]
check("U3a the cash-floor note counts only what can be placed: $50 of $133",
      len(cash_notes) == 1 and "raise $50 of $133" in cash_notes[0], cash_notes)
check("U3b ...and does NOT call the shortfall covered", cash_notes and "covered" not in cash_notes[0], cash_notes)

# ---- U4 ------------------------------------------------------------------------
# Cash $100 against a $140 floor. Counting the $600 LINK order as cash made
# "spare" positive and R6 planned a fiat buy.
BOOK4 = [pos("LINK", 1000.0, 1.0), pos("XLM", 150.0, 1.0), pos("ADA", 150.0, 1.0)]
BASE4 = {"LINK": 400.0, "XLM": 150.0, "ADA": 150.0}
o4, n4 = run_plan(cfg(allow_fiat_buys=True, weekly_fiat_budget_usd=100.0), BOOK4, cash=100.0, base=BASE4)
check("U4a no BUY is planned while cash is under the floor", not [o for o in o4 if o["side"] == "buy"], o4)
check("U4b ...the cash sleeve sells instead, by the real $40 shortfall",
      abs(sum(o["usd"] for o in o4 if not o.get("unplaceable")) - 40.0) < 1e-6, o4)

# U4c, U4d ADDED after mutation (2026-11-09). With R6's own two reads put back
# to counting every order, U4a and U4b stayed green: in the book above the cash
# sleeve fills both slots first, so R6 never got far enough to show its own
# arithmetic. Here the open coins are under the $5 minimum, so the sleeve
# cannot sell and R6 is the only rule left to run.
BOOK4C = [pos("LINK", 1000.0, 1.0), pos("XLM", 4.0, 1.0), pos("ADA", 4.0, 1.0)]
BASE4C = {"LINK": 400.0, "XLM": 4.0, "ADA": 4.0}
o4c, n4c = run_plan(cfg(allow_fiat_buys=True, weekly_fiat_budget_usd=100.0), BOOK4C, cash=100.0, base=BASE4C)
check("U4c with nothing the sleeve can sell and cash $100 under a $110.80 floor: R6 buys NOTHING",
      not [o for o in o4c if o["side"] == "buy"]
      and any(n.startswith("R6 contribution") and "stays as cash" in n for n in n4c), (o4c, n4c))
o4d, n4d = run_plan(cfg(allow_fiat_buys=True, weekly_fiat_budget_usd=100.0), BOOK4C, cash=300.0, base=BASE4C)
check("U4d with cash truly over the floor, R6 has BOTH slots: the marked order holds none of them",
      len([o for o in o4d if o["side"] == "buy"]) == 2, o4d)

# ---- U5: both ways -- a hold-only trim INSIDE the cap still counts ------------------
BASE5 = dict(BASE, LINK=990.0)                       # only $10 above the floor
o5, n5 = run_plan(cfg(), BOOK, cash=0.0, base=BASE5)
l5 = by(o5, "LINK")
check("U5a a $10 hold-only trim is proposed and NOT marked", len(l5) == 1 and not l5[0].get("unplaceable")
      and abs(l5[0]["usd"] - 10.0) < 1e-6, l5)
check("U5b ...it takes a slot: one more sell beside it, not two", len(o5) == 2, o5)
c5 = [n for n in n5 if n.startswith("R1 cash floor")]
check("U5c ...and its $10 counts as cash raised: $35 of $133", c5 and "raise $35 of $133" in c5[0], c5)

# ---- U6: no scope record, nothing changes -----------------------------------------
o6, n6 = run_plan(cfg(scope=False), BOOK, cash=0.0, base=BASE)
check("U6a with no scope the LINK trim is proposed unmarked, as it always was",
      len(by(o6, "LINK")) == 1 and "unplaceable" not in by(o6, "LINK")[0], o6)
check("U6b ...and the older note is the one printed", any("cap trims raise" in n for n in n6)
      or any("the cap trims raise" in n for n in n6), n6)

# ---- U7: the gate does refuse it ---------------------------------------------------
gate_cfg = dict(cfg(), armed=True, daily_plan_required=False, seal_required=True,
                min_sealed_signals=0, rule5_require_significance=False,
                max_daily_notional_usd=50.0)
rs = G.preconditions({"side": "sell", "usd": link[0]["usd"] if link else 600.0, "sym": "LINK"},
                     cfg=gate_cfg, st={"orders_today": [], "sealed_signals": 0},
                     sealed_ok=True, guard_blocks=[], config_path=NO_CFG)
check("U7a guards.preconditions refuses the marked order as over the cap",
      any("placeable now" in r for r in rs), rs)
rs_ok = G.preconditions({"side": "sell", "usd": 25.0, "sym": "JASMY"},
                        cfg=gate_cfg, st={"orders_today": [], "sealed_signals": 0},
                        sealed_ok=True, guard_blocks=[], config_path=NO_CFG)
check("U7b ...and passes a placeable one with the same config (the refusal is the cap, not the fixture)",
      rs_ok == [], rs_ok)

n = len(results)
print(f"\nSW4 unplaceable: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
