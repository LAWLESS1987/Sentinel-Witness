#!/usr/bin/env python3
"""SW1 -- what moving the trader into Sentinel-Witness added, driven both ways.

2026-10-05. The trader was copied from covenant into this repository on the
operator's words "move all info that was here regarding trading ... intergrate
and rewire to actually trade", with his choices: Rule 5 off, the caps and the
hold-only floors kept; copy, prove, then retire. Four things are new here and
each is a place a mistake would cost money, so each is run, never grepped:

  W1-W2  the Rule 5 waiver is a RECORD: only a complete one waives, and it
         waives Rule 5 and nothing else.
  W3     a TRADER_HALT dropped in covenant stops this trader too.
  W4-W6  witness.py refuses on every failure to reach covenant -- no home, a
         crash, a timeout, a malformed answer -- and never passes one through.
  W7-W8  without covenant (or without its floor file) a cycle reads, plans,
         seals and sends nothing; and the waiver travels in the sealed record.

Uses temp folders and stub runners only. Places nothing, opens no credential,
starts no covenant process.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import witness as W          # noqa: E402
import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:200]}"), flush=True)


WAIVER = {"waived": True, "by": "operator", "at": "2026-10-05",
          "words": "Trade now within caps"}
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")


def fake_covenant():
    """A folder witness.covenant_home() accepts: it holds covenant_unified_v8.py."""
    d = tempfile.mkdtemp()
    open(os.path.join(d, "covenant_unified_v8.py"), "w").close()
    return d


def base_cfg(**over):
    c = {"armed": True, "daily_plan_required": False, "max_order_usd": 25.0,
         "min_order_usd": 5.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0,
         "seal_required": False, "min_sealed_signals": 30,
         "rule5_require_significance": True}
    c.update(over)
    return c


def reasons(cfg, order=None, st=None):
    return G.preconditions(order or {"side": "sell", "usd": 10.0, "sym": "LINK"},
                           cfg=cfg, st=st if st is not None else {"orders_today": [], "sealed_signals": 7},
                           sealed_ok=True, guard_blocks=[], config_path=NO_CFG)


def rule5_in(rs):
    return [r for r in rs if r.startswith("Rule 5")]


# ---- W1: only a complete record waives ---------------------------------------
check("W1a a complete waiver is read as one", G.rule5_waiver({"rule5_waiver": WAIVER}) is not None)
defects = {
    "absent": {},
    "waived false": {"rule5_waiver": {**WAIVER, "waived": False}},
    "waived the STRING true": {"rule5_waiver": {**WAIVER, "waived": "true"}},
    "waived 1": {"rule5_waiver": {**WAIVER, "waived": 1}},
    "no 'by'": {"rule5_waiver": {k: v for k, v in WAIVER.items() if k != "by"}},
    "blank 'words'": {"rule5_waiver": {**WAIVER, "words": "   "}},
    "'at' not a string": {"rule5_waiver": {**WAIVER, "at": 20261005}},
    "a list, not an object": {"rule5_waiver": [WAIVER]},
}
let_through = [k for k, c in defects.items() if G.rule5_waiver(c) is not None]
check("W1b every incomplete or mistyped waiver is NO waiver: " + ", ".join(defects),
      not let_through, let_through)
check("W1c a cfg that is not a dict is no waiver, not a crash", G.rule5_waiver(None) is None
      and G.rule5_waiver("waived") is None)

# ---- W2: it waives Rule 5 and nothing else -------------------------------------
without = reasons(base_cfg())
withw = reasons(base_cfg(rule5_waiver=WAIVER))
check("W2a without a waiver, 7 of 30 signals refuses in Rule 5's own words",
      rule5_in(without), without)
check("W2b with the waiver, Rule 5 no longer refuses", not rule5_in(withw), withw)
sig = base_cfg(min_sealed_signals=0, rule5_waiver=dict(WAIVER, waived=False))
check("W2c ...and the significance half is waived the same way: count met, record "
      "not clear, refused without the waiver",
      rule5_in(reasons(sig, st={"orders_today": [], "sealed_signals": 9,
                                "rule5": {"clears": False, "why": "0/9 wins"}})))
check("W2d ...and cleared with it",
      not rule5_in(reasons(base_cfg(min_sealed_signals=0, rule5_waiver=WAIVER),
                           st={"orders_today": [], "sealed_signals": 9,
                               "rule5": {"clears": False, "why": "0/9 wins"}})))
over = {"side": "buy", "usd": 40.0, "sym": "XLM"}
w_over = reasons(base_cfg(rule5_waiver=WAIVER), order=over)
check("W2e the waiver does not lift the per-trade cap: $40 against $25 still refuses",
      any("placeable now" in r for r in w_over), w_over)
w_dis = reasons(base_cfg(armed=False, rule5_waiver=WAIVER))
check("W2f ...nor arming: disarmed still refuses", any(r.startswith("armed=false") for r in w_dis), w_dis)
used = {"orders_today": [{"usd": 25.0, "side": "sell", "at": 0}, {"usd": 25.0, "side": "sell", "at": 0}],
        "day": T.time.strftime("%Y-%m-%d"), "sealed_signals": 7}
w_day = reasons(base_cfg(rule5_waiver=WAIVER), st=used)
check("W2g ...nor the daily order limit", any(r.startswith("per_day_cap") for r in w_day), w_day)
w_seal = G.preconditions({"side": "sell", "usd": 10.0, "sym": "LINK"},
                         cfg=base_cfg(seal_required=True, rule5_waiver=WAIVER),
                         st={"orders_today": [], "sealed_signals": 7}, sealed_ok=False,
                         guard_blocks=[], config_path=NO_CFG)
check("W2h ...nor the seal: a refused seal still refuses", "decision not sealed to the chain" in w_seal, w_seal)
# W2i ADDED after mutation: with the waiver branch turned into an early
# `return bad`, W2a-h stayed 38/38 -- every one of them is decided BEFORE Rule 5.
# The guard stack is applied AFTER it, and the guard stack is where the cash
# floor lives. So drive a buy the guard stack has blocked.
w_guard = G.preconditions({"side": "buy", "usd": 10.0, "sym": "XLM"},
                          cfg=base_cfg(rule5_waiver=WAIVER),
                          st={"orders_today": [], "sealed_signals": 7}, sealed_ok=True,
                          guard_blocks=["cash_floor"], config_path=NO_CFG)
check("W2i ...nor the guard stack, which is checked AFTER Rule 5: a buy the cash "
      "floor blocks still refuses with the waiver in place",
      "guards: cash_floor" in w_guard, w_guard)

# ---- W3: covenant's halt stops this trader -------------------------------------
home = fake_covenant()
os.environ["SENTINEL_COVENANT_HOME"] = home
try:
    clear = reasons(base_cfg(rule5_waiver=WAIVER))
    check("W3a with no halt in covenant, no covenant-halt reason", not any("in covenant" in r for r in clear), clear)
    open(os.path.join(home, "TRADER_HALT"), "w").close()
    halted = reasons(base_cfg(rule5_waiver=WAIVER))
    check("W3b a TRADER_HALT dropped in covenant refuses here", "TRADER_HALT file present in covenant" in halted, halted)
    os.remove(os.path.join(home, "TRADER_HALT"))
    check("W3c ...and removing it clears exactly that reason",
          "TRADER_HALT file present in covenant" not in reasons(base_cfg(rule5_waiver=WAIVER)))
finally:
    os.environ.pop("SENTINEL_COVENANT_HOME", None)

# ---- W4: where covenant is -----------------------------------------------------
check("W4a no env and no covenant_home in cfg: NOT configured (no built-in default)",
      W.covenant_home({}) is None)
check("W4b a folder without covenant_unified_v8.py is not covenant", W.covenant_home({"covenant_home": tempfile.mkdtemp()}) is None)
check("W4c a folder with it is", W.covenant_home({"covenant_home": home}) == os.path.abspath(home))
check("W4d the floor file is read IN covenant, not copied here",
      W.reserve_path({"covenant_home": home}) == os.path.join(os.path.abspath(home), "private", "RESERVE.json"))
check("W4e the trader and the guards read ONE floor file", T.RESERVE_PATH == G.RESERVE_PATH, (T.RESERVE_PATH, G.RESERVE_PATH))

# ---- W5: the day's approval -----------------------------------------------------
cfg_home = {"covenant_home": home}
r_nohome = W.daily_plan_gate_reasons(cfg={})
check("W5a no covenant: the plan gate REFUSES rather than passing", r_nohome and "nothing may go live" in r_nohome[0], r_nohome)
check("W5b covenant says approved ([]): no reason", W.daily_plan_gate_reasons(cfg=cfg_home, runner=lambda *a: (True, [], "")) == [])
said = ["no approved daily plan for 2026-10-05: ..."]
check("W5c covenant's refusal is passed through word for word",
      W.daily_plan_gate_reasons(cfg=cfg_home, runner=lambda *a: (True, said, "")) == said)
crash = W.daily_plan_gate_reasons(cfg=cfg_home, runner=lambda *a: (False, None, "covenant exited 1: boom"))
check("W5d covenant crashing is a refusal", crash and "unavailable" in crash[0], crash)
odd = W.daily_plan_gate_reasons(cfg=cfg_home, runner=lambda *a: (True, {"ok": True}, ""))
check("W5e an answer that is not a list of reasons is a refusal, not an approval", odd and "unavailable" in odd[0], odd)
check("W5f a `now` that is not epoch seconds is a refusal",
      "unavailable" in W.daily_plan_gate_reasons(now="2026-10-05", cfg=cfg_home, runner=lambda *a: (True, [], ""))[0])

# ---- W6: the seal ----------------------------------------------------------------
check("W6a no covenant: the seal is refused", W.seal_decision_result({}, {"at": 1})["ok"] is False)
check("W6b covenant crashing: refused", W.seal_decision_result(cfg_home, {"at": 1},
      runner=lambda *a: (False, None, "covenant did not answer within 700 s"))["ok"] is False)
check("W6c an answer with no 'ok' field: refused, not assumed",
      W.seal_decision_result(cfg_home, {"at": 1}, runner=lambda *a: (True, {"admission": "admitted"}, ""))["ok"] is False)
good = {"ok": True, "status": 200, "admission": "admitted", "tx_id": "t", "detail": "HTTP 200", "mined": ""}
check("W6d covenant's admission is passed through unchanged",
      W.seal_decision_result(cfg_home, {"at": 1}, runner=lambda *a: (True, good, "")) == good)
real_run = W._run(home, "import sys; sys.exit(3)", {}, 30)
check("W6e the real runner reports a non-zero exit as a failure (not a stub)", real_run[0] is False and "exited 3" in real_run[2], real_run)
real_ok = W._run(home, "import json,sys; json.loads(sys.stdin.read()); print('noise'); print(json.dumps([]))", {"x": 1}, 30)
check("W6f ...and reads the LAST line as the answer, ignoring what printed before it", real_ok == (True, [], ""), real_ok)

# ---- W7: no covenant, no cycle ---------------------------------------------------
check("W7a witness_ready: no covenant -> a reason", T.witness_ready({}) is not None)
check("W7b covenant but no floor file -> a reason naming the file",
      "reserve file is missing" in (T.witness_ready(cfg_home, reserve=os.path.join(home, "private", "RESERVE.json")) or ""))
os.makedirs(os.path.join(home, "private"), exist_ok=True)
rp = os.path.join(home, "private", "RESERVE.json")
open(rp, "w").write("{}")
check("W7c covenant and its floor file -> ready", T.witness_ready(cfg_home, reserve=rp) is None)

touched = []
keep = {k: getattr(T, k) for k in ("gather", "load_state", "save_state", "seal_decision")}
try:
    T.gather = lambda cfg: touched.append("gather") or {}
    T.load_state = lambda: touched.append("load_state") or {}
    T.save_state = lambda st: touched.append("save_state")
    T.seal_decision = lambda cfg, rec: touched.append("seal") or (False, "")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        T.run_once({"armed": True})
    check("W7d a cycle with no covenant reads, plans, seals and saves NOTHING",
          not touched and "REFUSING THIS CYCLE" in out.getvalue(), touched)
finally:
    for k, v in keep.items():
        setattr(T, k, v)


# ---- W8: the waiver is in the sealed record ---------------------------------------
def sealed_record(cfg):
    got = {}
    keep = {k: getattr(T, k) for k in ("STATE", "load_state", "node_status", "gather", "plan",
                                       "execute", "save_state", "witness_ready", "seal_decision")}
    keep_rc = T.signal_ledger.record_cycle
    try:
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.load_state = lambda: {"orders_today": [], "day": "", "equity_peak": 0.0,
                                "equity_start_of_day": 0.0, "closed_trades": [],
                                "last_sold": {}, "bought_total_usd": 0.0, "sealed_signals": 7}
        T.node_status = lambda ports, timeout=4: []
        T.gather = lambda cfg: {"positions": [], "unpriced": [], "venue_notes": [], "total": 0.0, "cash": 0.0}
        T.plan = lambda cfg, pf, week_spent=0.0: ([], ["no orders (stub)"])
        T.execute = lambda *a, **kw: []
        T.save_state = lambda st: None
        T.witness_ready = lambda cfg, reserve=None: None
        T.seal_decision = lambda c, rec: (got.update(rec) or (True, "stub"))
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 1, "settled": 7, "wins": 0, "mean_after_costs": -0.06,
            "p_value": 1.0, "clears": False, "why": "7 settled (stub)"}
        with contextlib.redirect_stdout(io.StringIO()):
            T.run_once(cfg)
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle = keep_rc
    return got


c8 = dict(T.DEFAULT_CONFIG, armed=False, seal_required=True, node_ports=[])
r_without = sealed_record(c8)
r_with = sealed_record(dict(c8, rule5_waiver=WAIVER))
check("W8a without a waiver the sealed Rule 5 record carries no 'waived'",
      r_without.get("rule5") and "waived" not in r_without["rule5"], r_without.get("rule5"))
check("W8b with one, the sealed record says waived=yes -- the chain shows each waived day",
      (r_with.get("rule5") or {}).get("waived") == "yes", r_with.get("rule5"))
check("W8c ...and the record itself is still the real one: 7 settled, not yet clear",
      (r_with.get("rule5") or {}).get("settled") == 7 and r_with["rule5"].get("clears") == "not yet",
      r_with.get("rule5"))

n, ok = len(results), sum(results)
print(f"\nSW1 witness: {ok}/{n} passed")
sys.exit(0 if ok == n else 1)
