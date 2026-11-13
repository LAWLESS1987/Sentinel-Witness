#!/usr/bin/env python3
"""R2 -- the caps are checked on the order that is actually sent.

2026-11-12, review round one. Confirmed by the outside reviewer.

THE DEFECT. Every cap, the day's remaining notional and the approval were
checked against an order's DECLARED dollars -- the planner's `usd`. What a
venue received was a QUANTITY, which the adapter then rounded to the venue's
increment and priced at a limit one band away from the reference. Nothing
compared what was sent with what was declared:

  * a $25.00 purchase whose limit is 1% above the reference can spend $25.25
    -- over the per-order ceiling, over the approved order;
  * a quantity that was really a dollar figure (25 "units" of a $2,000 coin
    with $25 declared) was a $50,000 order that passed every cap.

WHAT IS HERE NOW. covenant_trader.execute cuts the size to
declared / (reference x 1.01), so the most the order can move inside its band
is what it declared. Each adapter then computes, on the size and price it is
ABOUT TO SEND (after rounding), venues.final_order: `worst` -- the most
dollars it can move inside the band -- may not exceed the declared dollars,
and the order's worth at the reference may be at most 2% under them. The
declared dollars were already held to the ceiling, the day's remaining
notional and the approved order by guards.live_gate, so what is sent is
inside all three. Outside either bound the order is refused, not adjusted.

  F  final_order: the sized order passes; the unsized one, an over-rounded
     one and a dollars-as-units one are refused, each in words.
  K  Kraken: what is sent (volume x price) is inside the declared dollars;
     the volume is rounded DOWN; a lot so coarse that rounding loses more than
     2% refuses; a size or cost under the venue's minimum REFUSES and is not
     raised to it; the volume is base units (no quote-volume flag).
  C  Coinbase: the same; base_size is sent and quote_size never.
  B  Robinhood: the same; asset_quantity is sent and quote_amount never.
  X  execute: the venue is handed the sized quantity and the declared
     dollars; an order whose quantity and dollars disagree at its own price is
     refused before any venue is called.
  G  through the whole gate, at the ceiling: a $25 approved order is sent as
     an order that cannot move more than $25; with $15 left in the day, as
     one that cannot move more than $15.
  Q  base versus quote, the other direction: a fill reported in quote
     currency is read as dollars, not as coins.

ON ONE CLAIM IN THE REVIEW: "refuse sizes below the venue minimum instead of
rounding up to it". Nothing in this fork rounded a size UP: Kraken, Coinbase
and Robinhood each already refused a size under the minimum (and K3, C3, B3
pin that they still do). What was missing is Kraken's minimum order COST
(costmin), which was read from AssetPairs and never checked; it is now.

WHAT THIS CANNOT SEE. The reference price is the planner's; if it is wrong,
the band is around the wrong number. A sale that fills ABOVE the band (the
market has jumped) moves more dollars than `worst`; that is a better price for
the seller and is not refused, because nothing sent can prevent it. Transports
are stubbed; nothing here reaches a venue.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import venues as V           # noqa: E402
import covenant_trader as T  # noqa: E402
from gated_fixture import Gated, Venue, order   # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


final_order = getattr(V, "final_order", None)
BAND = V.PRICE_BAND


def judged(side, size, limit, ref, declared):
    """'ok' or the refusal's words, from venues.final_order."""
    if final_order is None:
        return "ok"                                # the code before the fix checked nothing
    try:
        final_order(side, size, limit, ref, declared)
        return "ok"
    except V.VenueRefused as e:
        return str(e)


def sized(usd, ref):
    return usd / (ref * (1.0 + BAND))


# ---- F: final_order -----------------------------------------------------------------
check("F1 a $20 sale sized as execute sizes it (19.80 units at $1.00, limit $0.99) passes: it "
      "can move at most $20.00", judged("sell", sized(20, 1.0), 0.99, 1.0, 20.0) == "ok",
      judged("sell", sized(20, 1.0), 0.99, 1.0, 20.0))
check("F1b ...and so does the purchase (limit $1.01)", judged("buy", sized(20, 1.0), 1.01, 1.0, 20.0) == "ok")
r = judged("buy", 20.0, 1.01, 1.0, 20.0)
check("F2 the UNSIZED purchase -- 20 units with a $1.01 ceiling -- could spend $20.20 against "
      "$20 declared: refused, with both figures", "$20.20" in r and "$20.00" in r, r)
