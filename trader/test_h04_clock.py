#!/usr/bin/env python3
"""H4 -- the day rolls forward only; a clock that reads earlier refuses the cycle.

2026-11-12, hardening pass, item 4 of an independent review of this folder.

THE DEFECT. covenant_trader.roll_day() cleared the day's orders whenever the
stored date DIFFERED from today's, and guards.orders_today_from() answered
"no orders today" on the same test. A clock set back one day -- or a time
zone crossed westward near midnight -- makes the stored day differ: the daily
order count and notional started again from zero, and a plan approved for the
earlier date was "today's approved plan" a second time.

  K1  an ordinary next day still rolls: the count clears, the marks move up.
  K2  a date earlier than the last one seen raises ClockWentBack; nothing is
      cleared. When the date catches up the day's orders are still there.
  K3  the same for the time within a day (beyond a 300 s sync allowance), and
      for a westward time-zone hop across midnight.
  K4  the marks only ever rise: after a jump forward, an earlier day refuses.
  K5  guards.orders_today_from says UNKNOWN (None) on a clock that went back,
      both caps block, and guards.preconditions names the clock.
  K6  a cycle on such a clock refuses, touches nothing and saves nothing; on a
      forward clock it runs and writes the marks.
  K7  a state file whose day marks are not dates is unreadable (H2's road).

WHAT THIS CANNOT SEE. The clock is the machine's; a clock that is simply WRONG
but later than every mark is believed. A jump far forward and back leaves the
trader refusing until an operator edits last_seen_day and last_seen_at in the
state file by hand -- on purpose: this program never lowers them. Covenant's
own reading of the date (the approval gate) is not driven here.
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

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


Back = getattr(T, "ClockWentBack", None) or type("NotDefinedBeforeTheFix", (Exception,), {})
Unreadable = getattr(T, "StateUnreadable", None) or type("NotDefinedBeforeTheFix2", (Exception,), {})
clock_back = getattr(G, "clock_back", lambda st, now=None: None)
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")
DAY = 86400.0
# Local noon today, so that +-1 day lands on the neighbouring local dates
# whatever hour this suite runs at.
NOON = time.mktime(time.strptime(time.strftime("%Y-%m-%d") + " 12:00:00", "%Y-%m-%d %H:%M:%S"))


def day_of(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


TWO = [{"sym": "XLM", "usd": 25.0, "side": "sell"}, {"sym": "ADA", "usd": 25.0, "side": "sell"}]


def full_day(at):
    """A state that placed its two orders on the local day of `at`."""
    return {"day": day_of(at), "last_seen_day": day_of(at), "last_seen_at": at,
            "orders_today": [dict(o) for o in TWO], "equity_peak": 100.0,
            "equity_start_of_day": 100.0, "closed_trades": [], "last_sold": {},
            "sealed_signals": 0, "bought_total_usd": 0.0}


def roll(st, now):
    try:
        if T.roll_day.__code__.co_argcount >= 2:
            return "rolled", T.roll_day(st, now)
        return "rolled", T.roll_day(st)          # the code before the fix has no clock to hand it
    except Back as e:
        return "refused", str(e)


# ---- K1: forward ----------------------------------------------------------------
st = full_day(NOON)
kind, out = roll(st, NOON + DAY)
check("K1a the next local day rolls as before: the two orders are cleared",
      kind == "rolled" and st["orders_today"] == [] and st["day"] == day_of(NOON + DAY), (kind, st))
check("K1b ...and both marks moved up with it",
      st.get("last_seen_day") == day_of(NOON + DAY) and st.get("last_seen_at") == NOON + DAY, st)
st = full_day(NOON)
kind, out = roll(st, NOON + 3600)
check("K1c later the SAME day nothing is cleared: still two orders",
      kind == "rolled" and len(st["orders_today"]) == 2, (kind, st["orders_today"]))

# ---- K2: the date goes back -----------------------------------------------------
st = full_day(NOON)
kind, why = roll(st, NOON - DAY)
check("K2a a clock set back one day is REFUSED, not rolled", kind == "refused", (kind, why))
check("K2b ...the day's two orders are still on record (the count was not handed back)",
      len(st["orders_today"]) == 2 and st["day"] == day_of(NOON), st)
check("K2c ...and the reason names both dates",
      kind == "refused" and day_of(NOON) in why and day_of(NOON - DAY) in why, why)
kind, out = roll(st, NOON + 60)
check("K2d when the clock is right again the same state runs, with its two orders intact",
      kind == "rolled" and len(st["orders_today"]) == 2, (kind, st["orders_today"]))

# ---- K3: the time goes back -------------------------------------------------------
st = full_day(NOON)
kind, why = roll(st, NOON - 3600)
check("K3a one hour earlier on the same day is refused too (an approval's hours cannot be re-run)",
      kind == "refused" and len(st["orders_today"]) == 2, (kind, why))
st = full_day(NOON)
kind, out = roll(st, NOON - 120)
check("K3b ...two minutes earlier is inside the 300 s allowed for a time-sync correction",
      kind == "rolled", (kind, out))
check("K3c ...and the mark did not move back with it (small steps cannot creep)",
      st.get("last_seen_at") == NOON, st.get("last_seen_at"))
midnight = NOON - 12 * 3600                       # local 00:00 today
st = full_day(midnight + 1800)                    # ran at 00:30 today
kind, why = roll(st, midnight - 1800)             # a zone one hour west: 23:30 "yesterday"
check("K3d a westward time-zone hop across midnight (00:30 -> 23:30 the day before) is refused",
      kind == "refused" and len(st["orders_today"]) == 2, (kind, why))

# ---- K4: monotonic ---------------------------------------------------------------
st = full_day(NOON)
roll(st, NOON + 5 * DAY)
kind, why = roll(st, NOON + DAY)
check("K4 after the clock has been seen five days ahead, a day only one ahead is refused",
      kind == "refused", (kind, why))

# ---- K5: the guards ----------------------------------------------------------------
st = full_day(NOON)
check("K5a orders_today_from: on the day itself, the two orders",
      G.orders_today_from(st, NOON + 60) == st["orders_today"])
check("K5b ...the next day, a known empty day", G.orders_today_from(st, NOON + DAY) == [])
check("K5c ...the day BEFORE, unknown (None) -- it used to be an empty day",
      G.orders_today_from(st, NOON - DAY) is None, G.orders_today_from(st, NOON - DAY))
legacy = {"day": day_of(NOON), "orders_today": [dict(o) for o in TWO]}
check("K5d ...and a state written before the marks existed is protected by its `day` alone",
      G.orders_today_from(legacy, NOON - DAY) is None and G.orders_today_from(legacy, NOON + DAY) == [])
gs = G.State(equity_now=0.0, equity_peak=0.0, equity_start_of_day=0.0, closed_trades=[],
             last_sold={}, positions={}, cash=0.0, orders_today=G.orders_today_from(st, NOON - DAY))
check("K5e both caps block on it",
      not G.PerDayCap(max_orders=2, max_notional=50.0).check(gs).allowed
      and not G.PerTradeCap(max_usd=25.0, min_usd=5.0, max_orders=2, max_notional=50.0).check(gs).allowed)
# Armed until shortly after the latest clock this file hands to preconditions
# (NOON + 60), whatever hour the suite runs at; always under 24 h ahead of it.
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(time.time(), NOON + 60) + 3000))
cfg = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False, "seal_required": True,
       "min_sealed_signals": 0, "rule5_require_significance": False, "min_order_usd": 5.0,
       "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}
order = {"side": "sell", "usd": 20.0, "sym": "XLM"}


def pre(state, now):
    return G.preconditions(order, cfg=cfg, st=state, sealed_ok=True, guard_blocks=[],
                           config_path=NO_CFG, now=now)


empty_today = dict(full_day(NOON), orders_today=[])
back = pre(empty_today, NOON - DAY)
check("K5f guards.preconditions refuses an order on a clock a day back, and names the clock",
      any(r.startswith("clock:") for r in back), back)
check("K5g ...and clears the same order, same state, on a clock that is not",
      pre(empty_today, NOON + 60) == [], pre(empty_today, NOON + 60))
check("K5h a last_seen_day that is not a date is a reason, never ignored",
      clock_back({"last_seen_day": "tomorrow"}, NOON) is not None
      and clock_back({"last_seen_day": day_of(NOON)}, NOON) is None)


# ---- K6: the cycle -----------------------------------------------------------------
def cycle(state_bytes):
    touched = []
    p = os.path.join(tempfile.mkdtemp(), "trader_state.json")
    with open(p, "wb") as fh:
        fh.write(state_bytes)
    names = ("node_status", "gather", "plan", "execute", "witness_ready", "seal_decision", "STATE")
    keep = {k: getattr(T, k) for k in names}
    keep_rc = T.signal_ledger.record_cycle
    out = io.StringIO()
    try:
        T.STATE = p
        T.node_status = lambda ports, timeout=4: []
        T.witness_ready = lambda cfg, reserve=None: None
        T.gather = lambda cfg: touched.append("gather") or {
            "positions": [], "unpriced": [], "venue_notes": [], "total": 0.0, "cash": 0.0}
        T.plan = lambda cfg, pf, week_spent=0.0: touched.append("plan") or ([], ["stub"])
        T.execute = lambda *a, **kw: touched.append("execute") or []
        T.seal_decision = lambda c, rec: touched.append("seal") or (True, "stub")
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 0, "wins": 0, "clears": False, "why": "stub"}
        with contextlib.redirect_stdout(out):
            T.run_once(dict(T.DEFAULT_CONFIG, armed=False, seal_required=True, node_ports=[]))
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle = keep_rc
    return touched, out.getvalue(), open(p, "rb").read()


ahead = json.dumps(full_day(time.time() + 2 * DAY)).encode()     # the record is two days AHEAD of the real clock
touched, out, after = cycle(ahead)
check("K6a a cycle whose clock is behind its own record REFUSES and names the clock",
      "REFUSING THIS CYCLE" in out and "clock:" in out, out[-300:])
check("K6b ...it gathers, plans, seals and executes nothing, and the state file is untouched",
      touched == [] and after == ahead, touched)
behind = json.dumps(full_day(time.time() - DAY)).encode()        # the record is yesterday's: an ordinary morning
touched, out, after = cycle(behind)
st_after = json.loads(after)
check("K6c the ordinary case runs: yesterday's record, today's clock",
      touched[:2] == ["gather", "plan"] and "REFUSING" not in out, (touched, out[-200:]))
check("K6d ...and the saved state carries today as the last day seen, with yesterday's orders cleared",
      st_after.get("last_seen_day") == time.strftime("%Y-%m-%d") and st_after["orders_today"] == []
      and isinstance(st_after.get("last_seen_at"), float), st_after)


# ---- K7: the file road ---------------------------------------------------------------
def loads(obj):
    p = os.path.join(tempfile.mkdtemp(), "trader_state.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    keep = T.STATE
    T.STATE = p
    try:
        T.load_state()
        return "loaded"
    except Unreadable as e:
        return "unreadable: %s" % e
    finally:
        T.STATE = keep


bad = {"day 'x'": {"day": "x"}, "day 2026-13-45": {"day": "2026-13-45"},
       "last_seen_day 'soon'": {"last_seen_day": "soon"}, "last_seen_at a string": {"last_seen_at": "noon"}}
let = {k: loads(v) for k, v in bad.items()}
let = {k: v for k, v in let.items() if not v.startswith("unreadable")}
check("K7a a state file whose day marks are not dates or numbers is unreadable: " + "; ".join(bad),
      not let, let)
check("K7b ...and real marks, or none yet (\"\"), load",
      loads({"day": "", "orders_today": []}) == "loaded" and loads(full_day(NOON)) == "loaded")

n = len(results)
print(f"\nH4 clock: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
