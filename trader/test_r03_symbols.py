#!/usr/bin/env python3
"""R3 -- `symbols` says what may be traded, not what is held.

2026-11-12, review round one. Listed in the hardening pass's own "what a
reviewer will still find", and true: H3 stopped an UNPRICED holding shrinking
the book's total, and left the same hole one line above it.

THE DEFECT. covenant_trader.gather() dropped every holding that is not in the
config's `symbols` list before it was priced. It was then in neither
`positions` nor `unpriced`, and not in `total`. Every other holding's share of
the book was computed against the smaller total: with XLM in the list and a
larger ETH holding outside it, XLM at 15% of a $2,700 book read as 57%, the
concentration cap asked for it to be trimmed, and a sale was planned.

WHAT IS HERE NOW. Every holding is priced. One outside `symbols` goes to
pf["outside"]: counted in the total, printed, in the guards' book, and never
given an order. One outside `symbols` that cannot be priced is unpriced like
any other, and plan() plans nothing.

  S1  gather: the holding outside `symbols` is counted in the total and kept
      apart from the tradeable positions.
  S2  plan: XLM at 15% of the real book is not sold (it was).
  S3  the holding outside `symbols` is never given an order, though it is
      far over the concentration cap.
  S4  outside `symbols` AND unpriced: plan() plans nothing, and names it.
  S5  with no `symbols` list every holding is tradeable, as before.
  S6  a cycle prints the holding, and the guards judge cash against the whole
      book (11%, not 43%).

WHAT THIS CANNOT SEE. A holding at a venue this machine has no credential for
is still invisible; nothing here can count what it cannot read. Stubs only.
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
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


class Book:
    name = "fake"

    def __init__(self, bal):
        self._bal = bal

    def has_credentials(self):
        return True

    def balances(self):
        return dict(self._bal)

    def open_orders_norm(self):
        return []

    def recent_fills(self, since=None):
        return []


CLOSES = [1.0] * 200
PRICES = {"XLM": (1.0, CLOSES, None, [], []), "ETH": (2000.0, [1500.0] * 200, None, [], [])}
HOLD = {"XLM": 400.0, "ETH": 1.0, "USD": 300.0}                 # $400 + $2,000 + $300 = $2,700


def run_gather(symbols, prices=None):
    keep_all, keep_pre = T.V.all_venues, T.daily.prefetch
    asked = []
    try:
        T.V.all_venues = lambda: [Book(HOLD)]
        T.daily.prefetch = lambda syms, source: asked.extend(syms) or {
            s: (prices or PRICES)[s] for s in syms if s in (prices or PRICES)}
        return T.gather({"symbols": symbols}), asked
    finally:
        T.V.all_venues, T.daily.prefetch = keep_all, keep_pre


SCOPE = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06", "words": "x"}
CFG = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0, "min_order_usd": 5.0,
       "max_orders_per_day": 2, "allow_fiat_buys": False, "weekly_fiat_budget_usd": 0.0,
       "trading_scope": SCOPE}


def run_plan(pf):
    held = {p["sym"]: p["qty"] for p in pf["positions"]}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf_, path=None: (dict(held), held, [])
        return T.plan(dict(CFG), pf)
    finally:
        T.reserve_baseline = keep


# ---- S1 -----------------------------------------------------------------------------
pf, asked = run_gather(["XLM"])
check("S1a with symbols=[XLM] and ETH also held: the total is the WHOLE book, $2,700 (it was $700)",
      abs(pf["total"] - 2700.0) < 1e-9, pf["total"])
check("S1b ...XLM is the one tradeable position, and ETH is listed apart as held outside `symbols`",
      [p["sym"] for p in pf["positions"]] == ["XLM"]
      and [(p["sym"], p["val"]) for p in pf.get("outside", [])] == [("ETH", 2000.0)], pf)
check("S1c ...ETH was priced like any holding (it used to be dropped before pricing)",
      sorted(asked) == ["ETH", "XLM"], asked)

# ---- S2 / S3 --------------------------------------------------------------------------
orders, notes = run_plan(pf)
check("S2 XLM is 15% of the real book, under the 20% cap: NO sale is planned for it",
      not [o for o in orders if o["sym"] == "XLM"], (orders, notes))
blind = dict(pf, total=700.0, outside=[])                       # what plan() used to be handed
o_blind, _ = run_plan(blind)
check("S2b ...the defect, shown: the same positions against the $700 total it used to get DO "
      "plan the XLM sale", [o["sym"] for o in o_blind] == ["XLM"] and o_blind[0]["side"] == "sell", o_blind)
check("S3 ETH is 74% of the book and outside `symbols`: it is never given an order",
      not [o for o in orders if o["sym"] == "ETH"] and orders == [], orders)

# ---- S4 ---------------------------------------------------------------------------------
pf_np, _ = run_gather(["XLM"], prices={"XLM": PRICES["XLM"], "ETH": (None, None, "venues disagree by 4%", [], [])})
orders, notes = run_plan(pf_np)
check("S4a outside `symbols` AND unpriced: it is listed as unpriced, not silently dropped",
      [u["sym"] for u in pf_np["unpriced"]] == ["ETH"] and pf_np.get("outside") == [], pf_np)
check("S4b ...and plan() plans NOTHING, naming it", orders == [] and len(notes) == 1 and "ETH" in notes[0], (orders, notes))

# ---- S5 ----------------------------------------------------------------------------------
pf_all, _ = run_gather([])
orders, notes = run_plan(pf_all)
check("S5 with no `symbols` list both holdings are tradeable positions, nothing is 'outside', and "
      "the plan trims ETH (74%) as the rules say", [p["sym"] for p in pf_all["positions"]] == ["ETH", "XLM"]
      and pf_all.get("outside") == [] and [o["sym"] for o in orders] == ["ETH"], (pf_all, orders))


# ---- S6: a cycle ----------------------------------------------------------------------------
def cycle(pf_):
    names = ("node_status", "gather", "execute", "witness_ready", "seal_decision", "load_state",
             "save_state", "reserve_baseline", "STATE")
    keep = {k: getattr(T, k) for k in names}
    keep_rc, keep_set = T.signal_ledger.record_cycle, G.set_starting_total
    held = {p["sym"]: p["qty"] for p in pf_["positions"]}
    out = io.StringIO()
    try:
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.node_status = lambda ports, timeout=4: []
        T.witness_ready = lambda cfg, reserve=None: None
        T.gather = lambda cfg: pf_
        T.load_state = lambda: {"orders_today": [], "day": "", "equity_peak": 0.0,
                                "equity_start_of_day": 0.0, "closed_trades": [], "last_sold": {},
                                "sealed_signals": 0, "bought_total_usd": 0.0}
        T.save_state = lambda st: None
        T.reserve_baseline = lambda p, path=None: (dict(held), held, [])
        T.execute = lambda *a, **kw: []
        T.seal_decision = lambda c, rec: (True, "stub")
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 0, "wins": 0, "clears": False, "why": "stub"}
        G.set_starting_total = lambda total, path=None: total
        cfg = dict(T.DEFAULT_CONFIG, **CFG)
        cfg.update(armed=False, seal_required=True, node_ports=[], symbols=["XLM"])
        with contextlib.redirect_stdout(out):
            T.run_once(cfg)
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle, G.set_starting_total = keep_rc, keep_set
    return out.getvalue()


text = cycle(pf)
check("S6a a cycle prints the holding outside `symbols`, with its value, and says it is counted "
      "and never traded", any("ETH" in l and "not in `symbols`" in l and "2,000.00" in l
                              for l in text.splitlines()), text[:900])
check("S6b ...and the guards judge cash against the WHOLE book: 11.1%, not the 42.9% of the "
      "smaller total", any("cash_floor" in l and "11.1%" in l for l in text.splitlines())
      and "42.9%" not in text, [l for l in text.splitlines() if "cash" in l])

n = len(results)
print(f"\nR3 symbols: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
