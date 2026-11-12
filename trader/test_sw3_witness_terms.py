#!/usr/bin/env python3
"""SW3 -- the witness owns its own terms, driven both ways.

2026-11-09, on the operator's words "improve the Sentinel witness trading
branch and defensive scheme there. Maximum cybersecurity."

THE DEFECT (covenant ops/CONSULT_SENTINEL_WITNESS_2026-10-30.md, finding 1).
witness.seal_decision_result() handed covenant this trader's own cfg, and
covenant's seal read node_key, node_ports and the xrpl_* switches from it. So
the executor chose which node sealed its decisions and which key signed them,
and `seal_required: false` in its own gitignored file let a refused seal
through to a live order.

WHAT IS RUN HERE. Not stubs of the runner: the REAL witness._run starts a real
interpreter in a temp folder that stands in for covenant. That folder's
covenant_trader.py is a recorder -- its seal_decision_result returns the cfg it
was handed -- so each check reads what actually crossed the boundary.

  T1  an executor cfg naming another port, another key, an absolute key, or a
      public-ledger switch changes NOTHING covenant seals with.
  T2  covenant's own file decides, and covenant's own bad terms refuse:
      an absolute key, a key outside its folder, ports that are not ports,
      a config that will not parse.
  T3  a PYTHONPATH in this process's environment does not choose the code
      that runs in covenant.
  T4  `ok` is a yes only as the boolean true.
  T5  an ARMED config with the seal switched off refuses the whole cycle; a
      disarmed one is left alone.

WHAT THIS CANNOT SEE. It reads one population: the config road. The subprocess
is the same OS user on the same host, and a party who can edit witness.py or
covenant's folder is not stopped by anything tested here. No real covenant is
started, no key is opened, nothing is sealed or placed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("SENTINEL_COVENANT_HOME", None)

import witness as W          # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


RECORDER = '''
DEFAULT_CONFIG = {"node_ports": [5000], "node_key": "covenant_A.db.key",
                  "xrpl_record": False, "xrpl_network": "testnet",
                  "xrpl_allow_mainnet": False, "seal_required": True}
WHO = "covenant"

def seal_decision_result(cfg, record):
    return {"ok": True, "status": 200, "admission": "admitted", "tx_id": "t",
            "detail": "recorded", "mined": "", "saw_cfg": cfg,
            "saw_record": record, "who": WHO}
'''


def fake_covenant(own_cfg=None, raw=None):
    """A folder witness.covenant_home() accepts, whose covenant_trader records
    what it is handed. own_cfg is written as ITS trader_config.json."""
    d = tempfile.mkdtemp()
    open(os.path.join(d, "covenant_unified_v8.py"), "w").close()
    with open(os.path.join(d, "covenant_trader.py"), "w", encoding="utf-8") as fh:
        fh.write(RECORDER)
    open(os.path.join(d, "covenant_A.db.key"), "w").close()
    if raw is not None:
        with open(os.path.join(d, "trader_config.json"), "w", encoding="utf-8") as fh:
            fh.write(raw)
    elif own_cfg is not None:
        with open(os.path.join(d, "trader_config.json"), "w", encoding="utf-8") as fh:
            json.dump(own_cfg, fh)
    return d


def seal(home, executor_cfg=None, record=None):
    cfg = dict(executor_cfg or {})
    cfg["covenant_home"] = home
    return W.seal_decision_result(cfg, record or {"at": 1})


# ---- T1: the executor's cfg does not reach the seal -----------------------------
home = fake_covenant()
# H6 (2026-11-12): an ARMED config is now held to a PINNED witness folder -- one
# named in code or in a file the trader never writes -- and a covenant_home in
# the config alone is refused while armed. T1e-T1i and T5e-T5g hand the witness
# an armed config naming `home`, so this suite pins `home` in code, which is
# what an operator's pin does. What an armed config does WITHOUT a pin, or with
# a different folder, is driven both ways in test_h06_waivers_witness.py.
W.PINNED_COVENANT_HOME = home
OUTSIDE_KEY = os.path.join(tempfile.mkdtemp(), "attacker.key")
open(OUTSIDE_KEY, "w").close()

plain = seal(home)
check("T1a the real runner reaches the stand-in covenant and gets its answer",
      plain.get("ok") is True and plain.get("who") == "covenant", plain)
check("T1b the answer says whose terms sealed it", plain.get("terms") == "covenant", plain)
check("T1c covenant sealed with its OWN port and key",
      (plain.get("saw_cfg") or {}).get("node_ports") == [5000]
      and (plain.get("saw_cfg") or {}).get("node_key") == "covenant_A.db.key", plain.get("saw_cfg"))
check("T1d the record crosses unchanged", plain.get("saw_record") == {"at": 1}, plain.get("saw_record"))

hostile = {"node_ports": [6666], "node_key": OUTSIDE_KEY, "xrpl_record": True,
           "xrpl_network": "mainnet", "xrpl_allow_mainnet": True,
           "seal_required": False, "armed": True}
moved = seal(home, hostile)
check("T1e an executor cfg naming another port does not move the seal",
      (moved.get("saw_cfg") or {}).get("node_ports") == [5000], moved.get("saw_cfg"))
check("T1f ...nor an absolute key outside covenant",
      (moved.get("saw_cfg") or {}).get("node_key") == "covenant_A.db.key", moved.get("saw_cfg"))
check("T1g ...nor the public-ledger switches",
      (moved.get("saw_cfg") or {}).get("xrpl_record") is False
      and (moved.get("saw_cfg") or {}).get("xrpl_allow_mainnet") is False, moved.get("saw_cfg"))
check("T1h covenant's seal is handed the term keys and NOTHING else from anyone",
      sorted(moved.get("saw_cfg") or {}) == sorted(W.TERM_KEYS), sorted(moved.get("saw_cfg") or {}))
check("T1i the hostile cfg and the plain one seal on identical terms",
      moved.get("saw_cfg") == plain.get("saw_cfg"), (moved.get("saw_cfg"), plain.get("saw_cfg")))

# The payload itself, read at the runner: no cfg leaves this process at all.
sent = []
W.seal_decision_result({"covenant_home": home, "node_ports": [6666]}, {"at": 2},
                       runner=lambda h, s, payload, t: sent.append(payload) or (True, {"ok": True}, ""))
check("T1j the request holds the record and no cfg", sent == [{"record": {"at": 2}}], sent)

# T1k ADDED after mutation (2026-11-09): with covenant's side changed to merge
# a "cfg" out of the request, T1a-T1j all stayed green, because this side no
# longer sends one -- each half was covering for the other. So hand covenant's
# script a cfg directly, as an older or altered executor would, and read what
# it seals with.
ok_, direct, why_ = W._run(home, W._SEAL_SCRIPT, {"cfg": hostile, "record": {"at": 3}}, 60)
check("T1k a cfg sent straight to covenant's script is ignored, not merged",
      ok_ and isinstance(direct, dict) and direct.get("saw_cfg") == plain.get("saw_cfg"),
      (ok_, why_, (direct or {}).get("saw_cfg") if isinstance(direct, dict) else direct))

# ---- T2: covenant's own file decides, and its own bad terms refuse ---------------
own = fake_covenant({"node_ports": [5020]})
check("T2a covenant's own trader_config.json is what sets the port",
      (seal(own, {"node_ports": [6666]}).get("saw_cfg") or {}).get("node_ports") == [5020])

bad_terms = {
    "an absolute key": {"node_key": OUTSIDE_KEY},
    "a key that climbs out of the folder": {"node_key": os.path.join("..", "k.key")},
    "an empty key": {"node_key": ""},
    "a key that is not text": {"node_key": 7},
    "no ports": {"node_ports": []},
    "ports that are not a list": {"node_ports": 5000},
    "a port that is text": {"node_ports": ["5000"]},
    "a port that is true": {"node_ports": [True]},
    "a port out of range": {"node_ports": [70000]},
}
for name, cfg_ in bad_terms.items():
    r = seal(fake_covenant(cfg_))
    check(f"T2b covenant's own terms with {name}: refused, nothing sealed",
          r.get("ok") is False and "witness terms" in str(r.get("detail")) and "who" not in r, r)
r = seal(fake_covenant(raw="{not json"))
check("T2c covenant's own config will not parse: refused, not defaulted",
      r.get("ok") is False and "will not parse" in str(r.get("detail")), r)
r = seal(fake_covenant(raw="[1, 2]"))
check("T2d covenant's own config is not an object: refused",
      r.get("ok") is False and "not an object" in str(r.get("detail")), r)

# ---- T3: the environment does not choose the code --------------------------------
decoy = tempfile.mkdtemp()
with open(os.path.join(decoy, "covenant_trader.py"), "w", encoding="utf-8") as fh:
    fh.write(RECORDER.replace('WHO = "covenant"', 'WHO = "decoy"'))
# A covenant whose own folder holds NO covenant_trader.py: if PYTHONPATH were
# inherited, the decoy would be imported and answer. It must fail instead.
bare = tempfile.mkdtemp()
open(os.path.join(bare, "covenant_unified_v8.py"), "w").close()
keep_pp = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = decoy
try:
    env = W._child_env()
    check("T3a PYTHONPATH is not handed to covenant's interpreter",
          not any(k.upper().startswith("PYTHON") for k in env), [k for k in env if k.upper().startswith("PYTHON")])
    check("T3b everything else in the environment is", "PATH" in {k.upper() for k in env})
    r = seal(bare)
    check("T3c with a decoy on PYTHONPATH, a covenant with no seal code REFUSES (the decoy never answers)",
          r.get("ok") is False and r.get("who") != "decoy", r)
    r = seal(home)
    check("T3d ...and a real one still answers as itself", r.get("who") == "covenant", r)
finally:
    if keep_pp is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = keep_pp

# ---- T4: ok is a boolean ----------------------------------------------------------
cfg_home = {"covenant_home": home}
for name, val in (("the text 'false'", "false"), ("the number 1", 1), ("a list", [0]), ("null", None)):
    r = W.seal_decision_result(cfg_home, {"at": 1},
                               runner=lambda *a, v=val: (True, {"ok": v, "status": 200, "admission": "admitted"}, ""))
    check(f"T4a an 'ok' that is {name}: refused", r["ok"] is False and not T.admitted(r), r)
good = {"ok": True, "status": 200, "admission": "admitted", "tx_id": "t", "detail": "HTTP 200", "mined": ""}
check("T4b a real true passes through unchanged",
      W.seal_decision_result(cfg_home, {"at": 1}, runner=lambda *a: (True, good, "")) == good)
no = dict(good, ok=False)
check("T4c a real false passes through as a refusal",
      W.seal_decision_result(cfg_home, {"at": 1}, runner=lambda *a: (True, no, ""))["ok"] is False)

# ---- T5: armed with the seal off refuses the cycle ---------------------------------
check("T5a armed + seal_required true: no reason", W.terms_reasons({"armed": True, "seal_required": True}) == [])
for name, cfg_ in (("false", {"armed": True, "seal_required": False}),
                   ("absent", {"armed": True}),
                   ("the text 'true'", {"armed": True, "seal_required": "true"}),
                   ("1", {"armed": True, "seal_required": 1})):
    check(f"T5b armed + seal_required {name}: a reason", len(W.terms_reasons(cfg_)) == 1, W.terms_reasons(cfg_))
check("T5c disarmed + seal off: left alone (it places nothing)",
      W.terms_reasons({"armed": False, "seal_required": False}) == [])
check("T5d something that is not a config: no reason invented", W.terms_reasons(None) == [])

os.makedirs(os.path.join(home, "private"), exist_ok=True)
rp = os.path.join(home, "private", "RESERVE.json")
open(rp, "w").write("{}")
check("T5e witness_ready: covenant, floors, armed, sealed -> ready",
      T.witness_ready({"covenant_home": home, "armed": True, "seal_required": True}, reserve=rp) is None)
why = T.witness_ready({"covenant_home": home, "armed": True, "seal_required": False}, reserve=rp)
check("T5f witness_ready: the same with the seal off -> the reason", why and "unwitnessed" in why, why)

touched = []
keep = {k: getattr(T, k) for k in ("gather", "load_state", "save_state", "seal_decision", "RESERVE_PATH")}
try:
    T.RESERVE_PATH = rp
    T.gather = lambda cfg: touched.append("gather") or {}
    T.load_state = lambda: touched.append("load_state") or {}
    T.save_state = lambda st: touched.append("save_state")
    T.seal_decision = lambda cfg, rec: touched.append("seal") or (False, "")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        T.run_once({"covenant_home": home, "armed": True, "seal_required": False})
    check("T5g a real cycle, armed with the seal off: reads, plans, seals and saves NOTHING",
          not touched and "REFUSING THIS CYCLE" in out.getvalue() and "unwitnessed" in out.getvalue(),
          (touched, out.getvalue()[-200:]))
finally:
    for k, v in keep.items():
        setattr(T, k, v)

n = len(results)
print(f"\nSW3 witness terms: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
