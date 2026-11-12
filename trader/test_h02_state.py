#!/usr/bin/env python3
"""H2 -- a state file that cannot be read stops the cycle and is never overwritten.

2026-11-12, hardening pass, item 2 of an independent review of this folder.

THE DEFECT. covenant_trader.load_state() caught every exception and returned
the defaults: no orders today, nothing ever bought, no equity peak, no sealed
signals. So a state file that was torn, truncated, or simply not JSON read as
"this trader has never run", the cycle went on with the day's order count and
the lifetime buy budget at zero, and save_state() at the end of the cycle wrote
that over the real record. guards.load_trader_state already refused such a
file; the trader's own reader did not.

  S1  a MISSING file is still a known empty state (the trader has never run).
  S2  a file that exists and cannot be read, parsed or made sense of raises
      StateUnreadable, with the reason.
  S3  a good file loads with its own numbers.
  S4  save_state() will not write over an unreadable file; it writes over a
      good one and creates a missing one.
  S5  a cycle on an unreadable state refuses, names the reason, touches no
      venue, plans, seals and saves nothing, leaves the file's bytes exactly as
      they were, and keeps one byte-for-byte copy beside it.
  S6  the same cycle on a good state runs, and the day's two orders are still
      on record afterwards.
  S7  main() exits 4 on such a refusal and 0 without one.

WHAT THIS CANNOT SEE. One file: the trader's state. signal_ledger.py and
daily.py keep their own files and their readers are not driven here. Uses temp
folders and stubs only; opens no credential, calls no venue or node.
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

import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


Unreadable = getattr(T, "StateUnreadable", None) or type("NotDefinedBeforeTheFix", (Exception,), {})
TODAY = time.strftime("%Y-%m-%d")
GOOD = {"day": TODAY, "orders_today": [{"sym": "XLM", "usd": 20.0, "side": "sell"},
                                       {"sym": "ADA", "usd": 25.0, "side": "sell"}],
        "equity_peak": 1500.0, "equity_start_of_day": 1400.0, "closed_trades": [],
        "last_sold": {}, "sealed_signals": 7, "bought_total_usd": 123.0}


def state_at(content=None, mkdir=False):
    """A fresh temp path for T.STATE, holding `content` bytes (None = no file)."""
    p = os.path.join(tempfile.mkdtemp(), "trader_state.json")
    if mkdir:
        os.makedirs(p)
    elif content is not None:
        with open(p, "wb") as fh:
            fh.write(content)
    return p


@contextlib.contextmanager
def state_path(p):
    keep = T.STATE
    T.STATE = p
    try:
        yield p
    finally:
        T.STATE = keep


def try_load(p):
    with state_path(p):
        try:
            return "loaded", T.load_state()
        except Unreadable as e:
            return "unreadable", str(e)


BAD = {
    "an empty file (a torn write)": b"",
    "half a record": b'{"day": "2026-11-12", "orders_today": [{"usd": 2',
    "not JSON": b"orders_today = 2\n",
    "a list": b"[1, 2, 3]",
    "the literal NaN": b'{"day": "' + TODAY.encode() + b'", "orders_today": [], "equity_peak": NaN}',
    "the literal Infinity": b'{"bought_total_usd": Infinity}',
    "orders_today an object": b'{"orders_today": {}}',
    "equity_peak a string": b'{"equity_peak": "1500"}',
    "a negative lifetime buy total": b'{"bought_total_usd": -50.0}',
    "day a number": b'{"day": 20261112}',
    "bytes that are not UTF-8": b'{"day": "\xff\xfe"}',
}

# ---- S1 / S2 / S3 --------------------------------------------------------------
kind, st = try_load(state_at(None))
check("S1 a missing state file is a known empty state: no orders, nothing bought, no crash",
      kind == "loaded" and st.get("orders_today") == [] and st.get("bought_total_usd") == 0.0, (kind, st))
let = {k: try_load(state_at(v)) for k, v in BAD.items()}
let = {k: v for k, v in let.items() if v[0] != "unreadable"}
check("S2 each of these is StateUnreadable, never the defaults: " + "; ".join(BAD), not let, let)
kind, st = try_load(state_at(None, mkdir=True))
check("S2b ...and so is a path that exists and is not a file at all", kind == "unreadable", (kind, st))
kind, why = try_load(state_at(BAD["the literal NaN"]))
check("S2c ...and the reason is in words an operator can act on (it names NaN)",
      kind == "unreadable" and "NaN" in why, why)
kind, st = try_load(state_at(json.dumps(GOOD).encode()))
check("S3 a good file loads with ITS numbers: 2 orders today, $123 bought, peak $1,500",
      kind == "loaded" and len(st["orders_today"]) == 2 and st["bought_total_usd"] == 123.0
      and st["equity_peak"] == 1500.0, (kind, st))

# ---- S4: the writer --------------------------------------------------------------
torn = BAD["half a record"]
p = state_at(torn)
with state_path(p):
    try:
        T.save_state(dict(T._fresh_state()) if hasattr(T, "_fresh_state") else {"orders_today": []})
        raised = False
    except Unreadable:
        raised = True
check("S4a save_state() refuses to write over an unreadable file",
      raised and open(p, "rb").read() == torn, (raised, open(p, "rb").read()[:60]))
p = state_at(json.dumps(GOOD).encode())
with state_path(p):
    T.save_state(dict(GOOD, sealed_signals=8))
check("S4b ...writes over a good one", json.load(open(p, encoding="utf-8"))["sealed_signals"] == 8)
p = state_at(None)
with state_path(p):
    T.save_state(dict(GOOD))
check("S4c ...and creates a missing one", json.load(open(p, encoding="utf-8"))["bought_total_usd"] == 123.0)


# ---- S5 / S6: the cycle ----------------------------------------------------------
def cycle(p):
    """run_once with the REAL load_state/save_state on `p` and everything that
    would leave the process stubbed. Returns (what was touched, the output)."""
    touched = []
    names = ("node_status", "gather", "plan", "execute", "witness_ready", "seal_decision")
    keep = {k: getattr(T, k) for k in names}
    keep_rc = T.signal_ledger.record_cycle
    out = io.StringIO()
    try:
        T.node_status = lambda ports, timeout=4: []
        T.witness_ready = lambda cfg, reserve=None: None
        T.gather = lambda cfg: touched.append("gather") or {
            "positions": [], "unpriced": [], "venue_notes": [], "total": 0.0, "cash": 0.0}
        T.plan = lambda cfg, pf, week_spent=0.0: touched.append("plan") or ([], ["no orders (stub)"])
        T.execute = lambda *a, **kw: touched.append("execute") or []
        T.seal_decision = lambda c, rec: touched.append("seal") or (True, "stub")
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 7, "wins": 0, "mean_after_costs": -0.01,
            "p_value": 1.0, "clears": False, "why": "stub"}
        cfg = dict(T.DEFAULT_CONFIG, armed=False, seal_required=True, node_ports=[])
        with state_path(p), contextlib.redirect_stdout(out):
            T.run_once(cfg)
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle = keep_rc
    return touched, out.getvalue()


p = state_at(torn)
touched, out = cycle(p)
check("S5a a cycle on an unreadable state REFUSES and says why",
      "REFUSING THIS CYCLE" in out and "cannot be read" in out, out[-400:])
check("S5b ...it read no venue and planned, sealed and executed nothing", touched == [], touched)
check("S5c ...the state file's bytes are exactly what they were (it was NOT overwritten with "
      "a zeroed day)", open(p, "rb").read() == torn, open(p, "rb").read()[:80])
copies = [f for f in os.listdir(os.path.dirname(p)) if ".unreadable-" in f]
check("S5d ...and one byte-for-byte copy is kept beside it",
      len(copies) == 1 and open(os.path.join(os.path.dirname(p), copies[0]), "rb").read() == torn, copies)
cycle(p)
copies2 = [f for f in os.listdir(os.path.dirname(p)) if ".unreadable-" in f]
check("S5e a second refused cycle on the same bytes makes no second copy, and still refuses",
      copies2 == copies and open(p, "rb").read() == torn, copies2)
check("S5f the refusal is flagged for main() to turn into a non-zero exit",
      bool(getattr(T, "LAST_REFUSAL", None)), getattr(T, "LAST_REFUSAL", "no such flag"))

p = state_at(json.dumps(GOOD).encode())
touched, out = cycle(p)
after = json.load(open(p, encoding="utf-8"))
check("S6a the same cycle on a GOOD state runs: it gathers, plans and seals",
      touched[:2] == ["gather", "plan"] and "seal" in touched and "REFUSING" not in out, touched)
check("S6b ...and the day's two orders and the $123 are still on record afterwards",
      len(after["orders_today"]) == 2 and after["bought_total_usd"] == 123.0, after)
check("S6c ...with no refusal flagged", not getattr(T, "LAST_REFUSAL", None))


# ---- S7: the exit code -----------------------------------------------------------
def run_main(refusal):
    keep = (T.load_config, T.run_once, sys.argv, T.LAST_SEAL_OK)
    try:
        T.load_config = lambda: dict(T.DEFAULT_CONFIG)
        T.run_once = lambda cfg, plan_only=False: setattr(T, "LAST_REFUSAL", refusal)
        T.LAST_SEAL_OK = True
        sys.argv = ["covenant_trader.py", "--once"]
        with contextlib.redirect_stdout(io.StringIO()):
            return T.main()
    finally:
        T.load_config, T.run_once, sys.argv, T.LAST_SEAL_OK = keep
        T.LAST_REFUSAL = None


check("S7a main() exits 4 when the cycle was refused on its state", run_main("state unreadable (test)") == 4)
check("S7b ...and 0 when it was not", run_main(None) == 0)

n = len(results)
print(f"\nH2 state: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