r = judged("sell", 20.0, 0.99, 1.0, 20.0)
check("F2b ...and the unsized sale the same (the most it moves inside the band is $20.20)", "$20.20" in r, r)
r = judged("buy", 25.0 / 1.01 + 0.01, 1.01, 1.0, 25.0)
check("F3 one cent over the declared dollars is over", "more than the $25.00 declared" in r, r)
r = judged("sell", 19.0, 0.99, 1.0, 20.0)
check("F4 an order rounded 5% under what was declared is NOT the order that was approved: refused",
      "under the $20.00 declared" in r and "not the order that was approved" in r, r)
check("F4b ...while 1.5% under is inside the 2% tolerance", judged("sell", 19.7, 0.99, 1.0, 20.0) == "ok")
r = judged("sell", 25.0, 1980.0, 2000.0, 25.0)
check("F5 BASE/QUOTE: 25 'units' of a $2,000 coin with $25 declared is a $50,000 order -- refused, "
      "and the refusal says the size is in units of the coin, not dollars",
      "more than the $25.00 declared" in r and "not in dollars" in r, r)
bad = [judged("sell", s, lim, ref, dec) for s, lim, ref, dec in (
    (float("nan"), 0.99, 1.0, 20.0), (19.8, float("inf"), 1.0, 20.0), (19.8, 0.99, 0.0, 20.0),
    (19.8, 0.99, 1.0, -20.0), ("19.8x", 0.99, 1.0, 20.0))]
check("F6 a size, limit, reference or declared figure that is NaN, infinite, zero, negative or "
      "not a number is refused", all(b != "ok" for b in bad), bad)


def permit(venue, sym, side, usd):
    G.take_cycle_lock()      # C1: a permit is used only by a process holding the cycle lock
    return G._issue_permit(venue, sym, side, usd)


def attempt(fn):
    try:
        return ("sent", fn())
    except V.VenueRefused as e:
        return ("refused", str(e))


# ---- K: Kraken -------------------------------------------------------------------------
class Kraken(V.KrakenVenue):
    def __init__(self, lot=8, ordermin=1.0, costmin=0.5, px_decimals=6):
        super().__init__()
        self.sent = []
        self._pairs = {"XLM": {"pair": "XXLMZUSD", "symbol": "XLM", "altname": "XLMUSD", "wsname": "XLM/USD",
                               "lot_decimals": lot, "pair_decimals": px_decimals,
                               "ordermin": ordermin, "costmin": costmin}}

    def _private(self, endpoint, data=None):
        self.sent.append((endpoint, dict(data or {})))
        return {"txid": ["OABC-1"], "descr": {"order": "x"}}


def kraken_live(k, side, qty, ref, declared):
    return attempt(lambda: k.place("XLM", side, qty, live=True, ref_px=ref,
                                   permit=permit("kraken", "XLM", side, declared)))


for side in ("sell", "buy"):
    k = Kraken()
    how, out = kraken_live(k, side, sized(25.0, 2.0), 2.0, 25.0)
    d = k.sent[-1][1] if k.sent else {}
    vol, px = float(d.get("volume", 0)), float(d.get("price", 0))
    worst = vol * (px if side == "buy" else max(px, 2.0 * 1.01))
    check("K1 Kraken %s, $25 declared at a $2.00 reference: SENT, and volume x the most it can "
          "trade at inside the band is $%.4f -- not over $25" % (side, worst),
          how == "sent" and 24.5 <= worst <= 25.0 + 1e-9 and vol <= sized(25.0, 2.0) + 1e-12, (how, out, d))
    check("K1b ...the volume is in the BASE coin: no quote-volume flag (viqc) is set, and the "
          "adapter reports what it sent", "oflags" not in d and (out or {}).get("final", {}).get("worst", 99) <= 25.0 + 1e-9,
          (d, out))
k = Kraken()
how, out = kraken_live(k, "buy", 12.5, 2.0, 25.0)                # the plan's own quantity, not sized
check("K2 the same purchase at the plan's own quantity (12.5 units, ceiling $2.02 = $25.25): "
      "REFUSED, nothing sent", how == "refused" and "$25.25" in out and k.sent == [], (how, out, k.sent))
k = Kraken(lot=0)                                                # whole coins only
how, out = kraken_live(k, "sell", sized(25.0, 2.0), 2.0, 25.0)   # 12.37 -> 12 units = $24.00
check("K2b a lot so coarse that rounding DOWN takes the order 3% under what was declared: "
      "refused -- it is not rounded up instead", how == "refused" and "under the $25.00 declared" in out
      and k.sent == [], (how, out, k.sent))
