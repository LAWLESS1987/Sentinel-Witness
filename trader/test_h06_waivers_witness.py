#!/usr/bin/env python3
"""H6 -- armed, no config line removes a gate or chooses the witness.

2026-11-12, hardening pass, item 6 of an independent review of this folder.

THE DEFECT. Three things in this trader's own gitignored config each removed
something that stands between a plan and a live order:

  * `rule5_waiver` removed Rule 5;
  * `trading_scope.open_reserve_pct` removed the 50% reserve on every coin but
    the hold-only three (it can be 0);
  * `covenant_home` -- or the environment variable SENTINEL_COVENANT_HOME --
    chose the FOLDER whose Python seals the decision, answers "is today
    approved", and holds the floor file and the halt file. witness._python
    runs that folder's own .venv interpreter.

And three plain switches did the same with no record at all:
daily_plan_required=false, min_sealed_signals below 30, and
rule5_require_significance=false.

WHAT CHANGED. Disarmed, all of it still works -- a rehearsal places nothing --
and the cycle prints REHEARSAL ONLY with each term named. Armed:

  A  guards.preconditions does not honour the waiver: it says so and applies
     Rule 5. Disarmed it is honoured as before.
  B  guards.live_terms_reasons names each term an armed config may not carry;
     a config with the shipped gates has none.
  C  covenant_trader.plan plans nothing on a reserve under the shipped 50%;
     disarmed, and at 50% or more, it plans.
  D  the witness folder must be PINNED -- witness.PINNED_COVENANT_HOME in
     code, or the one line of witness.PIN_FILE, a file in a folder the trader
     never writes. No pin, or an environment/config value naming another
     folder, refuses; the seal and the approval gate then run NO Python at all.
  E  the cycle refuses on any of these before it reads anything, and a
     disarmed cycle prints REHEARSAL ONLY.
  F  a config read from disk by preconditions itself is held to the same.

WHAT THIS CANNOT SEE, AND WHAT IT COSTS. The pin file belongs to the same OS
user as everything else: this closes the one-line road, not the account. The
terms are checked for the cycle (witness_ready) and for a config preconditions
reads from disk; a Python caller that hands preconditions() its own dict is
held to A and to nothing else in B, as with SW3's term. And it REVERSES, for
armed runs, two recorded operator decisions (the waiver of 2026-10-05 and the
scope of 2026-10-06): with them in the config an armed cycle now refuses.
Temp folders and stub runners only; starts no real covenant process except a
stand-in written by this file.
"""
from __future__ import annotations

import contextlib
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
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


live_terms = getattr(G, "live_terms_reasons", lambda cfg, hypothetical=False: [])
home_reasons = getattr(W, "home_reasons", lambda cfg, hypothetical=False: [])
NOT_HONOURED = getattr(G, "WAIVER_NOT_HONOURED", "armed with a Rule 5 waiver")
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")
TODAY = time.strftime("%Y-%m-%d")
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
WAIVER = {"waived": True, "by": "operator", "at": "2026-10-05", "words": "Trade now within caps"}
SCOPE0 = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06",
          "words": "Everything else is good to sell in trade."}


def gate_cfg(**over):
    """Armed, with the gates other files test switched to their clear values
    EXCEPT the ones this file is about."""
    c = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False, "seal_required": True,
         "min_sealed_signals": 30, "rule5_require_significance": True, "min_order_usd": 5.0,
         "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}
    c.update(over)
    return c


ORDER = {"side": "sell", "usd": 20.0, "sym": "XLM"}
SEVEN = {"day": TODAY, "orders_today": [], "sealed_signals": 7,
         "rule5": {"clears": False, "why": "7 settled, 0 wins"}}
CLEARED = {"day": TODAY, "orders_today": [], "sealed_signals": 30, "rule5": {"clears": True}}


def pre(c, st):
    return G.preconditions(ORDER, cfg=c, st=dict(st), sealed_ok=True, guard_blocks=[], config_path=NO_CFG)


