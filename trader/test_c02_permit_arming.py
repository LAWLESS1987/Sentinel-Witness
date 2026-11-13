"""C2 (2026-11-13): a permit never outlives the arming it was issued under. A permit used to live
PERMIT_VALID_S (120 s) from its issue whatever the arming said, so one issued in the last seconds of
an arming could send a live order up to two minutes after the arming had ended. Driven both ways
through the real gate with the shared fixture. Offline: stand-in venue, temporary config and state,
no network. The clock is moved by replacing guards.time.time, never by waiting."""
import inspect
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guards as G  # noqa: E402
from gated_fixture import Gated, Venue, order, until  # noqa: E402

results = []
SELL = order()


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


def refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None


class Clock:
    """The wall clock reads `at`; with `mono`, the clock that cannot be set back reads that many
    seconds later than it really does. Both are put back on the way out."""

    def __init__(self, at, mono=None):
        self.at, self.mono = at, mono
        self._real, self._real_mono = G.time.time, G.time.monotonic

    def __enter__(self):
        G.time.time = lambda: self.at
        if self.mono is not None:
            real, ahead = self._real_mono, self.mono
            G.time.monotonic = lambda: real() + ahead
        return self

    def __exit__(self, *exc):
        G.time.time, G.time.monotonic = self._real, self._real_mono