k = Kraken(lot=2)
how, out = kraken_live(k, "sell", sized(25.0, 2.0), 2.0, 25.0)   # 12.376 -> 12.37
check("K2c a two-decimal lot rounds 12.376 DOWN to 12.37 and that is sent",
      how == "sent" and k.sent[-1][1]["volume"] == "12.37", (how, out, k.sent))
k = Kraken(ordermin=20.0)
how, out = kraken_live(k, "sell", sized(25.0, 2.0), 2.0, 25.0)
check("K3 a size UNDER Kraken's minimum (12.37 against 20) is REFUSED and nothing is sent -- "
      "it is not raised to the minimum", how == "refused" and "minimum" in out and k.sent == [], (how, out, k.sent))
k = Kraken(costmin=30.0)
how, out = kraken_live(k, "sell", sized(25.0, 2.0), 2.0, 25.0)
check("K3b a COST under Kraken's minimum order cost ($24.50 against $30) is refused too -- "
      "costmin was read and never checked", how == "refused" and "minimum order cost" in out and k.sent == [],
      (how, out, k.sent))
k = Kraken()
how, out = kraken_live(k, "sell", 25.0, 2000.0, 25.0)            # dollars in the size field
check("K4 BASE/QUOTE at the adapter: the declared $25 passed as the SIZE of a $2,000 coin is "
      "refused before anything is sent", how == "refused" and "not in dollars" in out and k.sent == [], (how, out))
k = Kraken()
how, out = attempt(lambda: k.place("XLM", "sell", 12.0, live=True, permit=permit("kraken", "XLM", "sell", 25.0)))
check("K5 a live order with NO reference price is refused: there is nothing to check what is "
      "sent against", how == "refused" and "reference price" in out and k.sent == [], (how, out))
k = Kraken()
how, out = attempt(lambda: k.place("XLM", "buy", 12.5, live=False, ref_px=2.0, declared_usd=25.0))
check("K6 the DRY RUN is held to the same check, so a rehearsal shows the refusal a live order "
      "would get", how == "refused" and "$25.25" in out and k.sent == [], (how, out))


# ---- C: Coinbase ---------------------------------------------------------------------------
class Coinbase(V.CoinbaseVenue):
    def __init__(self, bid=1.9990, ask=2.0010, base_inc="0.00000001", base_min=0.00001):
        super().__init__()
        self.bid, self.ask, self.sent = bid, ask, []
        self._m = {"base_increment": base_inc, "quote_increment": "0.0001", "base_min_size": base_min}

    def meta(self, symbol):
        return dict(self._m, product_id=f"{symbol}-USD")

    def best_bid_ask(self, product_id):
        return self.bid, self.ask

    def _call(self, method, path, body=None):
        self.sent.append((path, body))
        return {"success": True, "success_response": {"order_id": "oid-1"}}


def coinbase_live(c, side, qty, ref, declared):
    return attempt(lambda: c.place("XLM", side, qty, live=True, ref_px=ref,
                                   permit=permit("coinbase", "XLM", side, declared)))


for side in ("sell", "buy"):
    c = Coinbase()
    how, out = coinbase_live(c, side, sized(25.0, 2.0), 2.0, 25.0)
    conf = (c.sent[-1][1]["order_configuration"]["limit_limit_gtd"] if c.sent else {})
    size, px = float(conf.get("base_size", 0)), float(conf.get("limit_price", 0))
    worst = size * (px if side == "buy" else max(px, 2.0 * 1.01))
    check("C1 Coinbase %s, $25 declared: SENT at the touch, and base_size x the most it can trade "
          "at is $%.4f -- not over $25" % (side, worst), how == "sent" and 24.5 <= worst <= 25.0 + 1e-9, (how, out, conf))
    check("C1b ...the size is base_size, in the coin; quote_size is never sent",
          "base_size" in conf and "quote_size" not in conf, conf)
c = Coinbase()
how, out = coinbase_live(c, "buy", 12.5, 2.0, 25.0)              # 12.5 x bid 1.999 = $24.99: inside
check("C2 a purchase at the plan's own quantity, resting at a bid BELOW the reference: 12.5 x "
      "$1.999 = $24.99 cannot exceed $25, and is sent", how == "sent", (how, out))
c = Coinbase(bid=2.0150, ask=2.0160)                             # the bid is 0.75% above the reference
how, out = coinbase_live(c, "buy", 12.5, 2.0, 25.0)              # 12.5 x 2.015 = $25.19
check("C2b the same purchase when the bid has risen above the reference (inside the band): 12.5 x "
      "$2.015 = $25.19 would exceed the $25 declared -- REFUSED, nothing sent",
      how == "refused" and "$25.19" in out and c.sent == [], (how, out, c.sent))
