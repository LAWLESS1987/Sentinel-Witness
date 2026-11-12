#!/usr/bin/env python3
"""R4 -- every gate-relevant number in the config is held on its safe side.

2026-11-12, review round one.

THE DEFECT. H5 gave three caps a ceiling in code. Every other number in
trader_config.json was still the config's to set in either direction:
max_position_pct 0.9 switched the concentration rule off; min_cash_pct 0
switched the cash floor off in the planner; weekly_fiat_budget_usd 100000 was
a weekly budget; allow_fiat_buys "false" (the text) was truthy, and bought.

THE BOUNDS, and where each number is written:

  max_position_pct            above 0, at most 0.20   MY_STRATEGY.md Rule 1:
                                                      "No single coin above 20%"
  min_cash_pct                at least 0.10, below 1  README: "the 10% cash floor"
  contribution_min_cash_pct   at least 0.10, below 1  MY_STRATEGY.md Rule 6
  weekly_fiat_budget_usd      0 to 100                MY_STRATEGY.md Rule 6:
                                                      "a budget of 100 a week"
  allow_fiat_buys             exactly true or false
  symbols, contribution_symbols   a list of symbols

  B  each bound: inside is accepted, outside is refused naming the key, the
     value and the range; a value that is not a number is refused.
  P  through guards.preconditions: an order under such a config is refused --
     the value is not clamped to its bound.
  W  the cycle refuses on such a config, armed or not.
  F  the fiat permission is the boolean true and nothing that looks like it.
  K  EVERY gate-relevant key (guards.GATE_KEYS) is accounted for in
     guards.CONFIG_KEY_NOTES, as bounded (and by what) or as needing none (and
     why) -- and each one said to be bounded is refused when driven past it.
  D  the shipped defaults and the example config are inside every bound.

WHAT THIS CANNOT SEE. The guards' own thresholds (drawdown 25%, daily loss 8%,
cooldown 7 days, the 50% reserve and buy budget) are constants in guards.py
and not in the config at all; an edit to that file is not a config change.
The floors and the starting book are covenant's private/RESERVE.json, an
operator's file by design. Temp files only.
"""
from __future__ import annotations

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
import covenant_trader as T  # noqa: E402

results = []
NAN, INF = float("nan"), float("inf")


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


BOUNDS = getattr(G, "CONFIG_BOUNDS", {})
NOTES = getattr(G, "CONFIG_KEY_NOTES", {})
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")
TODAY = time.strftime("%Y-%m-%d")


def refused(key, val):
    return [r for r in G.config_reasons({key: val}) if key in r]


# ---- B: each bound -------------------------------------------------------------------
cases = {
    "max_position_pct": ([0.20, 0.15, 0.01], [0.21, 0.9, 1.0, 0.0, -0.1]),
    "min_cash_pct": ([0.10, 0.25, 0.99], [0.0999, 0.05, 0.0, -0.1, 1.0, 5.0]),
    "contribution_min_cash_pct": ([0.10, 0.5], [0.09, 0.0, 1.0]),
    "weekly_fiat_budget_usd": ([0.0, 50.0, 100.0], [100.01, 1000.0, -1.0]),
}
let_in = {k: [v for v in good if refused(k, v)] for k, (good, bad) in cases.items()}
let_out = {k: [v for v in bad if not refused(k, v)] for k, (good, bad) in cases.items()}
check("B1 inside its range each value is accepted: " + "; ".join("%s %s" % (k, g) for k, (g, b) in cases.items()),
      not any(let_in.values()), let_in)
check("B2 outside its range each is REFUSED: " + "; ".join("%s %s" % (k, b) for k, (g, b) in cases.items()),
      not any(let_out.values()), let_out)
r = refused("max_position_pct", 0.9)
check("B3 the refusal names the key, the value, the range and where the number is written",
      len(r) == 1 and "0.9" in r[0] and "0.2" in r[0] and "MY_STRATEGY" in r[0] and "not clamped" in r[0], r)
junk = [NAN, INF, "0.2", None, True, [0.2]]
let = {k: [repr(v) for v in junk if not refused(k, v)] for k in cases}
check("B4 a value that is NaN, infinite, text, null, a boolean or a list is refused for every one "
      "of them", not any(let.values()), let)
check("B5 a config that leaves these keys out is not refused for it (it takes the shipped values)",
      G.config_reasons({"armed": False}) == [])
