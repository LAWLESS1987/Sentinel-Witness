"""Review round 1 (2026-11-12): fixes for findings an outside review raised and the code confirmed, each driven both ways.
G1  a seal from an unpinned witness folder is never remembered
G5  a confirmed fill amount is never taken back by a later, shorter page of fills
G7  a symbol with an order whose answer was lost is planned nothing
G11 a fill with no order id matches no row
G13 the arming file disarms when its first armed cycle refuses (read as text: the file is never run here)
Offline: no venue, no network, no real config."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import covenant_trader as T  # noqa: E402
import witness as W  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("  PASS " if ok else "  FAIL ") + name + (("  -- %r" % (detail,)) if detail and not ok else ""))


# ---- G1
def seal_run(home, pin):
    """seal_decision_result with the real-runner path stubbed at _run, a given home and a given pin."""
    saved = (W._run, W.effective_home, W.home_reasons, W.pinned_home, list(W._SEALED))
    del W._SEALED[:]
    try:
        W._run = lambda h, script, payload, timeout: (True, {"ok": True, "admission": "admitted (stub)"}, "")
        W.effective_home = lambda cfg=None: home
        W.home_reasons = lambda cfg, hypothetical=False: []
        W.pinned_home = lambda: (pin, None if pin else "no pin")
        out = W.seal_decision_result({"armed": False}, {"orders": [{"sym": "XRP", "side": "sell", "usd": 20.0, "qty": 10.0}]})
        return out, len(W._SEALED)
    finally:
        W._run, W.effective_home, W.home_reasons, W.pinned_home = saved[:4]
        W._SEALED[:] = saved[4]


out, n = seal_run(os.path.join(HERE, "pinned-folder"), os.path.join(HERE, "pinned-folder"))
check("G1a a seal from the pinned folder is remembered", out.get("ok") is True and n == 1, (out, n))
out, n = seal_run(os.path.join(HERE, "some-other-folder"), os.path.join(HERE, "pinned-folder"))
check("G1b a seal from a folder that is not the pinned one is NOT remembered", out.get("ok") is True and n == 0, (out, n))
out, n = seal_run(os.path.join(HERE, "some-other-folder"), None)
check("G1c with no pin at all, nothing is remembered", n == 0, n)

# ---- G7
st = {"orders_today": [
    {"sym": "XRP", "side": "sell", "usd": 20.0, "status": "PENDING", "txid": None, "fill": "UNCONFIRMED", "venue": "kraken"},
    {"sym": "ADA", "side": "sell", "usd": 20.0, "status": "PLACED", "txid": "OABC", "fill": "CONFIRMED", "venue": "kraken"},
    {"sym": "SOL", "side": "buy", "usd": 20.0},                       # a row from before fills were tracked
]}
held = T.unresolved_symbols(st)
check("G7a a PENDING row with no order id holds its symbol", set(held) == {"XRP"}, held)
kept, notes = T.drop_unresolved([{"sym": "xrp", "side": "sell", "usd": 20.0}, {"sym": "ADA", "side": "sell", "usd": 5.0},
                                 {"sym": "HBAR", "side": "buy", "usd": 5.0}], st)
check("G7b ...its order is dropped from the plan and the others are kept",
      [o["sym"] for o in kept] == ["ADA", "HBAR"] and len(notes) == 1 and "XRP" in notes[0].upper(), (kept, notes))
check("G7c a placed row and an old-style row hold nothing; a clean day changes nothing",
      T.drop_unresolved([{"sym": "ADA"}, {"sym": "SOL"}], st)[0] == [{"sym": "ADA"}, {"sym": "SOL"}]
      and T.drop_unresolved([{"sym": "XRP"}], {"orders_today": []}) == ([{"sym": "XRP"}], []))
check("G7d a state that is not a dict, or rows that are not dicts, raise nothing and hold nothing",
      T.unresolved_symbols(None) == {} and T.unresolved_symbols({"orders_today": ["x", 3, None]}) == {}
      and T.unresolved_symbols({"orders_today": "garbage"}) == {})
lost = {"orders_today": [{"sym": "ADA", "side": "buy", "usd": 5.0, "status": "PLACED", "txid": "", "fill": "UNCONFIRMED"}]}
check("G7e a tracked row with no order id is unresolved whatever its status says", set(T.unresolved_symbols(lost)) == {"ADA"})


# ---- G5 and G11
def day(filled=None, txid="OID1"):
    row = {"sym": "XRP", "side": "sell", "usd": 25.0, "venue": "kraken", "txid": txid, "fill": "UNCONFIRMED"}
    if filled is not None:
        row.update(filled_usd=filled, fill="CONFIRMED")
    return {"orders_today": [row]}


s = day()
T.apply_fills(s, {"fills": {"kraken": [{"order_id": "OID1", "usd": 25.0}]}})
check("G5a a fill is applied", s["orders_today"][0]["filled_usd"] == 25.0 and s["orders_today"][0]["fill"] == "CONFIRMED")
T.apply_fills(s, {"fills": {"kraken": [{"order_id": "OID1", "usd": 15.0}]}})
check("G5b a later, shorter page of fills does not take the confirmed amount back",
      s["orders_today"][0]["filled_usd"] == 25.0 and s["orders_today"][0]["fill"] == "CONFIRMED", s["orders_today"][0])
s = day(filled=10.0)
T.apply_fills(s, {"fills": {"kraken": [{"order_id": "OID1", "usd": 25.0}]}})
check("G5c ...and a larger amount still replaces a smaller one", s["orders_today"][0]["filled_usd"] == 25.0)
s = day(filled=float("nan"))
T.apply_fills(s, {"fills": {"kraken": [{"order_id": "OID1", "usd": 12.0}]}})
check("G5d a prior amount that is not a number is not kept", s["orders_today"][0]["filled_usd"] == 12.0)
s = day(txid="None")
T.apply_fills(s, {"fills": {"kraken": [{"usd": 25.0}, {"order_id": None, "usd": 25.0}, {"order_id": "", "usd": 25.0}]}})
check("G11 a fill with no order id is credited to no row, even one whose id reads 'None'",
      s["orders_today"][0].get("filled_usd") is None and s["orders_today"][0]["fill"] == "UNCONFIRMED", s["orders_today"][0])

# ---- G13 (the .bat is read, never run)
bat = open(os.path.join(HERE, "SENTINEL_ARM_AND_RUN.bat"), encoding="utf-8", errors="replace").read()
lines = [ln.strip() for ln in bat.splitlines()]
i_once = next(i for i, ln in enumerate(lines) if "covenant_trader.py --once" in ln and not ln.upper().startswith("REM"))
check("G13a the line after the first armed cycle sends a failed cycle to :cyclefailed",
      lines[i_once + 1].lower() == "if errorlevel 1 goto :cyclefailed", lines[i_once + 1])
i_lab = lines.index(":cyclefailed")
block = "\n".join(lines[i_lab:lines.index(":armfailed")])
check("G13b :cyclefailed writes armed false, removes armed_until, and exits non-zero",
      "c['armed']=False" in block and "c.pop('armed_until',None)" in block and "exit /b 1" in block
      and "c['armed']=True" not in block)

n = len(results)
print(f"\nG round 1: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
