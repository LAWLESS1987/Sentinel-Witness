#!/usr/bin/env python3
"""H7 -- an arming expires, and the arming file asks for a typed phrase.

2026-11-12, hardening pass, item 7 of an independent review of this folder.

THE DEFECT. SENTINEL_ARM_AND_RUN.bat armed the trader on one key press
(`choice /C YN`) and wrote `"armed": true`, which then stayed true until
somebody edited it back: one click in October was still an armed trader in
November. And guards.preconditions tested `if not cfg.get("armed")`, so any
truthy value armed it -- including the text "false".

  A  guards.arming(): armed only for the boolean true WITH an armed_until that
     is an ISO-8601 time carrying its zone, in the future, and at most 24
     hours ahead. Missing, malformed, zoneless, too far ahead, expired, or
     `armed` not a boolean -> DISARMED, each with its reason.
  B  guards.preconditions refuses an order on each of those, and clears one
     on a complete arming.
  C  covenant_trader.execute places live only on a complete arming; on an
     expired one the same order goes to the venue's DRY RUN and is not booked.
  D  a whole cycle on an expired arming runs disarmed and says why first.
  E  money_posture.py reports an expired arming as DISARMED.
  F  the .bat: no one-key arming, the explanation is shown, the phrase must
     match exactly, and the arming it writes carries armed_until (12 hours),
     is accepted by guards.arming, and keeps the rest of the config.

HOW F IS DRIVEN, since the .bat itself is never run by a test: the two Python
commands it runs are read OUT of the file and run here, in a temp folder, on
a temp trader_config.json. The batch around them (set /p, the labels, the
choice of interpreter) is read as text, not executed -- so a batch syntax
error would not be caught here. Uses temp folders and fake venues only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import guards as G           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


def iso(t, fmt="%Y-%m-%dT%H:%M:%SZ"):
    return time.strftime(fmt, time.gmtime(t))


arming = getattr(G, "arming", lambda cfg, now=None: (bool(cfg.get("armed")), "the code before the fix"))
NOW = float(int(time.time()))
H = 3600.0
NO_CFG = os.path.join(tempfile.mkdtemp(), "no_such_config.json")
TODAY = time.strftime("%Y-%m-%d")

# ---- A: guards.arming ------------------------------------------------------------
check("A1 not armed: {} and armed=false are disarmed, in the words every reader knows",
      arming({}, NOW) == (False, "armed=false in trader_config.json")
      and arming({"armed": False, "armed_until": iso(NOW + H)}, NOW)[0] is False)
good = {
    "one hour ahead, Z": iso(NOW + H),
    "23 h 59 min ahead": iso(NOW + 24 * H - 60),
    "with +00:00": iso(NOW + H, "%Y-%m-%dT%H:%M:%S+00:00"),
    "the same instant written in -05:00": iso(NOW + H - 5 * H, "%Y-%m-%dT%H:%M:%S-05:00"),
}
let = {k: arming({"armed": True, "armed_until": v}, NOW) for k, v in good.items()}
let = {k: v for k, v in let.items() if v[0] is not True}
check("A2 armed=true with an unexpired expiry inside 24 hours IS armed: " + "; ".join(good), not let, let)
bad = {
    "missing": (None, "missing"),
    "expired a minute ago": (iso(NOW - 60), "expired"),
    "expired yesterday": (iso(NOW - 24 * H), "expired"),
    "25 hours ahead": (iso(NOW + 25 * H), "at most 24"),
    "a year ahead": (iso(NOW + 365 * 24 * H), "at most 24"),
    "no zone": (iso(NOW + H, "%Y-%m-%dT%H:%M:%S"), "zone"),
    "a date only": (iso(NOW + H, "%Y-%m-%d"), "zone"),
    "the word tomorrow": ("tomorrow", "ISO"),
    "empty": ("", "ISO"),
    "a number": (NOW + H, "ISO"),
    "true": (True, "ISO"),
}
let = {}
for k, (v, needle) in bad.items():
    c = {"armed": True}
    if v is not None:
        c["armed_until"] = v
    ok, why = arming(c, NOW)
    if ok or not why.startswith("DISARMED") or needle not in why:
        let[k] = (ok, why)
check("A3 armed=true with armed_until " + ", ".join(bad) + " is DISARMED, each with its reason",
      not let, let)
let = {repr(v): arming({"armed": v, "armed_until": iso(NOW + H)}, NOW)
       for v in ("true", "false", 1, [True], {"yes": 1})}
let = {k: v for k, v in let.items() if v[0] or "boolean" not in v[1]}
check("A4 `armed` that is truthy but not the boolean true -- 'true', 'false', 1, a list -- is DISARMED",
      not let, let)
c = {"armed": True, "armed_until": iso(NOW + H)}
check("A5 the same arming read two hours later has expired, and the reason says when",
      arming(c, NOW)[0] is True and arming(c, NOW + 2 * H)[0] is False
      and c["armed_until"] in arming(c, NOW + 2 * H)[1], arming(c, NOW + 2 * H))


# ---- B: preconditions --------------------------------------------------------------
def cfg(**over):
    c = {"armed": True, "armed_until": iso(time.time() + H), "daily_plan_required": False,
         "seal_required": True, "min_sealed_signals": 0, "rule5_require_significance": False,
         "min_order_usd": 5.0, "max_order_usd": 25.0, "max_orders_per_day": 2,
         "max_daily_notional_usd": 50.0}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


ORDER = {"side": "sell", "usd": 20.0, "sym": "XLM"}


def pre(c):
    return G.preconditions(ORDER, cfg=c, st={"day": TODAY, "orders_today": []}, sealed_ok=True,
                           guard_blocks=[], config_path=NO_CFG)


check("B1 a complete arming clears the order", pre(cfg()) == [], pre(cfg()))
for label, over, needle in (("no armed_until", {"armed_until": None}, "missing"),
                            ("an expired one", {"armed_until": iso(time.time() - 60)}, "expired"),
                            ("one 3 days ahead", {"armed_until": iso(time.time() + 72 * H)}, "at most 24"),
                            ("armed the text 'true'", {"armed": "true"}, "boolean")):
    rs = pre(cfg(**over))
    check("B2 %s refuses the order: one reason, DISARMED, naming it" % label,
          len(rs) == 1 and rs[0].startswith("DISARMED") and needle in rs[0], rs)


# ---- C: execute() -------------------------------------------------------------------
class FakeVenue:
    name = "fake"

    def __init__(self):
        self.calls = []

    def has_credentials(self):
        return True

    # Later items of this pass ask a venue more before a live order (what its
    # key may do, what is open). A stand-in answers them the way a clean,
    # trade-only venue would, so this file keeps driving the ARMING alone.
    def key_permissions(self):
        return {"can_trade": True, "can_transfer": False, "can_withdraw": False, "source": "fake"}

    def open_orders_norm(self):
        return []

    def recent_fills(self, since=None):
        return []

    def reference_price(self, sym):
        return 1.0

    def place(self, sym, side, qty, live=False, **kw):
        self.calls.append((sym, side, qty, live))
        return {"descr": "fake", "txid": "TX1" if live else None}


def run_execute(c):
    fv = FakeVenue()
    keep_all, keep_state, keep_save = T.V.all_venues, T.STATE, T.save_state
    st = {"day": TODAY, "orders_today": []}
    try:
        T.V.all_venues = lambda: [fv]
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.save_state = lambda s: None
        res = T.execute(c, st, [{"sym": "XLM", "side": "sell", "qty": 20.0, "usd": 20.0,
                                 "rule": "test", "px": 1.0, "at": {"fake": 100.0}}], True, [])
    finally:
        T.V.all_venues, T.STATE, T.save_state = keep_all, keep_state, keep_save
    return res, fv, st


res, fv, st = run_execute(cfg())
check("C1 on a complete arming the order goes LIVE and is booked in the day's record",
      [c[3] for c in fv.calls] == [True] and res[0]["status"] == "PLACED" and len(st["orders_today"]) == 1,
      (fv.calls, res[0].get("status"), res[0].get("blocked_by")))
res, fv, st = run_execute(cfg(armed_until=iso(time.time() - 60)))
check("C2 on an EXPIRED arming the same order goes to the venue's DRY RUN and nothing is booked",
      [c[3] for c in fv.calls] == [False] and res[0]["status"] == "VALIDATED" and st["orders_today"] == []
      and any("expired" in b for b in res[0]["blocked_by"]), (fv.calls, res))
res, fv, st = run_execute(cfg(armed_until=None))
check("C3 ...and with no armed_until at all, the same",
      [c[3] for c in fv.calls] == [False] and st["orders_today"] == [], (fv.calls, res))


# ---- D: the cycle ---------------------------------------------------------------------
def cycle(c):
    seen = {}
    names = ("node_status", "gather", "plan", "execute", "witness_ready", "seal_decision",
             "load_state", "save_state", "STATE")
    keep = {k: getattr(T, k) for k in names}
    keep_rc = T.signal_ledger.record_cycle
    out = io.StringIO()
    try:
        T.STATE = os.path.join(tempfile.mkdtemp(), "trader_state.json")
        T.node_status = lambda ports, timeout=4: []
        T.witness_ready = lambda cfg, reserve=None: seen.setdefault("ready_armed", cfg.get("armed")) and None
        T.gather = lambda cfg: {"positions": [], "unpriced": [], "venue_notes": [], "total": 0.0, "cash": 0.0}
        T.plan = lambda cfg, pf, week_spent=0.0: ([], ["stub"])
        T.execute = lambda cfg, *a, **kw: seen.update(exec_armed=cfg.get("armed")) or []
        T.seal_decision = lambda cfg, rec: (True, "stub")
        T.load_state = lambda: {"orders_today": [], "day": "", "equity_peak": 0.0,
                                "equity_start_of_day": 0.0, "closed_trades": [],
                                "last_sold": {}, "sealed_signals": 0, "bought_total_usd": 0.0}
        T.save_state = lambda st: None
        T.signal_ledger.record_cycle = lambda positions, **kw: {
            "open": 0, "settled": 0, "wins": 0, "clears": False, "why": "stub"}
        with contextlib.redirect_stdout(out):
            T.run_once(c)
    finally:
        for k, v in keep.items():
            setattr(T, k, v)
        T.signal_ledger.record_cycle = keep_rc
    return seen, out.getvalue()


base = dict(T.DEFAULT_CONFIG, node_ports=[])
expired = dict(base, armed=True, armed_until=iso(time.time() - 3600))
seen, out = cycle(expired)
check("D1 a cycle on an EXPIRED arming says DISARMED and why, before anything else",
      "DISARMED: the arming expired" in out and out.find("DISARMED") < out.find("COVENANT TRADER"), out[:300])
check("D2 ...runs the whole cycle disarmed: the header says [disarmed] and execute is handed "
      "a disarmed config", "[disarmed]" in out and seen.get("exec_armed") is False
      and seen.get("ready_armed") is False, (seen, out[:200]))
check("D3 ...and the config it was given is not edited (the arming is read, never rewritten)",
      expired["armed"] is True)
seen, out = cycle(dict(base, armed=True, armed_until=iso(time.time() + 3600)))
check("D4 a cycle on a complete arming is ARMED, and the header shows until when",
      "[ARMED until " in out and seen.get("exec_armed") is True and "DISARMED" not in out, (seen, out[:200]))

# ---- E: money_posture -----------------------------------------------------------------
import money_posture as MP   # noqa: E402


def posture(config):
    p = os.path.join(tempfile.mkdtemp(), "trader_config.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    keep = (MP.CFG, MP._run)
    out = io.StringIO()
    try:
        MP.CFG, MP._run = p, (lambda cmd: "")          # no scheduler query from a test
        with contextlib.redirect_stdout(out):
            rc = MP.main()
    finally:
        MP.CFG, MP._run = keep
    return rc, out.getvalue()


rc, out = posture({"armed": True, "armed_until": iso(time.time() - 3600)})
check("E1 money_posture: armed=true with an EXPIRED armed_until is DISARMED (exit 0), and says why",
      rc == 0 and "DISARMED" in out and "expired" in out, (rc, out[-300:]))
rc, out = posture({"armed": True})
check("E2 ...armed=true with NO armed_until is DISARMED (exit 0)", rc == 0 and "missing" in out, (rc, out[-200:]))
rc, out = posture({"armed": True, "armed_until": iso(time.time() + 3600)})
check("E3 ...and a complete arming is ARMED (exit 1), with the time it ends",
      rc == 1 and "ARMED." in out and "armed until" in out, (rc, out[-300:]))

# ---- F: the .bat -----------------------------------------------------------------------
BAT = open(os.path.join(HERE, "SENTINEL_ARM_AND_RUN.bat"), encoding="utf-8").read()
code = "\n".join(l for l in BAT.splitlines() if not l.strip().upper().startswith("REM"))
check("F1 the one-key arming is gone: no `choice` in the commands", "choice" not in code.lower())
check("F2 it shows what arming means before it asks: REAL orders, REAL money, how long, and "
      "the trader's own --status", "WHAT ARMING MEANS" in code and "REAL orders" in code
      and "REAL money" in code and "covenant_trader.py --status" in code
      and code.find("WHAT ARMING MEANS") < code.find("set /p"))
m_phrase = re.search(r'^set "ARM_PHRASE=([^"]+)"', code, re.M)
m_hours = re.search(r'^set "ARM_HOURS=([^"]+)"', code, re.M)
check("F3 the operator must TYPE a phrase (set /p), and it is more than a letter",
      "set /p" in code and m_phrase is not None and len(m_phrase.group(1)) >= 10, m_phrase)
check("F4 the arming lasts 12 hours -- inside the 24 that guards.arming allows",
      m_hours is not None and float(m_hours.group(1)) == 12.0, m_hours)
check("F5 the interpreter is not taken from covenant_home in trader_config.json any more "
      "(it is this repository's, else the pinned folder's)",
      "ConvertFrom-Json" not in code and "covenant_home.pin" in code)
snippets = [m.group(1).replace("%%", "%") for m in re.finditer(r'^"%PY%" -c "(.*)"\s*$', code, re.M)]
check("F6 two Python commands are found in the file: the phrase check, then the arming",
      len(snippets) == 2 and "TYPED" in snippets[0] and "armed_until" in snippets[1],
      [s[:60] for s in snippets])
phrase_snip, arm_snip = (snippets + ["raise SystemExit(9)", "raise SystemExit(9)"])[:2]
PHRASE = m_phrase.group(1) if m_phrase else "?"
check("F7 the arming command comes only AFTER the phrase check and its refusal branch",
      0 < code.find(phrase_snip[:40]) < code.find("if errorlevel 1 goto :notarmed") < code.find("c['armed']=True"))


def run_snip(snip, cwd, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("TYPED", "ARM_PHRASE", "ARM_HOURS")}
    e.update(env)
    return subprocess.run([sys.executable, "-c", snip], cwd=cwd, env=e, capture_output=True,
                          text=True, timeout=60)


tmp = tempfile.mkdtemp()
typed = {"the phrase exactly": (PHRASE, 0), "lower case": (PHRASE.lower(), 1),
         "a trailing space": (PHRASE + " ", 1), "just Y": ("Y", 1), "yes": ("yes", 1), "nothing": ("", 1)}
let = {}
for k, (text, want) in typed.items():
    env = {"ARM_PHRASE": PHRASE}
    if text:
        env["TYPED"] = text
    rc = run_snip(phrase_snip, tmp, **env).returncode
    if rc != want:
        let[k] = rc
check("F8 the phrase check passes for the phrase exactly and for nothing else (lower case, a "
      "trailing space, Y, yes, Enter alone)", not let, let)
check("F8b ...and with no phrase defined at all it refuses", run_snip(phrase_snip, tmp, TYPED="").returncode == 1)

before = {"armed": False, "max_order_usd": 25.0, "covenant_home": "x", "rule5_waiver": {"waived": False}}
with open(os.path.join(tmp, "trader_config.json"), "w", encoding="utf-8") as fh:
    json.dump(before, fh)
t0 = time.time()
r = run_snip(arm_snip, tmp, ARM_HOURS=m_hours.group(1) if m_hours else "12")
after = json.load(open(os.path.join(tmp, "trader_config.json"), encoding="utf-8"))
check("F9 the arming command (run here on a TEMP config) writes armed=true WITH armed_until",
      r.returncode == 0 and after.get("armed") is True and isinstance(after.get("armed_until"), str), (r.stderr[-200:], after))
until = G.parse_utc(after.get("armed_until")) if hasattr(G, "parse_utc") else None
check("F10 ...12 hours from now, and guards.arming accepts it",
      until is not None and abs((until - t0) - 12 * H) < 120 and arming(after)[0] is True, (until, arming(after)))
check("F11 ...records who and when, and keeps every other key as it was",
      "phrase" in str(after.get("armed_by")) and after.get("armed_at")
      and all(after.get(k) == v for k, v in before.items() if k != "armed"), after)
check("F12 ...and leaves no temp file behind", os.listdir(tmp) == ["trader_config.json"], os.listdir(tmp))
with open(os.path.join(tmp, "trader_config.json"), "w", encoding="utf-8") as fh:
    json.dump(before, fh)
r = run_snip(arm_snip, tmp, ARM_HOURS="48")
check("F13 asked for 48 hours the arming command fails and the config is left disarmed",
      r.returncode != 0 and json.load(open(os.path.join(tmp, "trader_config.json"), encoding="utf-8")) == before,
      r.stderr[-200:])

n = len(results)
print(f"\nH7 arming: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