# ---- A: the waiver at the gate -----------------------------------------------------
armed_w = pre(gate_cfg(rule5_waiver=WAIVER), SEVEN)
check("A1a ARMED with a complete waiver and 7 of 30 signals: Rule 5 REFUSES (it used to clear)",
      any(r.startswith("Rule 5") and "need 30" in r for r in armed_w), armed_w)
check("A1b ...and the refusal says the waiver is there and is not honoured while armed",
      NOT_HONOURED in armed_w, armed_w)
dis_w = pre(gate_cfg(armed=False, rule5_waiver=WAIVER), SEVEN)
check("A2 DISARMED with the same waiver: Rule 5 does not refuse (rehearsal), and the order is "
      "refused as disarmed", not any(r.startswith("Rule 5") for r in dis_w)
      and any(r.startswith("armed=false") for r in dis_w) and NOT_HONOURED not in dis_w, dis_w)
check("A3 ARMED with NO waiver and a Rule 5 record that clears: nothing refuses",
      pre(gate_cfg(), CLEARED) == [], pre(gate_cfg(), CLEARED))
check("A4 ARMED with the waiver even when Rule 5 clears on its own: the waiver is still named "
      "(an armed config may not carry one)", NOT_HONOURED in pre(gate_cfg(rule5_waiver=WAIVER), CLEARED))

# ---- B: the armed terms --------------------------------------------------------------
shipped = dict(T.DEFAULT_CONFIG, armed=True)
check("B0 an armed config with the shipped gates carries no refused term",
      live_terms(shipped) == [], live_terms(shipped))
terms = {
    "a Rule 5 waiver": ({"rule5_waiver": WAIVER}, "waiver"),
    "open_reserve_pct 0": ({"trading_scope": SCOPE0}, "open_reserve_pct"),
    "open_reserve_pct 0.49": ({"trading_scope": dict(SCOPE0, open_reserve_pct=0.49)}, "open_reserve_pct"),
    "daily_plan_required false": ({"daily_plan_required": False}, "daily_plan_required"),
    "min_sealed_signals 0": ({"min_sealed_signals": 0}, "min_sealed_signals"),
    "min_sealed_signals 29": ({"min_sealed_signals": 29}, "min_sealed_signals"),
    "min_sealed_signals the text '30'": ({"min_sealed_signals": "30"}, "min_sealed_signals"),
    "min_sealed_signals true": ({"min_sealed_signals": True}, "min_sealed_signals"),
    "rule5_require_significance false": ({"rule5_require_significance": False}, "rule5_require_significance"),
}
let = [k for k, (over, needle) in terms.items()
       if not [r for r in live_terms(dict(shipped, **over)) if needle in r]]
check("B1 ARMED, each of these is a named refusal: " + "; ".join(terms), not let, let)
let = [k for k, (over, _n) in terms.items() if live_terms(dict(shipped, armed=False, **over))]
check("B2 DISARMED, none of them is (a rehearsal keeps the looser behaviour)", not let, let)
let = [k for k, (over, needle) in terms.items()
       if not [r for r in live_terms(dict(shipped, armed=False, **over), hypothetical=True) if needle in r]]
check("B3 ...and asked hypothetically, each is named again -- which is what REHEARSAL ONLY prints",
      not let, let)
check("B4 ARMED with a reserve AT or ABOVE the shipped 50% (0.5, 0.8): not refused",
      live_terms(dict(shipped, trading_scope=dict(SCOPE0, open_reserve_pct=0.5))) == []
      and live_terms(dict(shipped, trading_scope=dict(SCOPE0, open_reserve_pct=0.8))) == [])
check("B5 ARMED with min_sealed_signals RAISED to 60: not refused (a config may tighten)",
      live_terms(dict(shipped, min_sealed_signals=60)) == [])


# ---- C: plan() and the reserve ---------------------------------------------------------
def run_plan(c):
    positions = [{"sym": "JASMY", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "DOWN", "at": {"f": 100.0}},
                 {"sym": "XLM", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "UP", "at": {"f": 100.0}}]
    held = {p["sym"]: p["qty"] for p in positions}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: (dict(held), held, [])
        base = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0,
                "min_order_usd": 5.0, "max_orders_per_day": 2, "allow_fiat_buys": False,
                "weekly_fiat_budget_usd": 0.0}
        base.update(c)
        return T.plan(base, {"positions": positions, "cash": 800.0, "total": 1000.0,
                             "unpriced": [], "venue_notes": []})
    finally:
        T.reserve_baseline = keep