check("B6 the bounds are these four, with these edges",
      {k: v[:4] for k, v in BOUNDS.items()} == {
          "max_position_pct": (0.0, True, 0.20, False), "min_cash_pct": (0.10, False, 1.0, True),
          "contribution_min_cash_pct": (0.10, False, 1.0, True),
          "weekly_fiat_budget_usd": (0.0, False, 100.0, False)}, BOUNDS)


# ---- P: through preconditions ------------------------------------------------------------
def cfg(**over):
    c = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False, "seal_required": True,
         "min_sealed_signals": 0, "rule5_require_significance": False, "min_order_usd": 5.0,
         "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}
    c.update(over)
    return c


ORDER = {"side": "sell", "usd": 20.0, "sym": "XLM"}


def pre(c):
    return G.preconditions(ORDER, cfg=c, st={"day": TODAY, "orders_today": []}, sealed_ok=True,
                           guard_blocks=[], config_path=NO_CFG)


check("P0 the fixture is clear", pre(cfg()) == [], pre(cfg()))
let = {}
for key, val in (("max_position_pct", 0.9), ("min_cash_pct", 0.0), ("contribution_min_cash_pct", 0.0),
                 ("weekly_fiat_budget_usd", 5000.0), ("allow_fiat_buys", "true"), ("symbols", "XLM")):
    rs = pre(cfg(**{key: val}))
    if not [r for r in rs if r.startswith("config:") and key in r]:
        let[key] = rs
check("P1 an order under a config with any one of these past its bound is REFUSED, in the "
      "config's name: max_position_pct 0.9, min_cash_pct 0, contribution_min_cash_pct 0, "
      "weekly_fiat_budget_usd 5000, allow_fiat_buys 'true', symbols 'XLM'", not let, let)
check("P2 ...and at the bound it clears (the refusal is the bound, not the key)",
      pre(cfg(max_position_pct=0.20, min_cash_pct=0.10, weekly_fiat_budget_usd=100.0)) == [])

# ---- W: the cycle --------------------------------------------------------------------------
home = tempfile.mkdtemp()
open(os.path.join(home, "covenant_unified_v8.py"), "w").close()
os.makedirs(os.path.join(home, "private"))
rp = os.path.join(home, "private", "RESERVE.json")
open(rp, "w").write("{}")
keep_pin = W.PINNED_COVENANT_HOME
try:
    W.PINNED_COVENANT_HOME = home
    for armed in (True, False):
        c = dict(T.DEFAULT_CONFIG, covenant_home=home, armed=armed, armed_until=UNTIL, min_cash_pct=0.0)
        why = T.witness_ready(c, reserve=rp)
        check("W1 a cycle on a config with min_cash_pct 0 is refused before it begins (armed=%s)" % armed,
              bool(why) and "min_cash_pct" in why, why)
    ok_cfg = dict(T.DEFAULT_CONFIG, covenant_home=home)
    check("W2 ...and the shipped defaults are not", T.witness_ready(ok_cfg, reserve=rp) is None,
          T.witness_ready(ok_cfg, reserve=rp))
finally:
    W.PINNED_COVENANT_HOME = keep_pin


# ---- F: the fiat permission ------------------------------------------------------------------
def run_plan(allow):
    positions = [{"sym": "XLM", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "UP", "at": {"f": 100.0}}]
    held = {"XLM": 100.0}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: (dict(held), held, [])
        c = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0, "min_order_usd": 5.0,
             "max_orders_per_day": 2, "allow_fiat_buys": allow, "weekly_fiat_budget_usd": 100.0}
        o, n = T.plan(c, {"positions": positions, "cash": 900.0, "total": 1000.0, "unpriced": [],
                          "venue_notes": []}, week_spent=0.0)
        return [x for x in o if x["side"] == "buy"]
    finally:
        T.reserve_baseline = keep


check("F1 allow_fiat_buys true: the weekly contribution plans its buy", len(run_plan(True)) == 1)
let = [repr(v) for v in ("false", "true", 1, "no", [True]) if run_plan(v)]
check("F2 allow_fiat_buys 'false', 'true', 1, 'no', [true] -- truthy, and none of them the boolean "
      "true: NO buy is planned (the text 'false' used to buy)", not let, let)
let = [repr(v) for v in ("false", "true", 1, None) if not refused("allow_fiat_buys", v)]
check("F3 ...and each is refused as a config value", not let, let)
let = [repr(v) for v in ("XLM", [1], [""], {"XLM": 1}) if not refused("symbols", v) or not refused("contribution_symbols", v)]
check("F4 symbols / contribution_symbols that are not a list of symbols are refused; a list is not",
      not let and not refused("symbols", ["XLM", "ADA"]) and not refused("contribution_symbols", []), let)