def rewrite_config(fx, **change):
    """Change the trader's config ON DISK, as an operator's edit would. None removes a key."""
    import json
    with open(fx.config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for k, v in change.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    with open(fx.config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


# ---- a long arming: nothing changes ---------------------------------------------------------
with Gated(approved=[SELL]) as fx:
    fx.seal([dict(SELL)])
    t0 = time.time()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    p = G._PERMITS.get(permit) or {}
    check("P1 an arming with an hour left: the gate permits", reasons == [] and bool(permit), reasons)
    check("P2 ...and the permit lasts its own %d seconds, as before" % G.PERMIT_VALID_S,
          abs(p.get("expires", 0) - (t0 + G.PERMIT_VALID_S)) < 5, p)
    check("P3 ...and it carries the end of the arming, about an hour ahead",
          p.get("arming_ends") is not None and 3500 < p["arming_ends"] - t0 < 3700, p.get("arming_ends"))
    check("P4 ...and it is used once", G.consume_permit(permit, "fake", "XLM", "sell") == 20.0)

# ---- a short arming: the permit ends with it -------------------------------------------------
with Gated(approved=[SELL], cfg={"armed_until": until(30)}) as fx:
    fx.seal([dict(SELL)])
    t0 = time.time()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    p = G._PERMITS.get(permit) or {}
    check("P5 an arming with 30 seconds left: the gate still permits", reasons == [] and bool(permit), reasons)
    check("P6 ...and the permit ends with the arming, not %d seconds from now" % G.PERMIT_VALID_S,
          p.get("expires", 0) <= p.get("arming_ends", 0) and p.get("expires", 0) - t0 < 35, p)
    with Clock(t0 + 20):
        check("P7 ten seconds before the arming ends the permit is good",
              G.consume_permit(permit, "fake", "XLM", "sell") == 20.0)

with Gated(approved=[SELL], cfg={"armed_until": until(30)}) as fx:
    fx.seal([dict(SELL)])
    t0 = time.time()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    ends = (G._PERMITS.get(permit) or {}).get("arming_ends") or 0
    with Clock(ends):
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check("P8 at the second the arming ends the permit sends nothing, and says why",
              said == "the arming this permit was issued under has ended", said)
    with Clock(ends + 60):
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check("P9 a minute after the arming ended (inside the old 120 seconds) it still sends nothing",
              said == "the arming this permit was issued under has ended", said)
    check("P10 ...and it was never marked used", (G._PERMITS.get(permit) or {}).get("used") is False)

# ---- the permit itself, asked directly -----------------------------------------------------
with Gated(approved=[SELL]) as fx:
    now = time.time()
    pid = G._issue_permit("fake", "XLM", "sell", 20.0, armed_until=now + 5)
    check("P11 a permit issued 5 seconds before an arming ends lasts 5 seconds", abs(G._PERMITS[pid]["expires"] - (now + 5)) < 2)
    pid = G._issue_permit("fake", "XLM", "sell", 20.0, armed_until=now + 100000)
    check("P12 an arming far ahead never lengthens a permit", abs(G._PERMITS[pid]["expires"] - (now + G.PERMIT_VALID_S)) < 2)
    pid = G._issue_permit("fake", "XLM", "sell", 20.0, armed_until=now - 1)
    said = refused(lambda: G.consume_permit(pid, "fake", "XLM", "sell"))
    check("P13 a permit issued under an arming already over can never be used",
          said == "the arming this permit was issued under has ended", said)
    pid = G._issue_permit("fake", "XLM", "sell", 20.0)
    check("P14 issued with no arming end at all (the older tests' direct calls): its own %d seconds" % G.PERMIT_VALID_S,
          G._PERMITS[pid]["arming_ends"] is None and G.consume_permit(pid, "fake", "XLM", "sell") == 20.0)
    for bad in (float("nan"), float("inf"), "soon"):
        pid = G._issue_permit("fake", "XLM", "sell", 20.0, armed_until=bad)
        check("P15 an arming end that is not a finite number (%r) does not lengthen or shorten a permit by accident" % (bad,),
              G._PERMITS[pid]["arming_ends"] is None and abs(G._PERMITS[pid]["expires"] - (time.time() + G.PERMIT_VALID_S)) < 2)

# ---- the clock that cannot be set back ----------------------------------------------------------
with Gated(approved=[SELL], cfg={"armed_until": until(30)}) as fx:
    fx.seal([dict(SELL)])
    t0 = time.time()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    with Clock(t0 - 3600, mono=31):
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check("P19 the wall clock set back an hour after the issue does not give an ended arming its time back",
              reasons == [] and said == "the arming this permit was issued under has ended", (reasons, said))
    with Clock(t0 - 3600, mono=10):
        check("P20 ...while inside its real 30 seconds, clock set back or not, the permit is good (the control)",
              G.consume_permit(permit, "fake", "XLM", "sell") == 20.0)

with Gated(approved=[SELL]) as fx:
    fx.seal([dict(SELL)])
    t0 = time.time()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    with Clock(t0 - 3600, mono=G.PERMIT_VALID_S + 1):
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check("P21 a long arming and a wall clock set back: the permit still dies after its %d seconds" % G.PERMIT_VALID_S,
              said == "the permit has expired", said)

# ---- disarming by hand stops an order that already holds a permit ------------------------------
for label, change, word in (
        ("P22 armed set to false on disk after the permit was issued", {"armed": False}, "no longer armed"),
        ("P23 armed_until moved into the past on disk", {"armed_until": until(-60)}, "no longer armed"),
        ("P24 armed_until removed on disk", {"armed_until": None}, "no longer armed")):
    with Gated(approved=[SELL]) as fx:
        fx.seal([dict(SELL)])
        reasons, permit = G.live_gate(dict(SELL), Venue())
        rewrite_config(fx, **change)
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check(label + ": the permit sends nothing, and says so", reasons == [] and said is not None and word in said, (reasons, said))
        check(label + ": ...and was not marked used", (G._PERMITS.get(permit) or {}).get("used") is False)

with Gated(approved=[SELL]) as fx:
    fx.seal([dict(SELL)])
    reasons, permit = G.live_gate(dict(SELL), Venue())
    with open(fx.config_path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not a config")
    said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
    check("P25 a config that can no longer be read sends nothing", said is not None and ("no longer armed" in said), said)

with Gated(approved=[SELL], cfg={"armed_until": until(30)}) as fx:
    fx.seal([dict(SELL)])
    reasons, permit = G.live_gate(dict(SELL), Venue())
    ends = (G._PERMITS.get(permit) or {}).get("arming_ends") or 0
    rewrite_config(fx, armed_until=until(7200))
    with Clock(ends + 1):
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
        check("P26 an arming extended on disk after the issue does not extend a permit already issued",
              said == "the arming this permit was issued under has ended", said)

with Gated(approved=[SELL]) as fx:
    fx.seal([dict(SELL)])
    reasons, permit = G.live_gate(dict(SELL), Venue())
    rewrite_config(fx, max_order_usd=25)
    check("P27 the control: a config rewritten and still armed, the permit is used",
          reasons == [] and G.consume_permit(permit, "fake", "XLM", "sell") == 20.0, reasons)

# ---- the gate hands the arming's end to the permit: pinned so a rewrite cannot drop it quietly ---
src = inspect.getsource(G.live_gate)
check("P16 live_gate issues the permit with the arming's end", "armed_until=ends" in src and 'parse_utc(disk.get("armed_until"))' in src)
check("P17 live_gate refuses when that end cannot be read (a pin on the text: arming() refuses first, so the branch "
      "cannot be reached through the gate)",
      'if ends is None:\n        return ["live gate: the end of the arming (armed_until) could not be read' in src.replace("\r\n", "\n"))

with Gated(approved=[SELL]) as fx:
    fx.seal([dict(SELL)])
    reasons, permit = G.live_gate(dict(SELL), Venue())
    real_load = G.load_config

    def cannot_read(*a, **k):
        raise OSError("the disk went away")
    G.load_config = cannot_read
    try:
        said = refused(lambda: G.consume_permit(permit, "fake", "XLM", "sell"))
    finally:
        G.load_config = real_load
    check("P28 a config whose read raises when the permit is used sends nothing, and says the config could not be read",
          said is not None and "no longer armed" in said and "could not be read (OSError)" in said, said)
    check("P29 ...and once it can be read again the same permit is good",
          G.consume_permit(permit, "fake", "XLM", "sell") == 20.0)
check("P18 consume_permit asks about the arming before the permit's own time",
      inspect.getsource(G.consume_permit).index("arming_ends") < inspect.getsource(G.consume_permit).index('"the permit has expired"'))

n = len(results)
print(f"\nC2 permit and arming: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
