#!/usr/bin/env python3
"""H10 -- a refusal is not "maybe booked", an open order is seen, a sale is
proceeds only once it has filled, and no order goes without a price band.

2026-11-12, hardening pass, item 10 of an independent review of this folder.

FOUR DEFECTS, ONE SUBJECT: what happens to an order after it is decided.

  (a) Kraken answers a rejected order with HTTP 200 and an `error` list.
      covenant_trader.execute freed the day's row only for "HTTP 4...", so
      every Kraken rejection was booked as "unknown, may be booked" and used
      one of the day's two orders. This file's own pre-flight refusals (a
      size that rounds to zero) did the same. And Kraken orders carried no
      client reference at all; Robinhood's was random per attempt.
  (b) A placed order was recorded and never looked at again. A limit order
      still resting on the book was invisible, the next cycle planned the same
      sale on top of it, and its dollars counted as raised from the moment it
      was placed.
  (c) Kraken and Robinhood orders were MARKET orders: no price band at all.

WHAT THE VENUES DOCUMENT, read 2026-11-12, and what is assumed from it:

  Kraken AddOrder: cl_ord_id (a 32-hex "short UUID" is one accepted form),
    mutually exclusive with userref; "Kraken verifies cl_ord_id uniqueness
    across open orders" -- so it is NOT an idempotency key once an order has
    closed. ordertype limit, timeinforce IOC, validate. OpenOrders: result.
    open keyed by txid, each with descr.pair, descr.type, cl_ord_id.
    TradesHistory: result.trades, each with ordertxid, pair, type, cost, vol,
    time.  docs.kraken.com/api-reference/trading/add-order
  Kraken errors: the list in KrakenVenue.DEFINITE_ERRORS and every "EOrder:"
    are read as rejections. The documentation describes them as validation or
    permission failures; it does NOT say in so many words that no order was
    placed -- that is this file's reading. EService:Unavailable, EService:
    Busy, EService:Deadline elapsed ("the order may have been placed despite
    the timeout") and EGeneral:Internal error stay ambiguous.
    docs.kraken.com/exchange/guides/general/errors
  Coinbase: a client_order_id already seen returns the existing order; List
    Orders (order_status=OPEN) and List Fills (order_id, product_id, side,
    price, size, size_in_quote, trade_time, trade_type).
    docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders
  Robinhood: client_order_id is a UUID "for idempotency validation";
    limit_order_config carries asset_quantity and limit_price. What a filled
    order looks like was not confirmed, so its fills are NOT read.
    docs.robinhood.com/crypto/trading

  R  the client reference: the same for the same order, different for any
     other; every adapter accepts it and a reference price.
  E  Kraken's error list: documented rejections are VenueRefused, the rest
     are not; execute frees the row for the one and keeps it for the other.
  B  the band: with a reference price Kraken sends a limit, immediate-or-
     cancel, at the band's edge; Coinbase refuses a touch outside the band
     before sending; Robinhood sends a limit; execute hands both through.
  O  open orders: gather reads them, plan plans nothing for that symbol, and
     plans nothing at all when a venue's open orders cannot be read.
  F  fills: a placed sale is UNCONFIRMED and raises nothing until the venue's
     fills say otherwise; then it is what filled.

WHAT THIS CANNOT SEE. Every venue here is a fake or has its transport
stubbed: the field names are as documented, not as observed. A row whose
answer was lost has no order id and is never confirmed by this code -- it
stays UNKNOWN for an operator to read. plan()'s own R6 arithmetic still
counts a sale planned in the SAME cycle as cash on hand; that is unchanged
and is not driven here.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import venues as V           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


Refused = getattr(V, "VenueRefused", None) or type("NotDefinedBeforeTheFix", (Exception,), {})
TODAY = time.strftime("%Y-%m-%d")

# ---- R: the client reference -------------------------------------------------------
ref = getattr(V, "client_ref", lambda *a: "")
a = ref(TODAY, "XLM", "sell", "20.00000000", 0)
check("R1 the reference is 32 hex characters and the same for the same order",
      len(a) == 32 and all(c in "0123456789abcdef" for c in a)
      and a == ref(TODAY, "xlm", "SELL", "20.00000000", 0), a)
others = [ref("2020-01-01", "XLM", "sell", "20.00000000", 0), ref(TODAY, "ADA", "sell", "20.00000000", 0),
          ref(TODAY, "XLM", "buy", "20.00000000", 0), ref(TODAY, "XLM", "sell", "21.00000000", 0),
          ref(TODAY, "XLM", "sell", "20.00000000", 1)]
check("R2 ...and different for another day, symbol, side, size or sequence", len(set(others + [a])) == 6, others)
lacking = [v.name for v in V.all_venues()
           if not {"ref_px", "client_ref"} <= set(inspect.signature(v.place).parameters)
           or not getattr(v, "LIVE_ORDER", None)]
check("R3 every adapter in all_venues() takes a reference price and a client reference, and "
      "states what its live order is", not lacking and len(V.all_venues()) >= 3, lacking)


# ---- a Kraken with its transport stubbed ------------------------------------------------
class Kraken(V.KrakenVenue):
    def __init__(self, answer=None):
        super().__init__()
        self.sent, self.answer = [], answer if answer is not None else {"txid": ["OABC-1"], "descr": {"order": "x"}}
        self._pairs = {"XLM": {"pair": "XXLMZUSD", "symbol": "XLM", "altname": "XLMUSD", "wsname": "XLM/USD",
                               "lot_decimals": 8, "pair_decimals": 6, "ordermin": 1.0, "costmin": 0.5}}

    def _private(self, endpoint, data=None):
        self.sent.append((endpoint, dict(data or {})))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


# ---- E: Kraken's error list --------------------------------------------------------------
definite = ["EOrder:Insufficient funds", "EOrder:Order minimum not met", "EOrder:Cannot open position",
            "EGeneral:Invalid arguments", "EGeneral:Permission denied", "EAPI:Invalid key",
            "EAPI:Invalid signature", "EAPI:Invalid nonce", "EAPI:Rate limit exceeded"]
ambiguous = ["EService:Unavailable", "EService:Busy", "EService:Deadline elapsed", "EGeneral:Internal error",
             "ESomething:Never seen before"]
is_def = getattr(V.KrakenVenue, "definite_refusal", lambda e: False)
check("E1 each documented Kraken rejection is read as definite: " + ", ".join(definite),
      all(is_def(e) for e in definite), [e for e in definite if not is_def(e)])
check("E2 ...and each of these is NOT (the order may have been placed): " + ", ".join(ambiguous),
      not any(is_def(e) for e in ambiguous), [e for e in ambiguous if is_def(e)])


def kraken_reply(errors):
    """What KrakenVenue._private raises for this error list, with only the
    HTTP layer and the credential stubbed."""
    k = V.KrakenVenue()
    k._creds = lambda: ("k", "c2VjcmV0")
    keep = V._http
    try:
        V._http = lambda req, timeout=30: {"error": list(errors), "result": {}}
        k._private("AddOrder", {"pair": "XXLMZUSD"})
        return "no error"
    except Refused:
        return "refused"
    except V.VenueError:
        return "unknown"
    finally:
        V._http = keep


check("E3 the real _private raises VenueRefused for a documented rejection, and a plain "
      "VenueError for an ambiguous one", kraken_reply(["EOrder:Insufficient funds"]) == "refused"
      and kraken_reply(["EService:Unavailable"]) == "unknown")
check("E4 a reply that mixes the two is ambiguous: every entry must be a rejection",
      kraken_reply(["EOrder:Insufficient funds", "EService:Busy"]) == "unknown")


def _try(venue, qty, **kw):
    try:
        return venue.place("XLM", "sell", qty, live=True, **kw)
    except Exception as e:                                       # noqa: BLE001
        return e


k = Kraken()
e_zero, e_min = _try(k, 0.000000001), _try(k, 0.5)
check("E5 a size that rounds to zero, or under the pair's minimum, is VenueRefused and nothing is sent",
      isinstance(e_zero, Refused) and isinstance(e_min, Refused) and k.sent == [], (e_zero, e_min, k.sent))


# ---- execute(): what a refusal does to the day's record ----------------------------------
UNTIL = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
CFG = {"armed": True, "armed_until": UNTIL, "daily_plan_required": False, "seal_required": True,
       "min_sealed_signals": 0, "rule5_require_significance": False, "min_order_usd": 5.0,
       "max_order_usd": 25.0, "max_orders_per_day": 2, "max_daily_notional_usd": 50.0}


class Fake:
    name = "fake"

    def __init__(self, behaviour=None):
        self.calls, self.b = [], behaviour

    def has_credentials(self):
        return True

    def key_permissions(self, fetch=None):
        return {"can_trade": True, "can_transfer": False, "can_withdraw": False, "source": "fake"}

    def place(self, sym, side, qty, live=False, ref_px=None, client_ref=None):
        self.calls.append({"sym": sym, "live": live, "ref_px": ref_px, "client_ref": client_ref})
        if isinstance(self.b, Exception):
            raise self.b
        return {"descr": "fake", "txid": "TX-%d" % len(self.calls) if live else None}


def run_execute(venue, orders, st=None):
    keep = (T.V.all_venues, T.STATE, T.save_state)
    st = st if st is not None else {"day": TODAY, "orders_today": []}
    try:
        T.V.all_venues = lambda: [venue]
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.save_state = lambda s: None
        res = T.execute(CFG, st, orders, True, [])
    finally:
        T.V.all_venues, T.STATE, T.save_state = keep
    return res, st


def order(sym="XLM", usd=20.0, px=2.0, side="sell"):
    return {"sym": sym, "side": side, "qty": usd / px, "usd": usd, "px": px, "rule": "test", "at": {"fake": 100.0}}


res, st = run_execute(Fake(Refused("kraken: EOrder:Insufficient funds")), [order()])
check("E6 a venue's definite refusal: the order is REFUSED and its row is FREED -- the day's "
      "count is not charged for an order that never existed (it used to stay as UNKNOWN)",
      res[0]["status"] == "REFUSED" and st["orders_today"] == [], (res[0]["status"], st["orders_today"]))
res, st = run_execute(Fake(V.VenueError("kraken: EService:Unavailable")), [order()])
check("E7 an ambiguous answer: the row STAYS as UNKNOWN and the day's count is charged",
      res[0]["status"] == "UNKNOWN" and len(st["orders_today"]) == 1
      and st["orders_today"][0]["status"].startswith("UNKNOWN"), (res[0]["status"], st["orders_today"]))
res, st = run_execute(Fake(V.VenueError("HTTP 408: request timeout")), [order()])
check("E8 HTTP 408 is a timeout, not a refusal: the row stays", len(st["orders_today"]) == 1, st["orders_today"])
res, st = run_execute(Fake(V.VenueError("HTTP 400: bad size")), [order()])
check("E9 ...and any other 4xx still frees it, as before", st["orders_today"] == [], st["orders_today"])

# ---- B: the band -----------------------------------------------------------------------------
BAND = getattr(V, "PRICE_BAND", None)
check("B0 the band is 1% of the reference price", BAND == 0.01, BAND)
k = Kraken()
k.place("XLM", "sell", 10.0, live=True, ref_px=2.0, client_ref=a)
ep, d = k.sent[-1]
check("B1 Kraken, with a reference price: a LIMIT, immediate-or-cancel, at the band's edge "
      "($1.98 for a sale against $2.00) -- not a market order",
      ep == "AddOrder" and d.get("ordertype") == "limit" and d.get("timeinforce") == "IOC"
      and abs(float(d.get("price", 0)) - 1.98) < 1e-9, d)
check("B1b ...carrying the client reference as cl_ord_id, and no validate flag (it is live)",
      d.get("cl_ord_id") == a and "validate" not in d, d)
k.place("XLM", "buy", 10.0, live=False, ref_px=2.0, client_ref=a)
ep, d = k.sent[-1]
check("B1c a purchase's ceiling is $2.02, and a dry run validates the same limit and sends no reference",
      abs(float(d.get("price", 0)) - 2.02) < 1e-9 and d.get("validate") == "true" and "cl_ord_id" not in d, d)
check("B1d the edge is rounded so that rounding never widens the band: a sale's floor up, a "
      "purchase's ceiling down", getattr(V, "_grid", lambda *x: None)(1.9800004, 6, "sell") == "1.980001"
      and getattr(V, "_grid", lambda *x: None)(2.0199996, 6, "buy") == "2.019999")


class Coinbase(V.CoinbaseVenue):
    def __init__(self, bid, ask):
        super().__init__()
        self.bid, self.ask, self.sent = bid, ask, []

    def meta(self, symbol):
        return {"product_id": f"{symbol}-USD", "base_increment": "0.00000001",
                "quote_increment": "0.0001", "base_min_size": 0.00001}

    def best_bid_ask(self, product_id):
        return self.bid, self.ask

    def _call(self, method, path, body=None):
        self.sent.append((path, body))
        return {"success": True, "success_response": {"order_id": "oid-1"}}


c = Coinbase(1.9990, 2.0010)
r = c.place("XLM", "sell", 10.0, live=True, ref_px=2.0, client_ref=a)
check("B2 Coinbase, touch inside the band: the post-only limit is sent, with the caller's "
      "reference as client_order_id", len(c.sent) == 1 and c.sent[0][1]["client_order_id"] == a
      and "limit_limit_gtd" in c.sent[0][1]["order_configuration"], c.sent)
c = Coinbase(1.90, 1.91)                              # the market has dropped 4.5% since the plan was priced
got = None
try:
    c.place("XLM", "sell", 10.0, live=True, ref_px=2.0, client_ref=a)
except Exception as e:                                           # noqa: BLE001
    got = e
check("B3 Coinbase, touch OUTSIDE the band (ask $1.91 against a $2.00 reference): refused before "
      "anything is sent", isinstance(got, Refused) and c.sent == [] and "band" in str(got), (got, c.sent))
got = None
try:
    c.place("XLM", "sell", 10.0, live=True, ref_px=2.0, ordertype="market")
except Exception as e:                                           # noqa: BLE001
    got = e
check("B4 Coinbase asked for a MARKET order with a reference price: refused -- a market order "
      "has no band", isinstance(got, Refused) and c.sent == [], (got, c.sent))


class Robin(V.RobinhoodVenue):
    def __init__(self):
        super().__init__()
        self.sent = []

    def meta(self, symbol):
        return {"pair": f"{symbol}-USD", "symbol": symbol, "ordermin": 0.1, "increment": "0.000100", "status": "tradable"}

    def _call(self, method, path, body=None):
        self.sent.append((method, path, body))
        return {"id": "rh-1"}


rb = Robin()
rb.place("XLM", "sell", 10.0, live=True, ref_px=2.0, client_ref=a)
rb.place("XLM", "sell", 10.0, live=True, ref_px=2.0, client_ref=a)
b1, b2 = rb.sent[0][2], rb.sent[1][2]
check("B5 Robinhood, with a reference price: a LIMIT at the band's edge, not a market order",
      b1.get("type") == "limit" and abs(float(b1["limit_order_config"]["limit_price"]) - 1.98) < 1e-9, b1)
check("B5b ...and a retry carries the SAME client_order_id, a UUID (it was random per attempt)",
      b1["client_order_id"] == b2["client_order_id"] and len(b1["client_order_id"]) == 36, (b1, b2))

f = Fake()
res, st = run_execute(f, [order("XLM", 20.0, 2.0), order("XLM", 20.0, 2.0)])
check("B6 execute hands the venue the plan's own price as the reference, and a client reference",
      f.calls[0]["ref_px"] == 2.0 and len(f.calls[0]["client_ref"] or "") == 32, f.calls)
check("B6b ...a second order for the same symbol and side the same day gets a different one "
      "(its sequence is 1), and each is written on its row",
      f.calls[0]["client_ref"] != f.calls[1]["client_ref"]
      and [r_["client_ref"] for r_ in st["orders_today"]] == [c_["client_ref"] for c_ in f.calls], (f.calls, st))
f = Fake()
no_px = {k_: v_ for k_, v_ in order("XLM", 20.0, 2.0).items() if k_ != "px"}
run_execute(f, [no_px])
check("B6c an order that carries no price of its own is banded around the price its dollars "
      "and quantity imply ($20 for 10 units: $2.00)", f.calls and abs(f.calls[0]["ref_px"] - 2.0) < 1e-12, f.calls)


# ---- O: open orders ----------------------------------------------------------------------------
class Reads:
    """A venue for gather(): balances, what is open, what has filled."""

    def __init__(self, name, bal, open_=(), fills=(), open_err=None, fills_err=None):
        self.name, self._bal, self._open, self._fills = name, bal, list(open_), list(fills)
        self._oe, self._fe = open_err, fills_err

    def has_credentials(self):
        return True

    def balances(self):
        return dict(self._bal)

    def open_orders_norm(self):
        if self._oe:
            raise V.VenueError(self._oe)
        return [dict(o) for o in self._open]

    def recent_fills(self, since=None):
        if self._fe:
            raise V.VenueError(self._fe)
        return [dict(x) for x in self._fills]


def run_gather(venues):
    keep_all, keep_pre = T.V.all_venues, T.daily.prefetch
    try:
        T.V.all_venues = lambda: venues
        T.daily.prefetch = lambda syms, source: {s: (1.0, [1.0] * 200, None, [], []) for s in syms}
        return T.gather({"symbols": []})
    finally:
        T.V.all_venues, T.daily.prefetch = keep_all, keep_pre


pf = run_gather([Reads("a", {"XLM": 100.0, "USD": 50.0}, open_=[{"sym": "XLM", "side": "sell", "id": "o1"}])])
check("O1 gather() reads what is open at the venue: one XLM sell, named with its venue",
      [(o["sym"], o["side"], o["venue"]) for o in pf.get("open_orders", [])] == [("XLM", "sell", "a")]
      and pf.get("open_orders_unread") == [], pf.get("open_orders"))
pf_bad = run_gather([Reads("a", {"XLM": 100.0, "USD": 50.0}, open_err="HTTP 503: down")])
check("O2 a venue whose open orders cannot be read is named, and the book is incomplete for it",
      pf_bad.get("open_orders_unread") == ["a"]
      and any("open orders at a" in w for w in T.book_incomplete(pf_bad)), T.book_incomplete(pf_bad))

SCOPE = {"open_reserve_pct": 0.0, "by": "operator", "at": "2026-10-06", "words": "x"}
PLAN_CFG = {"max_position_pct": 0.20, "min_cash_pct": 0.10, "max_order_usd": 25.0, "min_order_usd": 5.0,
            "max_orders_per_day": 2, "allow_fiat_buys": False, "weekly_fiat_budget_usd": 0.0,
            "trading_scope": SCOPE}


def run_plan(open_orders=(), unread=()):
    positions = [{"sym": "JASMY", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "DOWN", "at": {"a": 100.0}},
                 {"sym": "ADA", "qty": 100.0, "px": 1.0, "val": 100.0, "regime": "DOWN", "at": {"a": 100.0}}]
    held = {p["sym"]: p["qty"] for p in positions}
    keep = T.reserve_baseline
    try:
        T.reserve_baseline = lambda pf_, path=None: (dict(held), held, [])
        return T.plan(dict(PLAN_CFG), {"positions": positions, "cash": 800.0, "total": 1000.0,
                                       "unpriced": [], "venue_notes": [],
                                       "open_orders": list(open_orders), "open_orders_unread": list(unread)})
    finally:
        T.reserve_baseline = keep


o, n = run_plan()
check("O3 with nothing open, both coins below their line are planned for sale",
      sorted(x["sym"] for x in o) == ["ADA", "JASMY"], (o, n))
o, n = run_plan(open_orders=[{"sym": "JASMY", "side": "sell", "venue": "a", "id": "o1"}])
check("O4 with a JASMY order already open, NOTHING is planned for JASMY -- and the note says so",
      [x["sym"] for x in o] == ["ADA"] and any(m.startswith("open order: JASMY") and "open at a" in m for m in n), (o, n))
o, n = run_plan(unread=["a"])
check("O5 with a venue's open orders unread, nothing is planned at all, and the venue is named",
      o == [] and len(n) == 1 and "open orders at a" in n[0], (o, n))

# ---- F: fills -----------------------------------------------------------------------------------
f = Fake()
res, st = run_execute(f, [order("XLM", 20.0, 2.0)])
row = st["orders_today"][0]
check("F1 a placed sale is written UNCONFIRMED, with the venue it went to and the id it was given",
      row.get("fill") == "UNCONFIRMED" and row.get("venue") == "fake" and row.get("txid") == "TX-1", row)
gs = lambda rows: G.State(equity_now=0.0, equity_peak=0.0, equity_start_of_day=0.0, closed_trades=[],
                          last_sold={}, positions={}, cash=0.0, orders_today=rows)
room = G.FiatBuyPermission(config_path=os.path.join(tempfile.mkdtemp(), "none.json")).headroom
check("F2 ...and raises NOTHING a purchase can be funded from until it is confirmed "
      "(it used to count in full the moment it was placed)", room(gs([dict(row)])) == 0.0, room(gs([dict(row)])))
check("F2b a row from before fills were tracked (no `fill` key) counts as it always did: $20",
      room(gs([{"sym": "XLM", "side": "sell", "usd": 20.0}])) == 20.0)
apply = getattr(T, "apply_fills", lambda st_, pf_: [])
st_none = {"orders_today": [dict(row)]}
apply(st_none, {"fills": {"fake": []}})
check("F3 with no fill reported by the venue the row stays UNCONFIRMED",
      st_none["orders_today"][0]["fill"] == "UNCONFIRMED" and room(gs(st_none["orders_today"])) == 0.0)
st_part = {"orders_today": [dict(row)]}
apply(st_part, {"fills": {"fake": [{"order_id": "TX-1", "sym": "XLM", "side": "sell", "usd": 8.0, "qty": 4.0}]}})
check("F4 with $8 of the $20 filled it is PARTIAL, and $8 is what it raised",
      st_part["orders_today"][0]["fill"] == "PARTIAL" and room(gs(st_part["orders_today"])) == 8.0,
      st_part["orders_today"])
st_full = {"orders_today": [dict(row)]}
notes = apply(st_full, {"fills": {"fake": [{"order_id": "TX-1", "usd": 12.0}, {"order_id": "TX-1", "usd": 7.9},
                                           {"order_id": "SOMEONE-ELSES", "usd": 500.0}]}})
check("F5 with $19.90 filled across two fills it is CONFIRMED, $19.90 raised -- and another "
      "order's fill is not counted toward it",
      st_full["orders_today"][0]["fill"] == "CONFIRMED" and abs(room(gs(st_full["orders_today"])) - 19.9) < 1e-9
      and len(notes) == 1, (st_full["orders_today"], notes))
st_other = {"orders_today": [dict(row)]}
apply(st_other, {"fills": {"another_venue": [{"order_id": "TX-1", "usd": 20.0}]}})
check("F6 a fill with the same id at ANOTHER venue does not confirm it",
      st_other["orders_today"][0]["fill"] == "UNCONFIRMED")
check("F7 a `fill` value nobody defined makes the day's proceeds unknown, and the guard blocks",
      room(gs([dict(row, fill="PROBABLY")])) is None)
pf_f = run_gather([Reads("a", {"XLM": 100.0, "USD": 50.0}, fills=[{"order_id": "X", "usd": 5.0}]),
                   Reads("b", {"ADA": 10.0}, fills_err="not implemented")])
check("F8 gather() carries each venue's fills; a venue that cannot report them is named and "
      "confirms nothing (the book is NOT incomplete for that alone)",
      pf_f.get("fills") == {"a": [{"order_id": "X", "usd": 5.0}]} and pf_f.get("fills_unread") == ["b"]
      and T.book_incomplete(pf_f) == [], (pf_f.get("fills"), pf_f.get("fills_unread")))

# ---- the adapters' own readers, on the documented shapes -----------------------------------------
k = Kraken()
oo = k.open_orders_norm(lambda: {"open": {"OQCLML-BW3P3-BUCMWZ": {
    "descr": {"pair": "XLMUSD", "type": "sell", "ordertype": "limit"}, "vol": "10", "cl_ord_id": a}}})
check("N1 Kraken OpenOrders -> the symbol (from the pair, whichever of its names is used), side, id, reference",
      oo == [{"sym": "XLM", "side": "sell", "id": "OQCLML-BW3P3-BUCMWZ", "client_ref": a}], oo)
ff = k.recent_fills(None, lambda: {"trades": {"T1": {"ordertxid": "OQCLML-BW3P3-BUCMWZ", "pair": "XXLMZUSD",
                                                     "time": 1794477600.5, "type": "sell", "price": "1.99",
                                                     "cost": "19.90", "fee": "0.05", "vol": "10.0"}}})
check("N2 Kraken TradesHistory -> the order it belongs to and the dollars it raised",
      len(ff) == 1 and ff[0]["order_id"] == "OQCLML-BW3P3-BUCMWZ" and ff[0]["sym"] == "XLM"
      and abs(ff[0]["usd"] - 19.90) < 1e-9, ff)
bad = None
try:
    k.open_orders_norm(lambda: {"open": {"O1": {"descr": {"pair": "NOSUCHPAIR", "type": "sell"}}}})
except V.VenueError as e:
    bad = str(e)
check("N3 an open order on a pair the adapter does not know is an ERROR, not a symbol it made up",
      bad is not None and "NOSUCHPAIR" in bad, bad)
cbv = V.CoinbaseVenue()
oo = cbv.open_orders_norm(lambda: [{"order_id": "o-1", "product_id": "XLM-USD", "side": "SELL",
                                    "status": "OPEN", "client_order_id": a}])
ff = cbv.recent_fills(None, lambda: [
    {"order_id": "o-1", "product_id": "XLM-USD", "side": "SELL", "price": "1.99", "size": "10",
     "size_in_quote": False, "trade_time": "2026-11-12T10:00:00.123456789Z", "trade_type": "FILL"},
    {"order_id": "o-1", "product_id": "XLM-USD", "side": "SELL", "price": "1.99", "size": "10",
     "size_in_quote": False, "trade_time": "2026-11-12T10:00:01Z", "trade_type": "REVERSAL"}])
check("N4 Coinbase List Orders / List Fills -> the same shape; a REVERSAL is not a fill",
      oo == [{"sym": "XLM", "side": "sell", "id": "o-1", "client_ref": a}]
      and len(ff) == 1 and abs(ff[0]["usd"] - 19.90) < 1e-9 and ff[0]["at"] is not None, (oo, ff))
rbv = V.RobinhoodVenue()
got = None
try:
    rbv.recent_fills(None)
except V.VenueError as e:
    got = str(e)
check("N5 Robinhood's fills are NOT read, and the adapter says so rather than guessing",
      got is not None and "UNDETERMINED" in got
      and rbv.open_orders_norm(lambda: [{"id": "r1", "symbol": "XLM-USD", "side": "sell"}])[0]["sym"] == "XLM", got)

n_ = len(results)
print(f"\nH10 order mechanics: {sum(results)}/{n_} passed")
sys.exit(0 if all(results) else 1)