o, n = run_plan({"armed": True, "trading_scope": SCOPE0})
check("C1 ARMED with open_reserve_pct 0: plan() plans NO orders and says the reserve is under "
      "the shipped floor", o == [] and len(n) == 1 and "RESERVE UNDER THE SHIPPED FLOOR" in n[0], (o, n))
o, n = run_plan({"armed": False, "trading_scope": SCOPE0})
check("C2 DISARMED with the same scope: the rehearsal plans the R3 sale of the coin below its line",
      [x["sym"] for x in o] == ["JASMY"] and o[0]["rule"].startswith("R3"), (o, n))
o, n = run_plan({"armed": True, "trading_scope": dict(SCOPE0, open_reserve_pct=0.5)})
check("C3 ARMED at the shipped 50%: it plans, inside the reserve ($25 of the $50 sellable)",
      [x["sym"] for x in o] == ["JASMY"] and abs(o[0]["usd"] - 25.0) < 1e-9, (o, n))


# ---- D: the pinned witness folder -------------------------------------------------------
def fake_covenant(who="covenant"):
    d = tempfile.mkdtemp()
    open(os.path.join(d, "covenant_unified_v8.py"), "w").close()
    with open(os.path.join(d, "covenant_trader.py"), "w", encoding="utf-8") as fh:
        fh.write('DEFAULT_CONFIG = {"node_ports": [5000], "node_key": "k.key"}\n'
                 'def seal_decision_result(cfg, record):\n'
                 '    return {"ok": True, "status": 200, "admission": "admitted", "tx_id": "t",\n'
                 '            "detail": "x", "mined": "", "who": %r}\n' % who)
    open(os.path.join(d, "k.key"), "w").close()
    return d


PINNED, OTHER = fake_covenant("pinned"), fake_covenant("override")
KEEP_PIN = (getattr(W, "PINNED_COVENANT_HOME", None), getattr(W, "PIN_FILE", None))


def set_pin(code=None, file_text=None, file_path=None):
    """Pin in code, or write a pin file; with neither, there is no pin."""
    W.PINNED_COVENANT_HOME = code
    p = file_path or os.path.join(tempfile.mkdtemp(), "covenant_home.pin")
    if file_text is not None:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(file_text)
    W.PIN_FILE = p
    return p


pinned_home = getattr(W, "pinned_home", lambda: (None, "not defined before the fix"))
set_pin()
check("D1a with no pin in code and no pin file, there is no pinned folder -- and a reason",
      pinned_home()[0] is None and pinned_home()[1], pinned_home())
set_pin(file_text="# covenant\n\n%s\n" % PINNED)
check("D1b a pin file's first real line is the pinned folder",
      pinned_home()[0] is not None and os.path.samefile(pinned_home()[0], PINNED), pinned_home())
set_pin(file_text="covenant\n")
check("D1c a pin that is not an absolute path is no pin", pinned_home()[0] is None, pinned_home())
set_pin(file_text=tempfile.mkdtemp() + "\n")
check("D1d a pinned folder that does not hold covenant_unified_v8.py is no pin", pinned_home()[0] is None)
set_pin(file_path=os.path.join(HERE, "private", "h6_never_written.pin"))     # named, never created
check("D1e a pin file INSIDE the trader's own folder is refused before it is even read "
      "(the trader writes there)", pinned_home()[0] is None and "inside" in pinned_home()[1], pinned_home())
set_pin(code=PINNED, file_text=OTHER + "\n")
check("D1f the pin in code wins over the pin file",
      pinned_home()[0] is not None and os.path.samefile(pinned_home()[0], PINNED), pinned_home())

set_pin()                                     # no pin at all
r = home_reasons({"armed": True, "covenant_home": PINNED})
check("D2a ARMED with NO pin: refused, though the config names a perfectly good covenant",
      len(r) == 1 and "no pinned witness folder" in r[0], r)
