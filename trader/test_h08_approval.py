#!/usr/bin/env python3
"""H8 -- the day's approval binds the orders, expires, and a decline is final.

2026-11-12, hardening pass, item 8 of an independent review of this folder.

THE DEFECT. guards.preconditions asked covenant one question -- is today's
plan approved? -- and on yes, covenant_trader.execute placed whatever plan()
computed at run time. Nothing compared the orders placed with the orders
approved. The plan the operator approved at 07:10 and the orders sent at 09:00
came from two runs of a planner, and after a price move, a config edit or a
different build they are not the same orders.

WHAT IS HERE NOW. witness.approval_record() asks covenant for the record
behind the yes: the day, the plan's hash, every decision row for the day, and
the plan's proposed orders. guards.approval_reasons() holds each order to it:

  P1  an order the plan proposed -- same symbol, same side, no larger and at
      most 2% smaller -- is covered. Anything else is refused, with the reason:
      another symbol, the other side, larger, more than 2% smaller.
  P2  a plan with no orders approves no orders.
  P3  one proposed order is one placement: what today's record already holds
      uses it up.
  P4  a DECLINE anywhere in the day's record is final, before or after an
      approve.
  P5  an approval lasts 12 hours from when it was given, then it has expired;
      one dated in the future is not believed.
  P6  an approval for another plan hash, another day, or a record that cannot
      be read, is no approval.
  W   witness.approval_record: covenant without the function, a crash, a
      wrong-shaped answer -> ok=false with the reason; a stand-in covenant
      that has it is passed through.
  X   covenant_trader.execute places the approved order live and sends the
      unapproved one to the dry run only; with no record at all nothing goes
      live.

WHAT THIS CANNOT SEE -- AND IT MATTERS. covenant_daily_plan.approval_record
is COVENANT'S to provide and this suite drives a stand-in. Whether the real
covenant has that function, and whether the plan it shows the operator
proposes the same orders this trader plans, is UNDETERMINED from this
repository. Until covenant provides it every live order is refused with
"approval binding unavailable": that is the designed, fail-closed state, and
it means no live order at all. Stubs and temp folders only.
"""
from __future__ import annotations

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


reasons_for = getattr(G, "approval_reasons", lambda order, approval, orders_today=None, now=None: [])
NOW = time.time()
TODAY = time.strftime("%Y-%m-%d", time.localtime(NOW))
H = 3600.0
HASH = "9f2c" * 16


def record(orders=None, decisions=None, **over):
    r = {"ok": True, "date": TODAY, "plan_hash": HASH,
         "decisions": [{"decision": "approve", "at": NOW - 2 * H, "plan_hash": HASH}]
         if decisions is None else decisions,
         "orders": [{"sym": "XLM", "side": "sell", "usd": 25.0},
                    {"sym": "ADA", "side": "sell", "usd": 20.0}] if orders is None else orders}
    r.update(over)
    return r


def order(sym="XLM", side="sell", usd=25.0):
    return {"sym": sym, "side": side, "usd": usd, "qty": usd, "rule": "test"}


def why(o, rec=None, today=None, now=NOW):
    return reasons_for(o, record() if rec is None else rec, today, now)


# ---- P1: the order must be one the plan proposed ---------------------------------
check("P1a the proposed order itself is covered", why(order()) == [], why(order()))
check("P1b ...and so is the plan's other order", why(order("ADA", "sell", 20.0)) == [])
check("P1c 1.6% smaller ($24.60 against $25) is inside the 2% tolerance", why(order(usd=24.60)) == [])
for label, o, needle in (
        ("a symbol the plan does not name (ETH)", order("ETH"), "not in the approved plan"),
        ("the other side (a BUY of XLM)", order(side="buy"), "not in the approved plan"),
        ("one cent LARGER ($25.01)", order(usd=25.01), "no larger"),
        ("double ($50)", order(usd=50.0), "no larger"),
        ("2.4% smaller ($24.40)", order(usd=24.40), "at most 2%"),
        ("half ($12.50)", order(usd=12.50), "at most 2%")):
    r = why(o)
    check("P1d %s is refused, and says why" % label, len(r) == 1 and needle in r[0], r)
