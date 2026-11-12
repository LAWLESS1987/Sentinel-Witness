"""G2 (review round 1, 2026-11-12): the live gate judges a BUY against the book the trader recorded
in its state file, not against the State its caller hands it. Driven both ways through the real
gate with the shared fixture. Offline: stand-in venue, temporary config and state, no network."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guards as G  # noqa: E402
from gated_fixture import Gated, Venue, order  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


def why(res, i=0):
    return res[i].get("blocked_by") or []


def book(equity=1000.0, peak=1000.0, start=1000.0, cash=900.0, positions=None):
    return G.State(equity_now=equity, equity_peak=peak, equity_start_of_day=start, closed_trades=[],
                   last_sold={}, positions=dict(positions or {"XLM": 100.0}), cash=cash)


def write_book(fx, state, **over):
    """Put a book in the fixture's state file the way run_once does, then return it."""
    fx.st["book"] = dict(G.book_snapshot(state), **over)
    fx.st["equity_peak"], fx.st["equity_start_of_day"] = state.equity_peak, state.equity_start_of_day
    with open(fx.state_path, "w", encoding="utf-8") as fh:
        json.dump(fx.st, fh)


BUY = order("XLM", "buy", 20.0)
CFG = {"allow_fiat_buys": True}

with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v], portfolio=book())
    check("B1 the pass case: a healthy recorded book and the same book handed in -- the buy goes live",
          v.live() == [True], (v.live(), why(res)))

# The finding itself: the account is down by half ON RECORD and the caller hands in a healthy book.
with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    write_book(fx, book(equity=500.0, peak=1000.0, start=1000.0, cash=450.0, positions={"XLM": 50.0}))
    res = fx.execute([dict(BUY)], [v], portfolio=book(), record_book=False)
    check("B2 a book down 50% on record, a healthy book handed in: refused, and the refusal names the difference",
          v.live() == [False] and any("differs from the book" in r for r in why(res)), (v.live(), why(res)))
    check("B3 ...and the guards were evaluated on the RECORDED book: a drawdown or daily-loss block is among the reasons",
          any(("drawdown" in r.lower()) or ("daily" in r.lower()) or ("loss" in r.lower()) for r in why(res)), why(res))

with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    low_cash = book(cash=50.0, positions={"XLM": 100.0, "ADA": 850.0})
    write_book(fx, low_cash)
    res = fx.execute([dict(BUY)], [v], portfolio=book(), record_book=False)
    check("B4 cash at 5% on record, cash at 90% handed in: the cash floor is read from the record and refuses",
          v.live() == [False] and any("cash_floor" in r for r in why(res)), (v.live(), why(res)))

with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    res = fx.execute([dict(BUY)], [v], portfolio=book(), record_book=False)
    check("B5 no measured book in the state file: the buy is refused, not judged on what was handed in",
          v.live() == [False] and any("no measured book" in r for r in why(res)), (v.live(), why(res)))

with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    write_book(fx, book(), at=time.time() - G.BOOK_VALID_S - 60)
    res = fx.execute([dict(BUY)], [v], portfolio=book(), record_book=False)
    check("B6 a recorded book older than the limit is refused", v.live() == [False]
          and any("s old" in r for r in why(res)), (v.live(), why(res)))

for label, over in (("a NaN equity", {"equity_now": float("nan")}), ("a negative cash", {"cash": -1.0}),
                    ("positions that are not a table", {"positions": [1, 2]}),
                    ("a position that is not a number", {"positions": {"XLM": "lots"}})):
    with Gated(approved=[BUY], cfg=CFG) as fx:
        v = Venue()
        write_book(fx, book(), **over)
        res = fx.execute([dict(BUY)], [v], portfolio=book(), record_book=False)
        check("B7 a recorded book with %s is refused" % label, v.live() == [False]
              and any("cannot be read" in r for r in why(res)), (v.live(), why(res)))

with Gated(approved=[BUY], cfg=CFG) as fx:
    v = Venue()
    write_book(fx, book(equity=1500.0, peak=1000.0))
    res = fx.execute([dict(BUY)], [v], portfolio=book(equity=1500.0), record_book=False)
    check("B8 a recorded book larger than the recorded peak is refused (the peak only rises)",
          v.live() == [False] and any("larger than the peak" in r for r in why(res)), (v.live(), why(res)))

# the helpers, directly
now = 1_800_000_000.0
snap = G.book_snapshot(book(), now=now)
good, w = G.book_from_disk({"book": snap, "equity_peak": 1000.0, "equity_start_of_day": 1000.0}, now=now + 10)
check("B9 book_from_disk returns the five numbers for a fresh, readable record",
      w is None and good == {"equity_now": 1000.0, "equity_peak": 1000.0, "equity_start_of_day": 1000.0,
                             "cash": 900.0, "positions": {"XLM": 100.0}}, (good, w))
check("B10 ...and refuses a state that is not a table, has no peak, or whose book was written in the future",
      G.book_from_disk(None, now=now)[0] is None
      and G.book_from_disk({"book": snap, "equity_start_of_day": 1000.0}, now=now)[0] is None
      and G.book_from_disk({"book": snap, "equity_peak": 1000.0, "equity_start_of_day": 1000.0}, now=now - 3600)[0] is None)
check("B11 the three new keys are compared between the state handed in and the state on disk",
      {"equity_peak", "equity_start_of_day", "book"} <= set(G.STATE_GATE_KEYS))

# a sell is not judged on the book (a guard never stops a sale): unchanged behaviour
SELL = order("XLM", "sell", 20.0)
with Gated(approved=[SELL]) as fx:
    v = Venue()
    res = fx.execute([dict(SELL)], [v])
    check("B12 a SELL still goes live with no recorded book: the book guards are buy-side only",
          v.live() == [True], (v.live(), why(res)))

n = len(results)
print(f"\nG2 book on disk: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