set_pin(file_text=PINNED + "\n")
check("D2b ARMED, pinned, and the config names the SAME folder: not refused",
      home_reasons({"armed": True, "covenant_home": PINNED}) == [])
check("D2c ARMED, pinned, and the config names nothing: not refused (the pin is the folder)",
      home_reasons({"armed": True}) == [])
r = home_reasons({"armed": True, "covenant_home": OTHER})
check("D2d ARMED, pinned, and the config names ANOTHER folder: refused, naming the config",
      len(r) == 1 and "trader_config.json covenant_home" in r[0], r)
os.environ["SENTINEL_COVENANT_HOME"] = OTHER
try:
    r = home_reasons({"armed": True, "covenant_home": PINNED})
    check("D2e ARMED, pinned, and the ENVIRONMENT names another folder: refused, naming the variable",
          len(r) == 1 and "SENTINEL_COVENANT_HOME" in r[0], r)
    check("D2f DISARMED with the same override: not refused -- and it is the override that is used",
          home_reasons({"armed": False}) == [] and os.path.samefile(W.effective_home({"armed": False}), OTHER))
finally:
    os.environ.pop("SENTINEL_COVENANT_HOME", None)
eff = getattr(W, "effective_home", lambda cfg=None: W.covenant_home(cfg))
check("D2g effective_home: armed + override -> None; armed + pinned -> the pin; disarmed -> the config's",
      eff({"armed": True, "covenant_home": OTHER}) is None
      and os.path.samefile(eff({"armed": True, "covenant_home": PINNED}), PINNED)
      and os.path.samefile(eff({"armed": False, "covenant_home": OTHER}), OTHER))

# the seal and the gate: is any Python run, and whose?
ran = []
stub = lambda home, script, payload, timeout: ran.append(home) or (True, {"ok": True, "admission": "admitted"}, "")
r = W.seal_decision_result({"armed": True, "covenant_home": OTHER}, {"at": 1}, runner=stub)
check("D3a ARMED with an override folder: the seal is REFUSED and NO Python is run from it",
      r["ok"] is False and ran == [] and "override" in str(r.get("detail")), (r, ran))
r = W.seal_decision_result({"armed": True, "covenant_home": OTHER}, {"at": 1})          # the real runner
check("D3b ...with the real runner too: the override's own seal code never answers",
      r["ok"] is False and r.get("who") != "override", r)
r = W.seal_decision_result({"armed": True, "covenant_home": PINNED}, {"at": 1})
check("D3c ARMED with the pinned folder: the seal runs there and answers as itself",
      r.get("ok") is True and r.get("who") == "pinned", r)
r = W.seal_decision_result({"armed": False, "covenant_home": OTHER}, {"at": 1})
check("D3d DISARMED with the override: the rehearsal seal runs in the override",
      r.get("ok") is True and r.get("who") == "override", r)
ran.clear()
gate_stub = lambda home, script, payload, timeout: ran.append(home) or (True, [], "")
g = W.daily_plan_gate_reasons(cfg={"armed": True, "covenant_home": OTHER}, runner=gate_stub)
check("D4a ARMED with an override: the approval gate REFUSES and runs nothing",
      g and "unavailable" in g[0] and "override" in g[0] and ran == [], (g, ran))
g = W.daily_plan_gate_reasons(cfg={"armed": True, "covenant_home": PINNED}, runner=gate_stub)
check("D4b ARMED with the pinned folder: the gate is asked there",
      g == [] and len(ran) == 1 and os.path.samefile(ran[0], PINNED), (g, ran))

# ---- E: the cycle ----------------------------------------------------------------------
os.makedirs(os.path.join(PINNED, "private"), exist_ok=True)
RP = os.path.join(PINNED, "private", "RESERVE.json")
open(RP, "w").write("{}")
clean = dict(T.DEFAULT_CONFIG, armed=True, armed_until=UNTIL, covenant_home=PINNED)
check("E1 witness_ready: armed, pinned, shipped gates -> ready", T.witness_ready(clean, reserve=RP) is None,
      T.witness_ready(clean, reserve=RP))