# ---- K: every gate-relevant key is accounted for -----------------------------------------------
gate_keys = set(getattr(G, "GATE_KEYS", ()))
check("K1 every gate-relevant key has a note saying what holds it, and no note is for a key "
      "that is not one: %d keys" % len(gate_keys), gate_keys and set(NOTES) == gate_keys,
      sorted(gate_keys ^ set(NOTES)))
check("K2 each note says 'bound' (and by what) or 'no bound' (and why)",
      NOTES and all(n.startswith(("bound", "no bound")) and len(n) > 25 for n in NOTES.values()),
      {k: v for k, v in NOTES.items() if not v.startswith(("bound", "no bound"))})
# Each key said to be bounded, driven past its bound, must be refused by the
# function that note names. An armed config, all else at the shipped values.
armed = dict(T.DEFAULT_CONFIG, armed=True, armed_until=UNTIL, covenant_home=home)
past = {
    "armed_until": lambda c: not G.arming(dict(c, armed_until="2099-01-01T00:00:00Z"))[0],
    "max_order_usd": lambda c: G.config_reasons(dict(c, max_order_usd=26.0)),
    "max_daily_notional_usd": lambda c: G.config_reasons(dict(c, max_daily_notional_usd=51.0)),
    "max_orders_per_day": lambda c: G.config_reasons(dict(c, max_orders_per_day=3)),
    "max_position_pct": lambda c: G.config_reasons(dict(c, max_position_pct=0.21)),
    "min_cash_pct": lambda c: G.config_reasons(dict(c, min_cash_pct=0.09)),
    "seal_required": lambda c: W.terms_reasons(dict(c, seal_required=False)),
    "min_sealed_signals": lambda c: G.live_terms_reasons(dict(c, min_sealed_signals=29)),
    "rule5_require_significance": lambda c: G.live_terms_reasons(dict(c, rule5_require_significance=False)),
    "daily_plan_required": lambda c: G.live_terms_reasons(dict(c, daily_plan_required=False)),
    "rule5_waiver": lambda c: G.live_terms_reasons(dict(c, rule5_waiver={"waived": True, "by": "x", "at": "x", "words": "x"})),
    "trading_scope": lambda c: G.live_terms_reasons(dict(c, trading_scope={"open_reserve_pct": 0.49, "by": "x", "at": "x", "words": "x"})),
    "weekly_fiat_budget_usd": lambda c: G.config_reasons(dict(c, weekly_fiat_budget_usd=101.0)),
    "contribution_min_cash_pct": lambda c: G.config_reasons(dict(c, contribution_min_cash_pct=0.09)),
    "covenant_home": lambda c: W.home_reasons(dict(c, covenant_home=tempfile.mkdtemp())),
}
bounded = {k for k, n in NOTES.items() if n.startswith("bound")}
try:
    W.PINNED_COVENANT_HOME = home
    not_driven = sorted(bounded - set(past))
    not_refused = sorted(k for k in bounded & set(past) if not past[k](armed))
finally:
    W.PINNED_COVENANT_HOME = keep_pin
check("K3 every key the notes call bounded (%d of them) is driven past its bound here -- none is "
      "bounded on paper only" % len(bounded), bounded and not not_driven, not_driven)
check("K4 ...and each one, past its bound, IS refused", not not_refused, not_refused)
check("K5 the keys with no bound are the five that can only tighten, narrow or are a plain "
      "permission: armed, min_order_usd, allow_fiat_buys, symbols, contribution_symbols",
      set(NOTES) - bounded == {"armed", "min_order_usd", "allow_fiat_buys", "symbols", "contribution_symbols"},
      sorted(set(NOTES) - bounded))

# ---- D: the shipped values ------------------------------------------------------------------------
check("D1 the shipped defaults are inside every bound", G.config_reasons(dict(G.SHIPPED_DEFAULTS)) == [],
      G.config_reasons(dict(G.SHIPPED_DEFAULTS)))
example = json.load(open(os.path.join(HERE, "trader_config.example.json"), encoding="utf-8"))
check("D2 ...and so is trader_config.example.json, the config an operator copies",
      G.config_reasons(example) == [], G.config_reasons(example))

n = len(results)
print(f"\nR4 config bounds: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