check("P1e the symbol is matched whatever its case in the record ('xlm')",
      why(order(), record(orders=[{"sym": "xlm", "side": "sell", "usd": 25.0}])) == [])

# ---- P2 --------------------------------------------------------------------------
r = why(order(), record(orders=[]))
check("P2 an approved plan with NO orders approves no orders", len(r) == 1 and "proposes NO orders" in r[0], r)

# ---- P3: one proposed order, one placement ----------------------------------------
placed = [{"sym": "XLM", "side": "sell", "usd": 25.0, "status": "PLACED"}]
r = why(order(), today=placed)
check("P3a the same order a second time in the day is refused: it has been placed",
      len(r) == 1 and "already" in r[0], r)
check("P3b ...the plan's OTHER order is still free", why(order("ADA", "sell", 20.0), today=placed) == [])
two = record(orders=[{"sym": "XLM", "side": "sell", "usd": 25.0}, {"sym": "XLM", "side": "sell", "usd": 25.0}])
check("P3c a plan that proposes the order TWICE covers it twice, and not a third time",
      why(order(), two, placed) == [] and why(order(), two, placed + placed) != [])
check("P3d an order whose venue answer was lost (UNKNOWN) uses its approval up too",
      why(order(), today=[{"sym": "XLM", "side": "sell", "usd": 25.0, "status": "UNKNOWN: timeout"}]) != [])

# ---- P4: a decline is final -------------------------------------------------------
ap = {"decision": "approve", "at": NOW - 2 * H, "plan_hash": HASH}
de = {"decision": "decline", "at": NOW - 1 * H, "plan_hash": HASH}
for label, rows in (("approve, then decline", [ap, de]),
                    ("decline, then approve", [dict(de, at=NOW - 3 * H), ap]),
                    ("a decline of ANOTHER plan hash, then an approve of this one",
                     [dict(de, at=NOW - 3 * H, plan_hash="0" * 64), ap]),
                    ("a decline alone", [de])):
    r = why(order(), record(decisions=rows))
    check("P4 %s: DECLINED, final for the day" % label, len(r) == 1 and "DECLINED" in r[0] and "final" in r[0], r)
check("P4b ...and with no decline in the record, two approves are simply approved",
      why(order(), record(decisions=[ap, dict(ap, at=NOW - 1 * H)])) == [])

# ---- P5: an approval expires ------------------------------------------------------
check("P5a an approval given 11 hours ago still covers the order",
      why(order(), record(decisions=[dict(ap, at=NOW - 11 * H)])) == [])
r = why(order(), record(decisions=[dict(ap, at=NOW - 13 * H)]))
check("P5b one given 13 hours ago has EXPIRED (it lasts 12)", len(r) == 1 and "EXPIRED" in r[0], r)
check("P5c ...unless it was given again since: the latest approve of this plan is the one that counts",
      why(order(), record(decisions=[dict(ap, at=NOW - 13 * H), dict(ap, at=NOW - 1 * H)])) == [])
# Twelve minutes apart, so that the two readings straddle the 12-hour mark
# without straddling a day (the record's date is set for each reading's clock).
a119 = dict(ap, at=NOW - 11.9 * H)
later = NOW + 0.2 * H
check("P5d the same approval, 11.9 hours old and covering the order, has expired twelve minutes "
      "later without anyone touching it",
      why(order(), record(decisions=[a119]), now=NOW) == []
      and "EXPIRED" in str(why(order(), record(decisions=[a119],
                                              date=time.strftime("%Y-%m-%d", time.localtime(later))),
                               now=later)))
r = why(order(), record(decisions=[dict(ap, at=NOW + 2 * H)]))
check("P5e an approval dated two hours in the FUTURE is not believed", len(r) == 1 and "future" in r[0], r)

