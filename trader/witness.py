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

THE WITNESS OWNS ITS OWN TERMS (SW3, 2026-11-09). Until today the seal call
handed covenant THIS trader's cfg, and covenant's seal read node_key,
node_ports and the xrpl_* switches out of it -- so the party being witnessed
chose which node sealed and which key signed (covenant's consult of
2026-10-30, finding 1). Now only the record crosses the boundary. Covenant
reads those terms from its own trader_config.json over its own defaults,
refuses a key that is not a file inside its own folder and ports that are not
a list of port numbers, and says so in the answer ("terms": "covenant"). And
an ARMED config that turns the seal off is refused for the whole cycle
(terms_reasons): Sentinel-Witness places nothing unwitnessed.

WHAT THIS DOES NOT DO, said here so nobody reads more into it: the subprocess
is still this OS user on this host, and this file still supplies the script
it runs. Someone who can edit this file, or covenant's folder, is not stopped.
What is closed is the config road -- an edited or second trader_config.json
can no longer move the seal.

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
#
# SW3: the request carries the RECORD and nothing else. The terms of the seal
# -- which key signs, which node is asked, whether a public record is written
# -- are covenant's, read here from covenant's own file over covenant's own
# defaults. A "cfg" in the request is ignored, not merged.
TERM_KEYS = ("node_key", "node_ports", "xrpl_record", "xrpl_network",
             "xrpl_allow_mainnet")

_SEAL_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, ".")
req = json.loads(sys.stdin.read())
import covenant_trader as T

def refuse(why):
    print(json.dumps({"ok": False, "status": None, "admission": None,
                      "tx_id": None, "held_not_judged": False,
                      "not_proven": False, "terms": "covenant",
                      "detail": "witness terms: " + why, "mined": ""}))
    sys.exit(0)

home = os.path.realpath(os.getcwd())
own = dict(getattr(T, "DEFAULT_CONFIG", {}))
p = os.path.join(home, "trader_config.json")
if os.path.isfile(p):
    try:
        with open(p, encoding="utf-8") as fh:
            mine = json.load(fh)
    except (OSError, ValueError) as e:
        refuse("covenant's own trader_config.json will not parse (%s)" % type(e).__name__)
    if not isinstance(mine, dict):
        refuse("covenant's own trader_config.json is not an object")
    own.update(mine)
terms = {k: own.get(k) for k in TERM_KEYS_HERE}

key = terms.get("node_key")
if not isinstance(key, str) or not key.strip():
    refuse("covenant names no node_key")
full = os.path.realpath(os.path.join(home, key))
try:
    inside = os.path.commonpath([full, home]) == home and full != home
except ValueError:
    inside = False
if os.path.isabs(key) or os.path.splitdrive(key)[0] or not inside:
    refuse("node_key must be a file inside covenant's own folder")
ports = terms.get("node_ports")
if (not isinstance(ports, list) or not ports or
        any(isinstance(x, bool) or not isinstance(x, int) or not 0 < x < 65536
            for x in ports)):
    refuse("node_ports must be a non-empty list of port numbers")

r = T.seal_decision_result(terms, req["record"])
if isinstance(r, dict):
    r["terms"] = "covenant"
print(json.dumps(r))
""".replace("TERM_KEYS_HERE", repr(TERM_KEYS))

# `now` is an epoch number or null, which is what covenant_daily_plan.today()
# takes (time.localtime(now)).
_GATE_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
req = json.loads(sys.stdin.read())
import covenant_daily_plan as D
print(json.dumps(D.gate_reasons(now=req.get("now"))))
"""


def _child_env():
    """This process's environment without the variables that tell a Python
    interpreter WHAT CODE TO LOAD (PYTHONPATH, PYTHONSTARTUP, PYTHONHOME,
    PYTHONINSPECT ...). SW3: the reason for a subprocess is that covenant's
    code signs the seal; a PYTHONPATH inherited from whatever started this
    trader would put some other folder's covenant_trader.py first. Everything
    else is kept -- covenant reads its own COVENANT_* settings."""
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith("PYTHON")}


def _run(home, script, payload, timeout):
    """(ok, parsed-last-line-or-None, why)."""
    try:
        p = subprocess.run([_python(home), "-c", script], cwd=home,
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=timeout, env=_child_env())
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
    # SW3: `cfg` finds covenant and goes no further. Only the record crosses.
    ok, out, why = (runner or _run)(home, _SEAL_SCRIPT,
                                    {"record": record}, SEAL_TIMEOUT_S)
    if not ok:
        return _refused("witness: " + why)
    if not isinstance(out, dict) or "ok" not in out:
        return _refused("witness: covenant's seal answer had no 'ok' field")
    # SW3: `ok` is a yes only as the boolean true. A truthy string ("false",
    # "no") or a number would otherwise read as sealed downstream.
    if not isinstance(out["ok"], bool):
        return _refused("witness: covenant's seal answer had an 'ok' that is "
                        "not true or false (%s)" % type(out["ok"]).__name__)
    return out


def terms_reasons(cfg):
    """Why this trader's config is LOOSER than the witness allows. [] is clear.

    SW3. One term today: an armed config may not switch the seal off. With
    seal_required false a refused seal does not stop a live order
    (guards.preconditions), so the switch was a way to trade unwitnessed, and
    it sat in the executor's own gitignored file. A disarmed config is left
    alone: it places nothing, and plan-only runs and the copied suites use it."""
    if not isinstance(cfg, dict) or not cfg.get("armed"):
        return []
    if cfg.get("seal_required") is not True:
        return ["armed with seal_required not true -- Sentinel-Witness places "
                "nothing unwitnessed; set seal_required to true"]
    return []


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