c = Coinbase(base_inc="1")
how, out = coinbase_live(c, "sell", sized(25.0, 2.0), 2.0, 25.0)
check("C2c a whole-coin increment rounds 12.37 DOWN to 12 ($24.00, 4% under): refused, not "
      "rounded up", how == "refused" and "under the $25.00 declared" in out and c.sent == [], (how, out))
c = Coinbase(base_min=20.0)
how, out = coinbase_live(c, "sell", sized(25.0, 2.0), 2.0, 25.0)
check("C3 a size UNDER Coinbase's minimum is REFUSED and nothing is sent -- it is not raised to "
      "the minimum", how == "refused" and "minimum" in out and c.sent == [], (how, out))
c = Coinbase()
how, out = coinbase_live(c, "sell", 25.0, 2000.0, 25.0)
check("C4 BASE/QUOTE: $25 passed as the size of a $2,000 coin is refused before anything is sent "
      "(here by the band: the $2.00 touch is nowhere near $2,000)", how == "refused" and c.sent == [], (how, out))


# ---- B: Robinhood ----------------------------------------------------------------------------
class Robin(V.RobinhoodVenue):
    def __init__(self, ordermin=0.1):
        super().__init__()
        self.sent, self._min = [], ordermin

    def meta(self, symbol):
        return {"pair": f"{symbol}-USD", "symbol": symbol, "ordermin": self._min, "increment": "0.000100",
                "status": "tradable"}

    def _call(self, method, path, body=None):
        self.sent.append((method, path, body))
        return {"id": "rh-1"}


def robin_live(rb, side, qty, ref, declared):
    return attempt(lambda: rb.place("XLM", side, qty, live=True, ref_px=ref,
                                    permit=permit("robinhood", "XLM", side, declared)))


rb = Robin()
how, out = robin_live(rb, "buy", sized(25.0, 2.0), 2.0, 25.0)
conf = rb.sent[-1][2]["limit_order_config"] if rb.sent else {}
check("B1 Robinhood buy, $25 declared: SENT as a limit; asset_quantity x limit_price is not over "
      "$25; quote_amount is never sent", how == "sent" and "quote_amount" not in conf
      and float(conf["asset_quantity"]) * float(conf["limit_price"]) <= 25.0 + 1e-9, (how, out, conf))
rb = Robin()
how, out = robin_live(rb, "buy", 12.5, 2.0, 25.0)
check("B2 ...and at the plan's own quantity ($25.25 at the ceiling): refused, nothing sent",
      how == "refused" and "$25.25" in out and rb.sent == [], (how, out))
rb = Robin(ordermin=20.0)
how, out = robin_live(rb, "sell", sized(25.0, 2.0), 2.0, 25.0)
check("B3 a size under Robinhood's minimum is refused, not raised to it",
      how == "refused" and "minimum" in out and rb.sent == [], (how, out))

# ---- X: execute ----------------------------------------------------------------------------------
O = order("XLM", "sell", 20.0, px=2.0)                           # 10 units at $2.00
with Gated(approved=[O]) as fx:
    v = Venue()
    res = fx.execute([dict(O)], [v])
    c0 = v.calls[0]
    row = fx.state_on_disk()["orders_today"][0]
check("X1 execute hands the venue the SIZED quantity -- $20 / ($2.00 x 1.01) = 9.90 units, not the "
      "plan's 10 -- with the declared $20 beside it", v.live() == [True]
      and abs(c0["qty"] - 20.0 / 2.02) < 1e-9 and c0["declared_usd"] == 20.0 and c0["ref_px"] == 2.0, c0)
check("X1b ...and the day's record keeps the declared $20 (which the order cannot exceed) and "
      "the size that was sent", row["usd"] == 20.0 and abs(row["qty"] - 20.0 / 2.02) < 1e-9, row)
confused = dict(order("XLM", "sell", 25.0, px=2000.0), qty=25.0)   # dollars written in the quantity field
with Gated(approved=[confused]) as fx:
    v = Venue()
    res = fx.execute([dict(confused)], [v])
check("X2 BASE/QUOTE in the order itself: 25 units at its own price of $2,000 is $50,000, and it "
      "declares $25 -- REFUSED before any venue is called, not even a dry run",
      v.calls == [] and res[0]["status"] == "REFUSED" and "disagree" in res[0]["detail"], (v.calls, res))