# ---- P6: not this plan, not today, not readable --------------------------------------
r = why(order(), record(decisions=[dict(ap, plan_hash="0" * 64)]))
check("P6a an approve of ANOTHER plan hash is no approval of this plan", len(r) == 1 and "no approval" in r[0], r)
r = why(order(), record(date="2020-01-01"))
check("P6b a record for another day is no approval today", len(r) == 1 and "2020-01-01" in r[0], r)
unreadable = {
    "None": None, "a list": [], "ok false": {"ok": False, "why": "covenant exited 1"},
    "ok the text 'true'": dict(record(), ok="true"), "no plan hash": record(plan_hash=""),
    "decisions not a list": record(decisions={"decision": "approve"}),
    "a decision that is neither": record(decisions=[dict(ap, decision="maybe")]),
    "an approve with no time": record(decisions=[{"decision": "approve", "plan_hash": HASH}]),
    "orders not a list": record(orders="XLM"),
    "an order with a NaN amount": record(orders=[{"sym": "XLM", "side": "sell", "usd": float("nan")}]),
    "an order with no side": record(orders=[{"sym": "XLM", "usd": 25.0}]),
}
let = {k: reasons_for(order(), v, None, NOW) for k, v in unreadable.items()}    # straight in: None is a case
let = {k: v for k, v in let.items() if not (len(v) == 1 and "unavailable" in v[0])}
check("P6c a record that is " + "; ".join(unreadable) + " -> binding unavailable, nothing covered",
      not let, let)
check("P6d ...and the reason carries covenant's own words when it gave any",
      "covenant exited 1" in why(order(), unreadable["ok false"])[0])


# ---- W: the witness -----------------------------------------------------------------
def fake_covenant(body):
    d = tempfile.mkdtemp()
    open(os.path.join(d, "covenant_unified_v8.py"), "w").close()
    with open(os.path.join(d, "covenant_daily_plan.py"), "w", encoding="utf-8") as fh:
        fh.write(body)
    return d


ask = getattr(W, "approval_record", lambda now=None, cfg=None, runner=None: {"ok": True, "why": "the code before the fix"})
without = fake_covenant("def gate_reasons(now=None):\n    return []\n")
r = ask(cfg={"covenant_home": without})
check("W1 a covenant that has gate_reasons but NO approval_record: ok=false, and the reason says "
      "what is missing", r.get("ok") is False and "approval_record" in str(r.get("why")), r)
has = fake_covenant(
    "def approval_record(now=None):\n"
    "    return {'ok': True, 'date': %r, 'plan_hash': %r,\n"
    "            'decisions': [{'decision': 'approve', 'at': %r, 'plan_hash': %r}],\n"
    "            'orders': [{'sym': 'XLM', 'side': 'sell', 'usd': 25.0}], 'saw_now': now}\n"
    % (TODAY, HASH, NOW - H, HASH))
r = ask(now=NOW, cfg={"covenant_home": has})
check("W2 a covenant that has it: the record comes through the real runner unchanged, and "
      "covers the order", r.get("ok") is True and r.get("saw_now") == NOW
      and reasons_for(order(), r, [], NOW) == [], r)
check("W3 no covenant configured: ok=false", ask(cfg={}).get("ok") is False)
check("W4 covenant crashing: ok=false with its words",
      ask(cfg={"covenant_home": has}, runner=lambda *a: (False, None, "covenant exited 1: boom")) ==
      {"ok": False, "why": "covenant exited 1: boom"})
for label, ans in (("a list", []), ("a dict with no ok", {"date": TODAY}), ("ok the number 1", {"ok": 1})):
    check("W5 an answer that is %s: ok=false, not passed through" % label,
          ask(cfg={"covenant_home": has}, runner=lambda *a, v=ans: (True, v, "")).get("ok") is False)
check("W6 a `now` that is not epoch seconds: ok=false",
      ask(now="2026-11-12", cfg={"covenant_home": has}).get("ok") is False)
ran = []
r = ask(cfg={"armed": True, "covenant_home": has}, runner=lambda *a: ran.append(1) or (True, record(), ""))
check("W7 ARMED with an unpinned folder: ok=false and NO Python is run (H6's pin holds here too)",
      r.get("ok") is False and ran == [], (r, ran))