cases = {"a Rule 5 waiver": ({"rule5_waiver": WAIVER}, "waiver"),
         "a zero reserve": ({"trading_scope": SCOPE0}, "open_reserve_pct"),
         "another witness folder": ({"covenant_home": OTHER}, "override"),
         "the approval switched off": ({"daily_plan_required": False}, "daily_plan_required")}
let = {k: T.witness_ready(dict(clean, **over), reserve=RP) for k, (over, needle) in cases.items()}
let = {k: v for k, v in let.items() if not (v and cases[k][1] in v)}
check("E2 witness_ready: the same config with " + ", ".join(cases) + " -> refused, each named",
      not let, let)

touched = []
keep = {k: getattr(T, k) for k in ("gather", "load_state", "save_state", "seal_decision", "RESERVE_PATH")}
out = io.StringIO()
try:
    T.RESERVE_PATH = RP
    T.gather = lambda cfg: touched.append("gather") or {}
    T.load_state = lambda: touched.append("load_state") or {}
    T.save_state = lambda st: touched.append("save_state")
    T.seal_decision = lambda cfg, rec: touched.append("seal") or (False, "")
    with contextlib.redirect_stdout(out):
        T.run_once(dict(clean, rule5_waiver=WAIVER))
finally:
    for k, v in keep.items():
        setattr(T, k, v)
check("E3 a real cycle, armed with a waiver: REFUSES, names the waiver, and reads, plans, seals "
      "and saves nothing", not touched and "REFUSING THIS CYCLE" in out.getvalue()
      and "waiver" in out.getvalue(), (touched, out.getvalue()[-300:]))

lines = getattr(T, "rehearsal_lines", lambda cfg: [])
loose = dict(T.DEFAULT_CONFIG, armed=False, covenant_home=OTHER, rule5_waiver=WAIVER, trading_scope=SCOPE0)
text = "\n".join(lines(loose))
check("E4 a DISARMED config with a waiver, a zero reserve and an unpinned folder prints "
      "REHEARSAL ONLY and names all three",
      "REHEARSAL ONLY" in text and "waiver" in text and "open_reserve_pct" in text and "override" in text, text)
check("E5 ...a disarmed config with the shipped gates and the pinned folder prints no such notice",
      lines(dict(T.DEFAULT_CONFIG, armed=False, covenant_home=PINNED)) == [])
check("E6 ...and an armed config never prints it (armed, the same terms REFUSE instead)",
      lines(dict(loose, armed=True)) == [])

# ---- F: a config read from disk by preconditions itself ----------------------------------
p = os.path.join(tempfile.mkdtemp(), "trader_config.json")
with open(p, "w", encoding="utf-8") as fh:
    json.dump(dict(gate_cfg(daily_plan_required=True), rule5_waiver=WAIVER, covenant_home=OTHER), fh)
rs = G.preconditions(ORDER, cfg=None, st=dict(CLEARED), sealed_ok=True, guard_blocks=[], config_path=p)
check("F1 preconditions reading an ARMED config from disk refuses on the waiver and on the "
      "unpinned folder, in those words",
      NOT_HONOURED in rs and any("override" in r for r in rs), rs)
with open(p, "w", encoding="utf-8") as fh:
    json.dump(dict(gate_cfg(daily_plan_required=True), covenant_home=PINNED), fh)
keep_gate = W.daily_plan_gate_reasons
try:
    W.daily_plan_gate_reasons = lambda now=None, cfg=None, runner=None: []      # today approved (stub)
    rs = G.preconditions(ORDER, cfg=None, st=dict(CLEARED), sealed_ok=True, guard_blocks=[], config_path=p)
finally:
    W.daily_plan_gate_reasons = keep_gate
check("F2 ...and the same file without the waiver, naming the pinned folder, clears", rs == [], rs)

W.PINNED_COVENANT_HOME, W.PIN_FILE = KEEP_PIN
n = len(results)
print(f"\nH6 waivers and witness: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
