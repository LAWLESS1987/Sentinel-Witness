#!/usr/bin/env python3
"""H5 -- a config that leaves a gate out gets the shipped one, and no cap can be
raised above the ceiling written in code.

2026-11-12, hardening pass, item 5 of an independent review of this folder.

TWO DEFECTS.

  1. guards.load_config() returned the file's own keys and nothing else.
     guards.preconditions reads `seal_required` and `min_sealed_signals` with
     .get(), so a config that simply left them out had no seal requirement
     and no Rule 5. covenant_trader.load_config merged its defaults; this
     reader -- the one the sentinel path uses -- did not.
  2. The three caps were whatever the config said. $25 / $50 / 2 was a line in
     a gitignored file; 2500 / 5000 / 200 would have been obeyed the same way.
     And the defaults in code were $100 / $300 / 4, four times what the README
     and MY_STRATEGY.md state.

THE CEILINGS CHOSEN, AND WHY: $25 per order, $50 per day, 2 orders per day.
Those are the numbers this folder already states everywhere it states any:
README "Inside $25 per order, $50 per day, 2 orders per day"; MY_STRATEGY.md
Rule 6 "$25 per order"; guards.rule5_waiver's docstring; and
trader_config.example.json. Nothing looser is written down anywhere, so
nothing looser is allowed. Raising one is an edit to guards.HARD_CEILINGS.

  D1  one source of truth: the shipped defaults agree across both files, and
      the default caps ARE the ceilings.
  D2  a minimal config read from disk carries the shipped gates.
  D3  ...and they refuse: no seal, no Rule 5 record -> refused, where the same
      file used to clear. A config that satisfies them still clears.
  D4  the same for a config handed in as a dict.
  D5  a cap above its ceiling is refused with the key, the value and the
      ceiling -- and NOT clamped: an order the ceiling itself would allow is
      still refused under that config. A cap at or under it is honoured.
  D6  a cap that is not a finite number above zero is refused.
  D7  the cycle refuses on such a config, armed or not.
  D8  the two cap guards (what daily.py's morning report asks) block on it.
  D9  an unreadable config is the shipped defaults: disarmed.

WHAT THIS CANNOT SEE. Only the three caps have ceilings. max_position_pct,
min_cash_pct, weekly_fiat_budget_usd and the rest are still the config's to
set in either direction. Reads temp files only.
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

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []
NAN, INF = float("nan"), float("inf")


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


CEIL = getattr(G, "HARD_CEILINGS", {})
config_reasons = getattr(G, "config_reasons", lambda cfg: [])
TMP = tempfile.mkdtemp()
TODAY = time.strftime("%Y-%m-%d")
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))


def cfg_file(obj, name="cfg.json"):
    p = os.path.join(TMP, "%d_%s" % (len(os.listdir(TMP)), name))
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))
    return p


ORDER = {"side": "sell", "usd": 20.0, "sym": "XLM"}
FRESH = {"day": TODAY, "orders_today": [], "sealed_signals": 0}

# ---- D1 ------------------------------------------------------------------------
shipped = getattr(G, "SHIPPED_DEFAULTS", None)
check("D1a guards.SHIPPED_DEFAULTS is covenant_trader.DEFAULT_CONFIG, key for key",
      shipped == T.DEFAULT_CONFIG, {k: (shipped or {}).get(k, "ABSENT") for k in T.DEFAULT_CONFIG
                                    if (shipped or {}).get(k, "ABSENT") != T.DEFAULT_CONFIG[k]})
check("D1b the ceilings are $25 an order, $50 a day, 2 orders a day",
      CEIL == {"max_order_usd": 25.0, "max_daily_notional_usd": 50.0, "max_orders_per_day": 2}, CEIL)
check("D1c the default caps ARE the ceilings, in both files (they were $100 / $300 / 4)",
      all(G.CAP_DEFAULTS[k] == v and T.DEFAULT_CONFIG[k] == v for k, v in CEIL.items()) and CEIL,
      (G.CAP_DEFAULTS, {k: T.DEFAULT_CONFIG[k] for k in CEIL}))
check("D1d ...and with no config file at all, caps() answers with them",
      {k: G.caps(os.path.join(TMP, "none.json"))[k] for k in CEIL} == CEIL and CEIL)

# ---- D2 / D3: the config read from disk -----------------------------------------
minimal = cfg_file({"armed": True, "armed_until": UNTIL, "daily_plan_required": False})
got = G.load_config(minimal)
check("D2a a minimal config read from disk carries seal_required=true and min_sealed_signals=30",
      got.get("seal_required") is True and got.get("min_sealed_signals") == 30, got)
check("D2b ...and what the file did say is kept", got.get("armed") is True and got.get("daily_plan_required") is False)
rs = G.preconditions(ORDER, cfg=None, st=dict(FRESH), sealed_ok=False, guard_blocks=[],
                     config_path=minimal)
check("D3a under that file an UNSEALED order is refused as not sealed (it used to clear)",
      any("not sealed" in r for r in rs), rs)
check("D3b ...and refused by Rule 5: 0 sealed signals, need 30 (it used to clear)",
      any(r.startswith("Rule 5") and "need 30" in r for r in rs), rs)
satisfied = cfg_file({"armed": True, "armed_until": UNTIL, "daily_plan_required": False,
                      "min_sealed_signals": 0, "rule5_require_significance": False})
rs_ok = G.preconditions(ORDER, cfg=None, st=dict(FRESH), sealed_ok=True, guard_blocks=[],
                        config_path=satisfied)
check("D3c a config that states its own Rule 5 threshold, with the seal given, clears",
      rs_ok == [], rs_ok)

# ---- D4: the config handed in -----------------------------------------------------
NO_CFG = os.path.join(TMP, "no_such_config.json")
handed = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False}
rs = G.preconditions(ORDER, cfg=dict(handed), st=dict(FRESH), sealed_ok=False,
                     guard_blocks=[], config_path=NO_CFG)
check("D4a a config handed in as a dict with the same keys missing is refused the same two ways",
      any("not sealed" in r for r in rs) and any(r.startswith("Rule 5") for r in rs), rs)
rs = G.preconditions(ORDER, cfg=dict(handed, seal_required=False, min_sealed_signals=0,
                                     rule5_require_significance=False),
                     st=dict(FRESH), sealed_ok=False, guard_blocks=[], config_path=NO_CFG)
check("D4b ...and what a config DOES state is still what applies (a stated false is not overridden)",
      rs == [], rs)


# ---- D5: the ceilings -------------------------------------------------------------
def full(**over):
    c = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False,
         "seal_required": True, "min_sealed_signals": 0, "rule5_require_significance": False,
         "min_order_usd": 5.0, "max_order_usd": 25.0, "max_orders_per_day": 2,
         "max_daily_notional_usd": 50.0}
    c.update(over)
    return c


def pre(c, order=None):
    return G.preconditions(order or ORDER, cfg=c, st=dict(FRESH), sealed_ok=True,
                           guard_blocks=[], config_path=NO_CFG)


check("D5a a config AT the ceilings clears a $20 order", pre(full()) == [], pre(full()))
over = {"max_order_usd": 100.0, "max_daily_notional_usd": 300.0, "max_orders_per_day": 4}
let = []
for key, val in over.items():
    rs = pre(full(**{key: val}))
    named = [r for r in rs if r.startswith("config:") and key in r and "%g" % val in r
             and "%g" % CEIL.get(key, -1) in r and "ceiling" in r]
    if not named:
        let.append((key, rs))
check("D5b each cap raised above its ceiling refuses the order, naming the key, the value "
      "and the ceiling: $100/order, $300/day, 4 orders/day", not let, let)
check("D5c ...and it is REFUSED, NOT CLAMPED: under max_order_usd=100 even a $20 order -- "
      "which the ceiling itself would allow -- does not go", pre(full(max_order_usd=100.0)) != [])
low = pre(full(max_order_usd=10.0))
check("D5d a cap LOWERED below its ceiling is honoured: $20 is over the $10 placeable",
      any("placeable now" in r and "10.00" in r for r in low) and not any(r.startswith("config:") for r in low), low)
check("D5e ...as is a lowered order count: 1 a day, and the day's one order is used",
      any(r.startswith("per_day_cap:") for r in G.preconditions(
          ORDER, cfg=full(max_orders_per_day=1),
          st={"day": TODAY, "orders_today": [{"usd": 20.0, "side": "sell"}], "sealed_signals": 0},
          sealed_ok=True, guard_blocks=[], config_path=NO_CFG)))
above_file = cfg_file({"max_order_usd": 100.0})
rs = G.preconditions(ORDER, cfg={k: v for k, v in full().items() if k != "max_order_usd"},
                     st=dict(FRESH), sealed_ok=True, guard_blocks=[], config_path=above_file)
check("D5f a cap that reaches preconditions through the FILE fallback is held to the ceiling too",
      any(r.startswith("config:") and "max_order_usd" in r for r in rs), rs)

# ---- D6: not a number ---------------------------------------------------------------
bad = {"NaN": NAN, "+inf": INF, "zero": 0, "negative": -25.0, "True": True, "the string '25'": "25", "None": None}
let = [(k, key) for key in CEIL for k, v in bad.items() if not config_reasons({key: v})]
check("D6a each cap set to " + ", ".join(bad) + " is refused", not let and CEIL, let)
check("D6b ...and 1.5 orders a day is not a number of orders",
      bool(config_reasons({"max_orders_per_day": 1.5})))
check("D6c through preconditions: max_daily_notional_usd = NaN refuses the order (NaN is never "
      "reached by any day's total, so it used to be no cap at all)",
      any(r.startswith("config:") and "max_daily_notional_usd" in r
          for r in pre(full(max_daily_notional_usd=NAN))), pre(full(max_daily_notional_usd=NAN)))
check("D6d a config that simply leaves the caps out is NOT refused for it (it takes the defaults)",
      config_reasons({"armed": False}) == [] and bool(CEIL))

# ---- D7: the cycle -----------------------------------------------------------------
home = tempfile.mkdtemp()
open(os.path.join(home, "covenant_unified_v8.py"), "w").close()
os.makedirs(os.path.join(home, "private"))
rp = os.path.join(home, "private", "RESERVE.json")
open(rp, "w").write("{}")
for armed in (True, False):
    c = dict(T.DEFAULT_CONFIG, covenant_home=home, armed=armed, armed_until=UNTIL, max_order_usd=100.0)
    why = T.witness_ready(c, reserve=rp)
    check("D7%s a cycle on a config with max_order_usd=100 is refused before it begins (armed=%s)"
          % ("a" if armed else "b", armed), bool(why) and "max_order_usd" in why and "ceiling" in why, why)
ok_cfg = dict(T.DEFAULT_CONFIG, covenant_home=home, armed=False)
check("D7c ...and the shipped defaults are not refused by this check",
      T.witness_ready(ok_cfg, reserve=rp) is None, T.witness_ready(ok_cfg, reserve=rp))

# ---- D8: the cap guards --------------------------------------------------------------
st = G.State(equity_now=0.0, equity_peak=0.0, equity_start_of_day=0.0, closed_trades=[],
             last_sold={}, positions={}, cash=0.0, orders_today=[])
high = cfg_file({"max_order_usd": 100.0, "max_daily_notional_usd": 300.0, "max_orders_per_day": 4})
at = cfg_file({"max_order_usd": 25.0, "max_daily_notional_usd": 50.0, "max_orders_per_day": 2})
check("D8a PerTradeCap and PerDayCap, reading a config above the ceilings, BLOCK and say why",
      not G.PerTradeCap(config_path=high).check(st).allowed
      and "ceiling" in G.PerTradeCap(config_path=high).check(st).reason
      and not G.PerDayCap(config_path=high).check(st).allowed)
check("D8b ...and reading one at the ceilings, allow",
      G.PerTradeCap(config_path=at).check(st).allowed and G.PerDayCap(config_path=at).check(st).allowed)

# ---- D9 --------------------------------------------------------------------------------
for label, content in (("not JSON", "{armed: true"), ("a list", "[1]")):
    got = G.load_config(cfg_file(content))
    check("D9 an unreadable config (%s) is the shipped defaults: disarmed, sealed, Rule 5 on" % label,
          got.get("armed") is False and got.get("seal_required") is True
          and got.get("min_sealed_signals") == 30, got)

n = len(results)
print(f"\nH5 config: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