# ---- X: execute() ----------------------------------------------------------------------
class FakeVenue:
    name = "fake"

    def __init__(self):
        self.calls = []

    def has_credentials(self):
        return True

    # A clean, trade-only venue, for the later items of this pass (H9, H10).
    def key_permissions(self):
        return {"can_trade": True, "can_transfer": False, "can_withdraw": False, "source": "fake"}

    def open_orders_norm(self):
        return []

    def recent_fills(self, since=None):
        return []

    def reference_price(self, sym):
        return 1.0

    def place(self, sym, side, qty, live=False, **kw):
        self.calls.append((sym, side, live))
        return {"descr": "fake", "txid": "TX" if live else None}


UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
CFG = {"armed": True, "armed_until": UNTIL, "daily_plan_required": True, "seal_required": True,
       "min_sealed_signals": 0, "rule5_require_significance": False, "min_order_usd": 5.0,
       "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}


def run_execute(orders, approval, fetched=None, plan_only=False, cfg=None):
    """execute() with covenant's yes/no gate stubbed to YES, so that what
    decides is the binding. `fetched` stands in for witness.approval_record
    when no approval is handed in."""
    fv = FakeVenue()
    asked = []
    keep = (T.V.all_venues, T.STATE, T.save_state, W.daily_plan_gate_reasons,
            getattr(W, "approval_record", None))
    st = {"day": TODAY, "orders_today": []}
    try:
        T.V.all_venues = lambda: [fv]
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.save_state = lambda s: None
        W.daily_plan_gate_reasons = lambda now=None, cfg=None, runner=None: []
        W.approval_record = lambda now=None, cfg=None, runner=None: asked.append(1) or fetched
        full = [dict(o, px=1.0, at={"fake": 100.0}) for o in orders]
        kw = {"approval": approval} if approval is not None else {}
        try:
            res = T.execute(cfg or CFG, st, full, True, [], plan_only, **kw)
        except TypeError:                       # the code before the fix takes no approval
            res = T.execute(cfg or CFG, st, full, True, [], plan_only)
    finally:
        T.V.all_venues, T.STATE, T.save_state, W.daily_plan_gate_reasons = keep[:4]
        if keep[4] is not None:
            W.approval_record = keep[4]
    return res, fv, st, asked


res, fv, st, _ = run_execute([order(), order("ETH", "sell", 25.0)], record())
check("X1 the approved order goes LIVE; the one the plan never proposed goes to the DRY RUN only",
      fv.calls == [("XLM", "sell", True), ("ETH", "sell", False)]
      and [r["status"] for r in res] == ["PLACED", "VALIDATED"], (fv.calls, [r["status"] for r in res]))
check("X1b ...only the approved one is in the day's record, and the other says why it was held back",
      [r["sym"] for r in st["orders_today"]] == ["XLM"]
      and any("not in the approved plan" in b for b in res[1]["blocked_by"]), (st, res[1].get("blocked_by")))
res, fv, st, _ = run_execute([order(), order()], record())
check("X2 the same approved order twice in one cycle: the first goes live, the second does not",
      [c[2] for c in fv.calls] == [True, False] and len(st["orders_today"]) == 1, fv.calls)
res, fv, st, _ = run_execute([order()], record(decisions=[ap, de]))
check("X3 with a decline in the day's record nothing goes live",
      [c[2] for c in fv.calls] == [False] and st["orders_today"] == [], fv.calls)
res, fv, st, asked = run_execute([order()], None, fetched={"ok": False, "why": "covenant has no approval_record"})
check("X4 with no record handed in, the witness is ASKED; when it has none, nothing goes live "
      "and the reason is on the order", asked == [1] and [c[2] for c in fv.calls] == [False]
      and any("unavailable" in b for b in res[0]["blocked_by"]), (asked, fv.calls, res[0].get("blocked_by")))
res, fv, st, asked = run_execute([order()], None, fetched=record())
check("X5 ...and when the witness has the record, the approved order goes live",
      asked == [1] and [c[2] for c in fv.calls] == [True], (asked, fv.calls))
res, fv, st, asked = run_execute([order()], None, fetched=record(), cfg=dict(CFG, armed=False))
check("X6 a DISARMED cycle never asks for the record at all (nothing could go live)",
      asked == [] and [c[2] for c in fv.calls] == [False], (asked, fv.calls))

n = len(results)
print(f"\nH8 approval: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
