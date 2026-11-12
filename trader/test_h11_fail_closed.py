#!/usr/bin/env python3
"""H11 -- a halt that cannot be looked at is a halt; "never run" is only ever a
file the OS says is not there.

2026-11-12, hardening pass, item 11 of an independent review of this folder.

THE DEFECT, AND ITS SHAPE. os.path.exists() answers False for a path it could
not examine at all: a permission error, a drive that has gone away, a name
the OS will not parse. Wherever False is the answer that CARRIES ON, a failure
to look became permission:

  the halt        `if os.path.exists(HALT)` -- a TRADER_HALT that could not be
                  looked at read as "no halt".
  "never run"     `if not os.path.exists(state): return []` (and 0.0, and {})
                  -- a state file that could not be looked at read as a
                  trader that had placed nothing today and bought nothing ever.
  "no floors yet" `if os.path.exists(reserve)` -- a floor file that could not
                  be looked at read as "none set", and new floors were written
                  at today's quantities.
  "no config yet" `if not os.path.exists(CONFIG)` -- a config that could not
                  be looked at was overwritten with the default one.

guards.path_state() says "absent" only for FileNotFoundError. Anything else
that is not "present" is "unknown", and every caller treats unknown as the
refusing answer.

  P  path_state: present, absent, unknown.
  H  the halt, here and in covenant: present refuses, absent does not, and
     un-examinable REFUSES (it used to pass).
  S  the state readers: absent is a known zero; un-examinable is unknown, and
     the caps block.
  T  the trader: an un-examinable state is StateUnreadable, an un-examinable
     floor file is ReserveUnreadable and nothing is written, an un-examinable
     config is not overwritten.
  M  money_posture.py reports an un-examinable halt as that, not as absent.
  A  armed with the folder pinned and no covenant_home in the config, the
     covenant halt is still looked for (in the pinned folder).

THE OTHERS OF THE SAME SHAPE, found by reading every os.path.exists / isfile
in the trading path, and what was done with each -- this list is the search
the review asked for, and it is prose, not a check:

  fixed here      guards.preconditions (both halts); guards.orders_today_now,
                  fiat_buys_week_now, bought_total_now, load_trader_state,
                  set_starting_total; covenant_trader._read_state_file,
                  reserve_baseline, load_config, --status; money_posture.
  already closed  witness.covenant_home, covenant_trader.witness_ready
                  (isfile False -> "not configured" / "reserve file missing",
                  both refusals); signal_ledger (no ledger -> 0 signals ->
                  Rule 5 refuses); guards.allow_fiat_buys (unreadable -> no).
  NOT fixed       venues.*.has_credentials(): a credential that cannot be
                  examined reads as "no credential installed", so that venue
                  is skipped as one this machine does not use rather than
                  named as a venue that could not be read (H3). daily.py's
                  own state reader (a missing equity history makes the guards
                  block, so it fails closed, but it is the same test).

WHAT THIS CANNOT SEE. A stat that fails is simulated by replacing the one
function path_state calls; no real un-examinable path is created.
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


HAS_FIX = hasattr(G, "path_state")
path_state = getattr(G, "path_state", lambda p: "present" if os.path.exists(p) else "absent")
REAL_LSTAT = os.lstat
REAL_EXISTS, REAL_LEXISTS, REAL_ISFILE = os.path.exists, os.path.lexists, os.path.isfile


@contextlib.contextmanager
def unexaminable(*paths):
    """Make looking at each of `paths` fail with PermissionError -- for the
    new reading (guards._lstat) and, so that this file also runs against the
    code before the fix, for os.path.exists/lexists, which answer False."""
    bad = {os.path.normcase(os.path.abspath(p)) for p in paths}

    def hit(p):
        try:
            return os.path.normcase(os.path.abspath(p)) in bad
        except (TypeError, ValueError):
            return False

    def lstat(p, *a, **k):
        if hit(p):
            raise PermissionError(13, "Permission denied (test)", str(p))
        return REAL_LSTAT(p, *a, **k)

    keep = getattr(G, "_lstat", None)
    try:
        if HAS_FIX:
            G._lstat = lstat
        os.path.exists = lambda p: False if hit(p) else REAL_EXISTS(p)
        os.path.lexists = lambda p: False if hit(p) else REAL_LEXISTS(p)
        os.path.isfile = lambda p: False if hit(p) else REAL_ISFILE(p)
        yield
    finally:
        if HAS_FIX:
            G._lstat = keep
        os.path.exists, os.path.lexists, os.path.isfile = REAL_EXISTS, REAL_LEXISTS, REAL_ISFILE


TMP = tempfile.mkdtemp()
NO_CFG = os.path.join(TMP, "no_such_config.json")
TODAY = time.strftime("%Y-%m-%d")
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))

# ---- P ---------------------------------------------------------------------------
there = os.path.join(TMP, "there")
open(there, "w").close()
check("P1 a file that is there is 'present'; a name that is not is 'absent'",
      path_state(there) == "present" and path_state(os.path.join(TMP, "nope")) == "absent")
with unexaminable(there):
    s = path_state(there)
check("P2 a path that cannot be examined is 'unknown' with the reason -- never 'absent'",
      s.startswith("unknown") and "PermissionError" in s, s)
check("P3 a name the OS will not even parse is unknown too", path_state("bad\x00name").startswith("unknown"),
      path_state("bad\x00name"))


# ---- H: the halt -------------------------------------------------------------------
def cfg(**over):
    c = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False, "seal_required": True,
         "min_sealed_signals": 0, "rule5_require_significance": False, "min_order_usd": 5.0,
         "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}
    c.update(over)
    return c


ORDER = {"side": "sell", "usd": 20.0, "sym": "XLM"}


def pre(c=None, **kw):
    kw.setdefault("st", {"day": TODAY, "orders_today": []})
    return G.preconditions(ORDER, cfg=c or cfg(), sealed_ok=True, guard_blocks=[], config_path=NO_CFG, **kw)


keep_halt = G.HALT
halt = os.path.join(TMP, "TRADER_HALT")
try:
    G.HALT = halt
    check("H1 no halt file: nothing refuses", pre() == [], pre())
    open(halt, "w").close()
    check("H2 a halt file: refused, in the words every reader knows", pre() == ["TRADER_HALT file present"], pre())
    with unexaminable(halt):
        r = pre()
    check("H3 a halt file that CANNOT BE LOOKED AT: refused, as a halt (it used to pass as 'no halt')",
          len(r) == 1 and "TRADER_HALT" in r[0] and "treated as a halt" in r[0], r)
    os.remove(halt)
    with unexaminable(halt):
        r = pre()
    check("H3b ...the same when there is in fact no file but nobody can tell", len(r) == 1 and "treated as a halt" in r[0], r)

    home = tempfile.mkdtemp()
    open(os.path.join(home, "covenant_unified_v8.py"), "w").close()
    chalt = os.path.join(home, "TRADER_HALT")
    c_home = cfg(covenant_home=home, armed=False)            # disarmed: the config's folder is the folder
    only_cov = lambda rs: [x for x in rs if "covenant" in x]
    check("H4 covenant's halt absent: no covenant reason", only_cov(pre(c_home)) == [], pre(c_home))
    open(chalt, "w").close()
    check("H5 covenant's halt present: refused", only_cov(pre(c_home)) == ["TRADER_HALT file present in covenant"])
    with unexaminable(chalt):
        r = only_cov(pre(c_home))
    check("H6 covenant's halt un-examinable: refused, as a halt",
          len(r) == 1 and "in covenant" in r[0] and "treated as a halt" in r[0], r)
finally:
    G.HALT = keep_halt

# ---- S: the state readers -----------------------------------------------------------
state = os.path.join(TMP, "trader_state.json")
missing = os.path.join(TMP, "never_written.json")
with open(state, "w", encoding="utf-8") as fh:
    json.dump({"day": TODAY, "orders_today": [{"usd": 25.0, "side": "sell"}, {"usd": 25.0, "side": "sell"}],
               "bought_total_usd": 400.0, "fiat_buys": [{"at": time.time(), "usd": 90.0}]}, fh)
check("S1 a state file that is NOT THERE is a known zero: no orders, $0 bought, an empty week, {}",
      G.orders_today_now(missing) == [] and G.bought_total_now(missing) == 0.0
      and G.fiat_buys_week_now(missing) == [] and G.load_trader_state(missing) == {})
check("S2 a state file that is there is read: 2 orders, $400 bought, $90 this week",
      len(G.orders_today_now(state)) == 2 and G.bought_total_now(state) == 400.0
      and G.fiat_buys_week_now(state)[0]["usd"] == 90.0)
with unexaminable(state):
    got = (G.orders_today_now(state), G.bought_total_now(state), G.fiat_buys_week_now(state),
           G.load_trader_state(state))
    r = G.preconditions(ORDER, cfg=cfg(), st=None, state_path=state, sealed_ok=True, guard_blocks=[],
                        config_path=NO_CFG)
# Against the code before the fix the file is still OPENABLE in this harness
# (only the look is made to fail), so the readers there answer from "exists is
# False": the known zero. That is the defect.
check("S3 a state file that CANNOT BE LOOKED AT is never the known zero: the day's orders are "
      "not [], the lifetime total is not $0, the week is not empty, the state is not {}",
      got[0] != [] and got[1] != 0.0 and got[2] != [] and got[3] != {}, got)
check("S4 ...and guards.preconditions, reading it itself, does not clear the order",
      r != [], r)

# ---- T: the trader ---------------------------------------------------------------------
keep_state = T.STATE
try:
    T.STATE = state
    with unexaminable(state):
        try:
            st = T.load_state()
            got = "loaded: %d orders" % len(st.get("orders_today", []))
        except T.StateUnreadable as e:
            got = "unreadable"
    check("T1 load_state on an un-examinable state: never the fresh defaults (0 orders)",
          got != "loaded: 0 orders", got)
finally:
    T.STATE = keep_state

reserve = os.path.join(tempfile.mkdtemp(), "RESERVE.json")
floors = {"baseline": {"XRP": 500.0}, "starting_total_usd": 1000.0}
with open(reserve, "w", encoding="utf-8") as fh:
    json.dump(floors, fh)
pf = {"positions": [{"sym": "XRP", "qty": 300.0, "px": 1.0, "val": 300.0}]}
with unexaminable(reserve):
    try:
        base, held, raised = T.reserve_baseline(pf, path=reserve)
        got = "floors: %r" % base
    except T.ReserveUnreadable:
        got = "unreadable"
after = json.load(open(reserve, encoding="utf-8"))
check("T2 reserve_baseline on an un-examinable floor file: the frozen XRP floor of 500 is NOT "
      "re-set to today's 300, and the file is not rewritten",
      "300" not in got and after == floors, (got, after))
base, held, raised = T.reserve_baseline(pf, path=reserve)
check("T2b ...and the same file, when it can be looked at, gives the floor it holds",
      base.get("XRP") == 500.0, base)

keep_cfg = T.CONFIG
conf = os.path.join(tempfile.mkdtemp(), "trader_config.json")
mine = {"armed": False, "max_order_usd": 10.0, "covenant_home": "somewhere", "note": "the operator's own"}
with open(conf, "w", encoding="utf-8") as fh:
    json.dump(mine, fh)
try:
    T.CONFIG = conf
    with unexaminable(conf), contextlib.redirect_stdout(io.StringIO()):
        loaded = T.load_config()
finally:
    T.CONFIG = keep_cfg
check("T3 load_config on an un-examinable config does NOT write the default one over it",
      json.load(open(conf, encoding="utf-8")) == mine and loaded.get("max_order_usd") == 10.0,
      json.load(open(conf, encoding="utf-8")))

# ---- M: money_posture -------------------------------------------------------------------
import money_posture as MP   # noqa: E402

mp_halt = os.path.join(tempfile.mkdtemp(), "TRADER_HALT")
mp_cfg = os.path.join(tempfile.mkdtemp(), "trader_config.json")
with open(mp_cfg, "w", encoding="utf-8") as fh:
    json.dump({"armed": True, "armed_until": UNTIL}, fh)
keep = (MP.CFG, MP.HALT, MP._run)
out = io.StringIO()
try:
    MP.CFG, MP.HALT, MP._run = mp_cfg, mp_halt, (lambda cmd: "")
    with unexaminable(mp_halt), contextlib.redirect_stdout(out):
        rc = MP.main()
    out2 = io.StringIO()
    with contextlib.redirect_stdout(out2):
        rc2 = MP.main()
finally:
    MP.CFG, MP.HALT, MP._run = keep
check("M1 money_posture, armed, with a halt file it cannot look at: it says COULD NOT BE "
      "CHECKED, and does not report ARMED on the strength of 'absent'",
      "COULD NOT BE CHECKED" in out.getvalue() and rc != 1, (rc, out.getvalue()[-200:]))
check("M2 ...and with a halt file that is plainly absent, the same config is ARMED (exit 1)",
      rc2 == 1 and "absent" in out2.getvalue(), rc2)

# ---- A: armed, pinned, no covenant_home in the config --------------------------------------
pinned = tempfile.mkdtemp()
open(os.path.join(pinned, "covenant_unified_v8.py"), "w").close()
keep_pin = (getattr(W, "PINNED_COVENANT_HOME", None),)
try:
    W.PINNED_COVENANT_HOME = pinned
    armed_no_home = cfg()                                       # armed, names no covenant_home
    check("A1 armed and pinned, the covenant halt path is the PINNED folder's, though the "
          "config names no folder", W.halt_path(armed_no_home) == os.path.join(os.path.abspath(pinned), "TRADER_HALT"),
          W.halt_path(armed_no_home))
    open(os.path.join(pinned, "TRADER_HALT"), "w").close()
    check("A2 ...and a halt dropped there refuses the order",
          "TRADER_HALT file present in covenant" in pre(armed_no_home), pre(armed_no_home))
    os.remove(os.path.join(pinned, "TRADER_HALT"))
    check("A3 ...and removing it clears it", pre(armed_no_home) == [], pre(armed_no_home))
finally:
    W.PINNED_COVENANT_HOME = keep_pin[0]

n = len(results)
print(f"\nH11 fail closed: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
