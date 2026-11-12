#!/usr/bin/env python3
"""H3 -- a cycle that cannot price or read the whole book plans no orders.

2026-11-12, hardening pass, item 3 of an independent review of this folder.

THE DEFECT. gather() lists a holding it could not price under `unpriced` and
leaves it out of `total`. plan() then computed every other holding's share
against that smaller total. Measured in U2 below: a coin that is 15% of a
$2,700 book reads as 57% of it once the other holding has no price, the
concentration cap asks for it to be trimmed to 20%, and a SELL is planned
because a price feed was down or two venues disagreed. A venue whose balances
could not be read at all has the same effect and was not even listed.

  U1  one unpriced holding: plan() returns NO orders and names the holding and
      why it has no price. The same book fully priced plans what the rules say.
  U2  the sale the defect produced, shown: the priced half of the book alone
      (what plan() used to see) does plan a sell.
  U3  a credentialed venue that could not be read: no orders, and it is named.
  U4  gather() records which venues failed; "no credential" is not a failure.
  U5  an incomplete book does not set the day's opening equity and does not
      write the once-only starting total; a whole book does both.
  U6  the floors are not read (and so not written) on an incomplete book.

WHAT THIS CANNOT SEE. A holding dropped by the operator's own `symbols` filter
is left out of `total` the same way, on purpose, and is not treated as missing
here. Stubs only: no venue, no price feed, no credential.
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

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


SCOPE = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06",
         "words": "Everything else is good to sell in trade."}
CFG = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0,
       "min_order_usd": 5.0, "max_orders_per_day": 2, "allow_fiat_buys": False,
       "weekly_fiat_budget_usd": 0.0, "trading_scope": dict(SCOPE)}


def pos(sym, qty, px, regime="UP"):
    return {"sym": sym, "qty": qty, "px": px, "val": qty * px, "regime": regime, "at": {"fake": qty}}


def run_plan(positions, cash, unpriced=(), venues_failed=()):
    """plan() on a stubbed book. Returns (orders, notes, floors_read)."""
    held = {p["sym"]: p["qty"] for p in positions}
    read = []
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf, path=None: read.append(1) or (dict(held), held, [])
        total = sum(p["val"] for p in positions) + cash
        o, n = T.plan(dict(CFG), {"positions": positions, "cash": cash, "total": total,
                                  "unpriced": list(unpriced), "venue_notes": [],
                                  "venues_failed": list(venues_failed)})
        return o, n, len(read)
    finally:
        T.reserve_baseline = keep


# The book: $100 of XLM, $2,000 of ETH, $400 cash = $2,500. XLM is 4%, ETH 80%.
XLM, ETH = pos("XLM", 100.0, 1.0), pos("ETH", 1.0, 2000.0)
NO_PRICE = {"sym": "ETH", "qty": 1.0, "why": "venues disagree by 3.1%"}

# ---- U1 ------------------------------------------------------------------------
o, n, floors = run_plan([XLM], cash=400.0, unpriced=[NO_PRICE])
check("U1a with ETH unpriced, plan() returns NO orders at all", o == [], o)
check("U1b ...and says which holding and why, in one note",
      len(n) == 1 and "ETH" in n[0] and "venues disagree by 3.1%" in n[0] and "NO orders" in n[0], n)
o_full, n_full, _ = run_plan([XLM, ETH], cash=400.0)
check("U1c the same book fully priced plans what the rules say: ETH (80%) is trimmed, XLM (4%) is not",
      [x["sym"] for x in o_full] == ["ETH"] and o_full[0]["side"] == "sell", (o_full, n_full))

# ---- U2: the sale the defect produced --------------------------------------------
BIG_XLM = pos("XLM", 400.0, 1.0)             # $400 of a real $2,700 book = 15%, under the 20% cap
o_whole, _, _ = run_plan([BIG_XLM, ETH], cash=300.0)
o_blind, _, _ = run_plan([BIG_XLM], cash=300.0)          # what plan() saw: ETH simply absent
o_now, n_now, _ = run_plan([BIG_XLM], cash=300.0, unpriced=[NO_PRICE])
check("U2a XLM at 15% of the whole book is not sold by the concentration cap",
      not [x for x in o_whole if x["sym"] == "XLM"], o_whole)
check("U2b the same XLM with ETH dropped from the total reads as 57% and IS sold -- the defect",
      [x["sym"] for x in o_blind] == ["XLM"] and o_blind[0]["side"] == "sell", o_blind)
check("U2c ...and when the plan is told ETH is unpriced, that sale is not planned",
      o_now == [] and "ETH" in n_now[0], (o_now, n_now))

# ---- U3 ------------------------------------------------------------------------
o, n, _ = run_plan([BIG_XLM], cash=300.0, venues_failed=["kraken"])
check("U3a a credentialed venue that could not be read: no orders, and the venue is named",
      o == [] and len(n) == 1 and "kraken" in n[0] and "could not be read" in n[0], (o, n))
check("U3b book_incomplete() is empty for a whole book and lists both kinds otherwise",
      T.book_incomplete({"unpriced": [], "venues_failed": []}) == []
      and len(T.book_incomplete({"unpriced": [NO_PRICE], "venues_failed": ["kraken"]})) == 2)


# ---- U4: gather() ----------------------------------------------------------------
class V1:
    def __init__(self, name, bal=None, cred=True, err=None):
        self.name, self._bal, self._cred, self._err = name, bal or {}, cred, err

    def has_credentials(self):
        return self._cred

    def balances(self):
        if self._err:
            raise T.V.VenueError(self._err)
        return dict(self._bal)


def run_gather(venues):
    keep_all, keep_pre = T.V.all_venues, T.daily.prefetch
    try:
        T.V.all_venues = lambda: venues
        T.daily.prefetch = lambda syms, source: {s: (1.0, [1.0] * 200, None, [], []) for s in syms}
        return T.gather({"symbols": []})
    finally:
        T.V.all_venues, T.daily.prefetch = keep_all, keep_pre


pf = run_gather([V1("a", {"XLM": 100.0, "USD": 50.0}), V1("b", err="HTTP 503: down"), V1("c", cred=False)])
check("U4a gather() names the credentialed venue that failed, and only it",
      pf["venues_failed"] == ["b"] and pf["venues_read"] == ["a"], pf)
check("U4b ...a venue with no credential is a note, not a failure",
      any(x.startswith("c: no credential") for x in pf["venue_notes"]) and "c" not in pf["venues_failed"], pf)
pf_ok = run_gather([V1("a", {"XLM": 100.0, "USD": 50.0}), V1("c", cred=False)])
check("U4c ...and a clean read has none", pf_ok["venues_failed"] == [] and not T.book_incomplete(pf_ok), pf_ok)


# ---- U5 / U6: the cycle -----------------------------------------------------------
def cycle(pf):
    """run_once on a stubbed portfolio. Returns (saved state, set_starting_total
    calls, orders handed to execute, floors read)."""
    saved, starts, handed, floors = {}, [], [], []
    names = ("node_status", "gather", "execute", "witness_ready", "seal_decision",
             "load_state", "save_state", "reserve_baseline", "STATE")
    keep = {k: getattr(T, k) for k in names}
    keep_rc, keep_set = T.signal_ledger.record_cycle, G.set_starting_total
    held = {p["sym"]: p["qty"] for p in pf["positions"]}
    try:
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.node_status = lambda ports, timeout=4: []
        T.witness_ready = lambda cfg, reserve=None: None
        T.gather = lambda cfg: pf
        T.load_state = lambda: {"orders_today": [], "day": "", "equity_peak": 0.0,
                                "equity_start_of_day": 0.0, "closed_trades": [],
                                "last_sold": {}, "sealed_signals": 0, "bought_total_usd": 0.0}
        T.save_state = lambda st: saved.update(st)
        T.reserve_baseline = lambda p, path=None: floors.append(1) or (dict(held), held, [])
        T.execute = lambda cfg, st, orders, *a, **kw: handed.extend(orders) or []
        T.seal_decision = lambda c, rec: (True, "stub")
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 0, "wins": 0, "clears": False, "why": "stub"}
        G.set_starting_total = lambda total, path=None: starts.append(total) or total
        cfg = dict(T.DEFAULT_CONFIG, **CFG)
        cfg.update(armed=False, seal_required=True, node_ports=[])
        with contextlib.redirect_stdout(io.StringIO()):
            T.run_once(cfg)
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle, G.set_starting_total = keep_rc, keep_set
    return saved, starts, handed, len(floors)


def book(positions, cash, unpriced=(), venues_failed=()):
    return {"positions": positions, "cash": cash,
            "total": sum(p["val"] for p in positions) + cash, "unpriced": list(unpriced),
            "venue_notes": [], "venues_read": ["fake"], "venues_failed": list(venues_failed)}


saved, starts, handed, floors = cycle(book([BIG_XLM], 300.0, unpriced=[NO_PRICE]))
check("U5a an incomplete book does not become the day's opening equity",
      not saved.get("equity_start_of_day"), saved.get("equity_start_of_day"))
check("U5b ...and the once-only starting total is NOT written from it", starts == [], starts)
check("U5c ...and no order reaches execute()", handed == [], handed)
check("U6 ...and the floor file is not read, so nothing can be written to it", floors == 0, floors)
saved, starts, handed, floors = cycle(book([BIG_XLM, ETH], 300.0))
check("U5d a whole book sets the day's opening equity ($2,700)",
      abs((saved.get("equity_start_of_day") or 0) - 2700.0) < 1e-9, saved.get("equity_start_of_day"))
check("U5e ...records the starting total from it, once", starts == [2700.0], starts)
check("U5f ...and its planned sell does reach execute()",
      [o["sym"] for o in handed] == ["ETH"] and floors == 1, (handed, floors))

n = len(results)
print(f"\nH3 unpriced: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