slightly = dict(order("XLM", "sell", 20.0, px=2.0), qty=10.5)       # 5% more coin than $20 buys
with Gated(approved=[slightly]) as fx:
    v = Venue()
    res = fx.execute([dict(slightly)], [v])
check("X2b ...and a quantity 5% over what its dollars buy at its own price is refused the same way",
      v.calls == [] and res[0]["status"] == "REFUSED", (v.calls, res))
with Gated(approved=[O]) as fx:
    v = Venue()
    res = fx.execute([dict(O, qty=9.95)], [v], seal=False)          # 0.5% under: inside the tolerance
    fine = res[0]["status"] != "REFUSED" or "disagree" not in str(res[0].get("detail"))
check("X2c ...while half a percent of disagreement is not that mistake", fine, res)


# ---- G: through the whole gate, with a real adapter over a stubbed wire ------------------------
class GatedKraken(Kraken):
    def has_credentials(self):
        return True

    def _private(self, endpoint, data=None):
        self.sent.append((endpoint, dict(data or {})))
        if endpoint == "GetApiKeyInfo":
            return {"permissions": ["query-funds", "query-open-trades", "query-closed-trades",
                                    "modify-trades", "close-trades"]}
        return {"txid": ["OABC-1"], "descr": {"order": "x"}}


def through_gate(usd, **fixture):
    o = order("XLM", "buy", usd, px=2.0, venue="kraken")
    healthy = G.State(equity_now=1000.0, equity_peak=1000.0, equity_start_of_day=1000.0, closed_trades=[],
                      last_sold={}, positions={"XLM": 100.0}, cash=900.0)
    cfg = dict({"allow_fiat_buys": True}, **fixture.pop("cfg", {}))
    with Gated(approved=[o], cfg=cfg, **fixture) as fx:
        k = GatedKraken()
        res = fx.execute([dict(o)], [k], portfolio=healthy)
        orders = [d for e, d in k.sent if e == "AddOrder" and "validate" not in d]
        return res, orders


res, sent = through_gate(25.0)
spend = float(sent[0]["volume"]) * float(sent[0]["price"]) if sent else None
check("G1 at the per-order CEILING: an approved $25 purchase goes out as an order whose volume x "
      "limit is $%s -- it cannot spend more than the $25 ceiling or the $25 approved"
      % ("%.4f" % spend if spend else "?"), res[0]["status"] == "PLACED" and spend is not None
      and 24.5 <= spend <= 25.0 + 1e-9, (res[0]["status"], res[0].get("blocked_by"), sent))
res, sent = through_gate(15.0, cfg={"max_daily_notional_usd": 40.0},
                         state={"orders_today": [{"sym": "ADA", "usd": 25.0, "side": "sell", "status": "PLACED"}]})
spend = float(sent[0]["volume"]) * float(sent[0]["price"]) if sent else None
check("G2 with $15 left in the day ($25 of a $40 day used): the $15 purchase goes out as an order "
      "that cannot spend more than $15", res[0]["status"] == "PLACED" and spend is not None
      and 14.7 <= spend <= 15.0 + 1e-9, (res[0]["status"], res[0].get("blocked_by"), sent))
res, sent = through_gate(20.0, cfg={"max_daily_notional_usd": 40.0},
                         state={"orders_today": [{"sym": "ADA", "usd": 25.0, "side": "sell", "status": "PLACED"}]})
check("G2b ...and a $20 purchase against the same $15 is refused by the day's cap, nothing sent",
      res[0]["status"] != "PLACED" and sent == []
      and any("placeable now" in b for b in res[0]["blocked_by"]), (res[0]["status"], res[0].get("blocked_by")))

# ---- Q: a fill reported in quote currency ---------------------------------------------------------
cbv = V.CoinbaseVenue()
fills = cbv.recent_fills(None, lambda: [
    {"order_id": "o-1", "product_id": "XLM-USD", "side": "SELL", "price": "2.00", "size": "20",
     "size_in_quote": True, "trade_time": "2026-11-12T10:00:00Z", "trade_type": "FILL"},
    {"order_id": "o-2", "product_id": "XLM-USD", "side": "SELL", "price": "2.00", "size": "20",
     "size_in_quote": False, "trade_time": "2026-11-12T10:00:00Z", "trade_type": "FILL"}])
check("Q1 a Coinbase fill with size_in_quote=true and size 20 is $20 (10 coins); the same numbers "
      "with size_in_quote=false are 20 coins ($40)",
      [(f["usd"], f["qty"]) for f in fills] == [(20.0, 10.0), (40.0, 20.0)], fills)

n = len(results)
print(f"\nR2 final order: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
