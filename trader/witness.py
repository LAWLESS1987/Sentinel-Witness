#!/usr/bin/env python3
"""witness.py -- the one boundary between this trader and covenant.

Sentinel-Witness EXECUTES; covenant WITNESSES. This trader reads balances,
plans, and places orders. Three things it does not own, and asks covenant for
on every cycle:

  1. THE SEAL. Each cycle's decision is signed with covenant's node key and
     submitted to covenant's node, whose ethics gate admits or refuses it, and
     which mines it into a block. seal_required=true means a refused or failed
     seal stops live orders.
  2. THE DAY'S PLAN APPROVAL. The operator's own rule (2026-09-12): "i'll have
     to daily approve of the strategy it lays out". The approvals live in
     covenant (covenant_daily_plan.py, ops/daily_plan/); nothing goes live
     without today's.
  3. THE RESERVE FLOORS. private/RESERVE.json in covenant holds the frozen
     hold-only floors (XRP, LINK, HBAR) and the starting book the buy budget is
     measured against. It is read from there, never re-created here: a missing
     file would otherwise read as "no floors set yet" and re-anchor every floor
     at today's quantity (see covenant_trader.ReserveUnreadable).

WHY A SUBPROCESS, NOT AN IMPORT. This repository carries its own, older
covenant_unified_v8.py at its root. Importing covenant's modules into this
process would let a name collision decide which node code signs the seal. Each
call below runs in covenant's own folder, with covenant's own interpreter when
it has a .venv, so the code that signs is the code covenant runs.

EVERY FAILURE REFUSES. No covenant_home, no interpreter, a timeout, a crash,
unparseable output -- each one is a refusal with its reason, never a pass.

WHERE COVENANT IS: env SENTINEL_COVENANT_HOME, else trader_config.json
"covenant_home". There is no built-in default, so a machine without covenant
cannot trade by accident.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "trader_config.json")

SEAL_TIMEOUT_S = 700        # the node's own reads allow 15+45 s and 2 x 310 s
GATE_TIMEOUT_S = 120


def covenant_home(cfg=None):
    """Covenant's folder, or None when it is not configured or not there."""
    home = os.environ.get("SENTINEL_COVENANT_HOME")
    if not home:
        if cfg is None:
            try:
                with open(CONFIG, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (OSError, ValueError):
                cfg = {}
        home = (cfg or {}).get("covenant_home") if isinstance(cfg, dict) else None
    if not home or not os.path.isfile(os.path.join(home, "covenant_unified_v8.py")):
        return None
    return os.path.abspath(home)


def reserve_path(cfg=None):
    """Covenant's private/RESERVE.json.

    With no covenant configured this names trader/private/RESERVE.json, which
    is gitignored here exactly as it is in covenant -- but no live cycle ever
    reaches it: covenant_trader.run_once() refuses to run at all without
    covenant_home, and refuses when the floor file is missing, so this
    repository never creates floors of its own."""
    home = covenant_home(cfg)
    if not home:
        return os.path.join(HERE, "private", "RESERVE.json")
    return os.path.join(home, "private", "RESERVE.json")


def halt_path(cfg=None):
    """Covenant's TRADER_HALT. A halt dropped in either folder stops this
    trader; one folder's halt never needs to be remembered in the other."""
    home = covenant_home(cfg)
    return os.path.join(home, "TRADER_HALT") if home else None


def _python(home):
    for rel in (os.path.join(".venv", "Scripts", "python.exe"),
                os.path.join(".venv", "bin", "python")):
        p = os.path.join(home, rel)
        if os.path.isfile(p):
            return p
    return sys.executable


# Runs inside covenant's folder. Reads one JSON object on stdin and prints one
# JSON object as the LAST line of stdout; anything the imported modules print
# before it is ignored by the parser below.
_SEAL_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
req = json.loads(sys.stdin.read())
import covenant_trader as T
r = T.seal_decision_result(req["cfg"], req["record"])
print(json.dumps(r))
"""

# `now` is an epoch number or null, which is what covenant_daily_plan.today()
# takes (time.localtime(now)).
_GATE_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
req = json.loads(sys.stdin.read())
import covenant_daily_plan as D
print(json.dumps(D.gate_reasons(now=req.get("now"))))
"""


def _run(home, script, payload, timeout):
    """(ok, parsed-last-line-or-None, why)."""
    try:
        p = subprocess.run([_python(home), "-c", script], cwd=home,
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, "covenant did not answer within %d s" % timeout
    except OSError as e:
        return False, None, "could not start covenant's python (%s)" % e
    lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
    if p.returncode != 0 or not lines:
        tail = ((p.stderr or "").strip().splitlines() or ["no output"])[-1]
        return False, None, "covenant exited %s: %s" % (p.returncode, tail[:160])
    try:
        return True, json.loads(lines[-1]), ""
    except ValueError:
        return False, None, "covenant's answer was not JSON: %s" % lines[-1][:120]


def _refused(detail):
    return {"ok": False, "status": None, "admission": None, "tx_id": None,
            "held_not_judged": False, "not_proven": False,
            "detail": detail, "mined": ""}


def seal_decision_result(cfg, record, runner=None):
    """Covenant's seal_decision_result, run in covenant. Same dict shape."""
    home = covenant_home(cfg)
    if not home:
        return _refused("witness: covenant_home is not configured (env "
                        "SENTINEL_COVENANT_HOME or trader_config.json), so "
                        "nothing can be sealed")
    ok, out, why = (runner or _run)(home, _SEAL_SCRIPT,
                                    {"cfg": cfg, "record": record}, SEAL_TIMEOUT_S)
    if not ok:
        return _refused("witness: " + why)
    if not isinstance(out, dict) or "ok" not in out:
        return _refused("witness: covenant's seal answer had no 'ok' field")
    return out


def daily_plan_gate_reasons(now=None, cfg=None, runner=None):
    """Covenant's covenant_daily_plan.gate_reasons(). [] means today's plan is
    approved; anything else is a reason no order may go live."""
    home = covenant_home(cfg)
    if not home:
        return ["daily plan gate unavailable (covenant_home not configured) "
                "-- nothing may go live"]
    if now is not None and (isinstance(now, bool) or not isinstance(now, (int, float))):
        return ["daily plan gate unavailable (now must be epoch seconds, got %s) "
                "-- nothing may go live" % type(now).__name__]
    payload = {"now": now}
    ok, out, why = (runner or _run)(home, _GATE_SCRIPT, payload, GATE_TIMEOUT_S)
    if not ok:
        return ["daily plan gate unavailable (%s) -- nothing may go live" % why]
    if not isinstance(out, list) or not all(isinstance(x, str) for x in out):
        return ["daily plan gate unavailable (answer was not a list of "
                "reasons) -- nothing may go live"]
    return out


if __name__ == "__main__":
    home = covenant_home()
    print("covenant_home :", home or "NOT CONFIGURED")
    print("reserve file  :", reserve_path(), "(present)" if os.path.isfile(reserve_path()) else "(MISSING)")
    print("daily plan    :", daily_plan_gate_reasons() or "approved for today")
