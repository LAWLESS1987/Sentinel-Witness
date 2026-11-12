#!/usr/bin/env python3
"""H9 -- a key that can move funds out places no live order, and a credential
another account can read is not used.

2026-11-12, hardening pass, item 9 of an independent review of this folder.

THE DEFECT. Nothing checked what an exchange key was allowed to do. The README
asks for "a trade-scoped exchange key ... with no withdrawal permission"; a
key that could also withdraw or transfer worked exactly the same. And nothing
checked who could read the credential file.

THE ENDPOINTS, read in the venues' own documentation on 2026-11-12:

  Coinbase   GET /api/v3/brokerage/key_permissions -> can_view, can_trade,
             can_transfer (booleans). One can_transfer covers deposit and
             withdrawal, so it is read as both.
             docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/
             data-api/get-api-key-permissions
  Kraken     POST /0/private/GetApiKeyInfo -> result.permissions, a list of
             strings (query-funds, add-funds, withdraw-funds, earn-funds,
             query-open-trades, query-closed-trades, modify-trades,
             close-trades, query-ledger, export-data, create-ws-token,
             add-withdraw-address, update-withdraw-address).
             docs.kraken.com/api-reference/account-data/get-api-key-info
  Robinhood  no such endpoint is listed at docs.robinhood.com/crypto/trading.
             Its key's scope is a record the operator writes by hand.

  K  Kraken: a trade-only key clears; each fund-moving permission, a
     permission nobody has classified, a key that cannot trade, and an answer
     that cannot be read, each refuse with the reason.
  C  Coinbase: the same.
  R  Robinhood: no record refuses; a complete trade-only record clears.
  E  the default fetch asks the documented endpoint.
  X  covenant_trader.execute places live on a trade-only key only, asks each
     venue once a cycle, and never asks when nothing could go live.
  A  credential files: who else can read them, from a Windows ACL or POSIX
     mode -- with fake readers, and with the real reader on a temp file.
  V  a venue refuses to read a shared credential.
  P  key_scope.py makes no network call.

WHAT THIS CANNOT SEE. The venues' real answers: every fetch here is a fake,
and the field names are as documented, not as observed (Kraken's page gives
its schema in snake_case and its example in camelCase; `permissions` is the
same in both). A key's permissions are read at the moment of the order; a key
changed at the venue afterwards is seen on the next cycle, not this one.
"""
from __future__ import annotations

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

import key_scope as K        # noqa: E402
import venues as V           # noqa: E402
import covenant_trader as T  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:300]}"), flush=True)


def boom():
    raise V.VenueError("kraken: EGeneral:Permission denied")


TRADE_ONLY = ["query-funds", "query-open-trades", "query-closed-trades", "modify-trades", "close-trades"]

# ---- K: Kraken -------------------------------------------------------------------
kr = V.KrakenVenue()
check("K1 a trade-only Kraken key clears: no reason", K.key_reasons(kr, lambda: {"permissions": TRADE_ONLY}) == [],
      K.key_reasons(kr, lambda: {"permissions": TRADE_ONLY}))
check("K1b ...with every permission a trade-only key may carry (ledger, export, websocket)",
      K.key_reasons(kr, lambda: {"permissions": sorted(K.KRAKEN_TRADE_ONLY)}) == [])
check("K1c ...and read from the camelCase shape Kraken's own example shows",
      K.key_reasons(kr, lambda: {"apiKeyName": "k", "nonceWindow": 0, "permissions": TRADE_ONLY,
                                 "ipAllowlist": []}) == [])
moving = {"withdraw-funds": "WITHDRAW", "add-withdraw-address": "WITHDRAW",
          "update-withdraw-address": "WITHDRAW", "earn-funds": "TRANSFER", "add-funds": "TRANSFER"}
let = {}
for perm, word in moving.items():
    r = K.key_reasons(kr, lambda p=perm: {"permissions": TRADE_ONLY + [p]})
    if not (len(r) == 1 and word in r[0] and perm in r[0]):
        let[perm] = r
check("K2 a Kraken key that also carries any of " + ", ".join(moving) + " is refused, naming it",
      not let, let)
r = K.key_reasons(kr, lambda: {"permissions": TRADE_ONLY + ["brand-new-permission"]})
check("K3 a permission nobody has classified refuses too (the list is what a trade-only key "
      "needs, not what is known to be dangerous)", len(r) == 1 and "brand-new-permission" in r[0], r)
r = K.key_reasons(kr, lambda: {"permissions": ["query-funds", "query-open-trades"]})
check("K4 a key that cannot trade is said to be one", len(r) == 1 and "cannot trade" in r[0], r)
unreadable = {"an empty object": lambda: {}, "permissions null": lambda: {"permissions": None},
              "an empty list": lambda: {"permissions": []}, "a list of numbers": lambda: {"permissions": [1, 2]},
              "a string": lambda: "ok", "the venue refusing the call": boom}
let = {k: K.key_reasons(kr, f) for k, f in unreadable.items()}
let = {k: v for k, v in let.items() if not (len(v) == 1 and "cannot be read" in v[0] and "no live order" in v[0])}
check("K5 an answer that is " + ", ".join(unreadable) + " -> the permissions cannot be read, no live order",
      not let, let)

# ---- C: Coinbase ------------------------------------------------------------------
cb = V.CoinbaseVenue()
ok_js = {"can_view": True, "can_trade": True, "can_transfer": False,
         "portfolio_uuid": "u", "portfolio_type": "DEFAULT"}
check("C1 a Coinbase key with can_trade and without can_transfer clears", K.key_reasons(cb, lambda: dict(ok_js)) == [])
r = K.key_reasons(cb, lambda: dict(ok_js, can_transfer=True))
check("C2 can_transfer=true is refused as able to TRANSFER and to WITHDRAW (Coinbase reports one flag for both)",
      len(r) == 2 and any("WITHDRAW" in x for x in r) and any("TRANSFER" in x for x in r), r)
r = K.key_reasons(cb, lambda: dict(ok_js, can_trade=False))
check("C3 can_trade=false: the key cannot trade", len(r) == 1 and "cannot trade" in r[0], r)
bad_cb = {"no can_transfer": {"can_trade": True}, "the text 'false'": dict(ok_js, can_transfer="false"),
          "null": dict(ok_js, can_transfer=None), "0": dict(ok_js, can_transfer=0), "a list": []}
let = {k: K.key_reasons(cb, lambda v=v: v) for k, v in bad_cb.items()}
let = {k: v for k, v in let.items() if not (len(v) == 1 and "cannot be read" in v[0])}
check("C4 can_transfer that is " + ", ".join(bad_cb) + " -> cannot be read, no live order", not let, let)
check("C5 Coinbase refusing the call -> cannot be read", "cannot be read" in K.key_reasons(cb, boom)[0])

# ---- R: Robinhood -----------------------------------------------------------------
rh = V.RobinhoodVenue()
keep_scope = K.KEY_SCOPE_FILE
tmp = tempfile.mkdtemp()


def scope_file(obj):
    p = os.path.join(tmp, "%d_key_scope.json" % len(os.listdir(tmp)))
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    K.KEY_SCOPE_FILE = p
    return p


REC = {"can_trade": True, "can_transfer": False, "can_withdraw": False, "by": "operator",
       "at": "2026-11-12", "words": "Trading: enabled. Transfers: none listed."}
try:
    check("R0 Robinhood declares that it has no endpoint to ask", rh.KEY_SCOPE_ENDPOINT is None
          and kr.KEY_SCOPE_ENDPOINT and cb.KEY_SCOPE_ENDPOINT)
    K.KEY_SCOPE_FILE = os.path.join(tmp, "absent.json")
    r = K.key_reasons(rh)
    check("R1 with no hand-written record, live Robinhood orders are refused, and the file is named",
          len(r) == 1 and "cannot be read" in r[0] and "absent.json" in r[0], r)
    scope_file({"robinhood": REC})
    check("R2 a complete trade-only record clears", K.key_reasons(rh) == [], K.key_reasons(rh))
    scope_file({"robinhood": dict(REC, can_withdraw=True)})
    check("R3 a record that says the key can withdraw is refused on its own word",
          any("WITHDRAW" in x for x in K.key_reasons(rh)))
    partial = {"no words": {k: v for k, v in REC.items() if k != "words"}, "blank by": dict(REC, by=" "),
               "can_trade the text 'true'": dict(REC, can_trade="true"), "a list": [REC]}
    let = {}
    for k, row in partial.items():
        scope_file({"robinhood": row})
        r = K.key_reasons(rh)
        if not (len(r) == 1 and "cannot be read" in r[0]):
            let[k] = r
    check("R4 a record with " + ", ".join(partial) + " is no record", not let, let)
    scope_file({"kraken": REC})
    check("R5 a record for ANOTHER venue does not cover this one", "cannot be read" in K.key_reasons(rh)[0])
    K.KEY_SCOPE_FILE = os.path.join(HERE, "private", "key_scope.json")
    check("R6 a record inside the trader's own folder is refused (the trader writes there)",
          "inside the trader" in K.key_reasons(rh)[0], K.key_reasons(rh))
    scope_file({"robinhood": REC, "kraken": dict(REC, can_withdraw=True)})
    check("R7 the hand record never stands in for a venue that CAN be asked: Kraken is asked, "
          "and its own answer decides", K.key_reasons(kr, lambda: {"permissions": TRADE_ONLY}) == []
          and "cannot be read" in K.key_reasons(kr, boom)[0])
finally:
    K.KEY_SCOPE_FILE = keep_scope

# ---- D: an adapter that cannot say ---------------------------------------------------


class Mute:
    name = "mute"


class Odd:
    name = "odd"

    def key_permissions(self, fetch=None):
        return {"can_trade": True}


check("D1 an adapter with no key_permissions at all: no live order", "cannot say" in K.key_reasons(Mute())[0])
check("D2 an adapter that answers with less than three true/false values: no live order",
      "cannot be read" in K.key_reasons(Odd())[0])

# ---- E: the default fetch asks the documented endpoint ---------------------------------
asked = []
kr2 = V.KrakenVenue()
kr2._private = lambda endpoint, data=None: asked.append(endpoint) or {"permissions": TRADE_ONLY}
cb2 = V.CoinbaseVenue()
cb2._call = lambda method, path, body=None: asked.append((method, path)) or dict(ok_js)
ok_both = K.key_reasons(kr2) == [] and K.key_reasons(cb2) == []
check("E1 with no fetch handed in, Kraken is asked GetApiKeyInfo and Coinbase GET "
      "/api/v3/brokerage/key_permissions, each once",
      ok_both and asked == ["GetApiKeyInfo", ("GET", "/api/v3/brokerage/key_permissions")], asked)


# ---- X: execute() ------------------------------------------------------------------------
# R1 (2026-11-12): an order goes live only through guards.live_gate, which
# judges the config and state on DISK, the witness and the venue. The dict
# fixture this section used -- armed, with the other gates switched off --
# can no longer go live at all, so it now runs on gated_fixture.Gated: every
# gate satisfied, on disk, with a stand-in covenant, and then the ONE thing
# this section is about is varied.
from gated_fixture import Gated, Venue, order as gated_order   # noqa: E402

CLEAN = {"can_trade": True, "can_transfer": False, "can_withdraw": False, "source": "fake"}


def order(sym, venue, usd=20.0):
    return gated_order(sym, "sell", usd, venue=venue)


def run_execute(venues, orders, plan_only=False, **cfg_on_disk):
    """execute() with every other gate satisfied (the plan proposes exactly
    these orders); what varies is the VENUE's key. Returns (results, rows)."""
    with Gated(approved=orders, cfg=cfg_on_disk) as fx:
        res = fx.execute([dict(o) for o in orders], venues, plan_only=plan_only)
        return res, fx.state_on_disk()["orders_today"]


def calls(v):
    return [(c["sym"], c["live"]) for c in v.calls]


good, bad = Venue("good", CLEAN), Venue("bad", dict(CLEAN, can_withdraw=True))
res, rows = run_execute([good, bad], [order("XLM", "good"), order("ADA", "bad", 25.0)])
check("X1 the order at the trade-only venue goes LIVE; the one at the venue whose key can "
      "withdraw goes to the DRY RUN only", calls(good) == [("XLM", True)] and calls(bad) == [("ADA", False)]
      and [r["status"] for r in res] == ["PLACED", "VALIDATED"], (calls(good), calls(bad), [r["status"] for r in res]))
check("X1b ...and that order says why, and is not in the day's record",
      any("WITHDRAW" in b for b in res[1]["blocked_by"]) and [r["sym"] for r in rows] == ["XLM"],
      (res[1].get("blocked_by"), rows))
good = Venue("good", CLEAN)
run_execute([good], [order("XLM", "good"), order("ADA", "good", 25.0)])
check("X2 two live orders at one venue: its key is asked about before EACH (the gate asks for "
      "every order it permits, and keeps nothing a caller could have put there)",
      good.key_asked == 2 and good.live() == [True, True], (good.key_asked, calls(good)))
blind = Venue("blind", V.VenueError("HTTP 503: unavailable"))
res, rows = run_execute([blind], [order("XLM", "blind")])
check("X3 a venue whose key cannot be asked about (the call fails): nothing goes live",
      calls(blind) == [("XLM", False)] and rows == []
      and any("cannot be read" in b for b in res[0]["blocked_by"]), (calls(blind), res[0].get("blocked_by")))
good = Venue("good", CLEAN)
run_execute([good], [order("XLM", "good")], armed=False)
check("X4 a DISARMED cycle never asks (nothing could go live), and the order is a dry run",
      good.key_asked == 0 and calls(good) == [("XLM", False)], (good.key_asked, calls(good)))
good = Venue("good", CLEAN)
run_execute([good], [order("XLM", "good")], plan_only=True)
check("X5 a plan-only cycle never asks either, and calls no venue", good.key_asked == 0 and good.calls == [])

# ---- A: who else can read the credential ---------------------------------------------------
ME, SYSTEM, ADMINS, EVERYONE, USERS = "S-1-5-21-1-2-3-1001", "S-1-5-18", "S-1-5-32-544", "S-1-1-0", "S-1-5-32-545"
FULL, READ, WRITE = 0x001F01FF, 0x00120089, 0x00000002


def win(aces, owner=ME):
    return lambda path: {"me": ME, "owner": owner, "aces": aces}


shared = K.credential_shared_with
check("A1 Windows: a file only this account, SYSTEM and Administrators can read is shared with nobody",
      shared("x", win([(ME, FULL, 0, 0), (SYSTEM, FULL, 0, 0), (ADMINS, FULL, 0, 0)])) == [])
check("A2 ...one that Everyone can read is shared with Everyone",
      shared("x", win([(ME, FULL, 0, 0), (EVERYONE, READ, 0, 0)])) == [EVERYONE])
check("A3 ...and the local Users group likewise", shared("x", win([(ME, FULL, 0, 0), (USERS, READ, 0, 0)])) == [USERS])
check("A4 an entry that is inherit-only does not apply to the file itself and is not counted",
      shared("x", win([(ME, FULL, 0, 0), (EVERYONE, READ, 0, 0x08)])) == [])
check("A5 a DENY entry gives nobody anything", shared("x", win([(ME, FULL, 0, 0), (EVERYONE, READ, 1, 0)])) == [])
check("A6 an entry that lets another account WRITE but not read is not a reader",
      shared("x", win([(ME, FULL, 0, 0), (USERS, WRITE, 0, 0)])) == [])
check("A7 a file with NO access control list is readable by everyone",
      "everyone" in shared("x", win(None))[0])
check("A6b an entry for OWNER RIGHTS (S-1-3-4) is the owner's own, not a stranger's -- and is "
      "one when the owner is",
      shared("x", win([("S-1-3-4", FULL, 0, 0), (SYSTEM, FULL, 0, 0)])) == []
      and len(shared("x", win([("S-1-3-4", FULL, 0, 0)], owner="S-1-5-21-9-9-9-1002"))) >= 1)
check("A8 a file OWNED by another account is that account's to open up",
      any("owner" in w for w in shared("x", win([(ME, FULL, 0, 0)], owner="S-1-5-21-9-9-9-1002"))))
for label, reader in (("an entry of a kind the check cannot read", win([(None, READ, 9, 0)])),
                      ("the ACL cannot be read at all", lambda p: (_ for _ in ()).throw(OSError(5, "denied"))),
                      ("the reader answers nonsense", lambda p: "fine")):
    try:
        shared("x", reader)
        got = "no error"
    except K.KeyScopeError as e:
        got = "refused"
    check("A9 when %s, the answer is an error, never 'nobody'" % label, got == "refused", got)
posix = lambda mode, uid=1000: (lambda path: {"mode": mode, "uid": uid, "me": 1000})
check("A10 POSIX: 0600 is shared with nobody; 0640 with its group; 0644 with everyone; another owner is named",
      shared("x", posix(0o100600)) == [] and shared("x", posix(0o100640)) == ["its group"]
      and shared("x", posix(0o100644)) == ["its group", "everyone"]
      and any("owner" in w for w in shared("x", posix(0o100600, uid=0))))

# the REAL reader, on a temp file this test creates
real = os.path.join(tempfile.mkdtemp(), "not_a_credential.txt")
open(real, "w").write("nothing secret\n")
if os.name == "nt":
    before = shared(real)
    check("A11 the REAL Windows reader: a new file in this account's temp folder is shared with "
          "nobody", before == [], before)
    g = subprocess.run(["icacls", real, "/grant", "*S-1-1-0:R"], capture_output=True, text=True)
    after = shared(real) if g.returncode == 0 else None
    check("A12 ...and once Everyone is granted read on it (icacls, on this temp file), the same "
          "reader names Everyone", after == [EVERYONE], (g.returncode, g.stdout[-120:], after))
else:
    os.chmod(real, 0o600)
    check("A11 the REAL POSIX reader: a 0600 file is shared with nobody", shared(real) == [], shared(real))
    os.chmod(real, 0o644)
    check("A12 ...and the same file at 0644 is shared with its group and everyone",
          shared(real) == ["its group", "everyone"], shared(real))

# ---- V: a venue refuses a shared credential ------------------------------------------------
cred = os.path.join(tempfile.mkdtemp(), "credentials")
open(cred, "w").write("key=TESTKEY\nsecret=dGVzdA==\n")          # made-up test values, a temp file
keep_reader = (K._windows_acl, K._posix_mode)
kv = V.KrakenVenue()
kv.CRED = cred
try:
    everyone = win([(ME, FULL, 0, 0), (EVERYONE, READ, 0, 0)]) if os.name == "nt" else posix(0o100644)
    K._windows_acl = K._posix_mode = everyone
    try:
        kv._creds()
        got = "read"
    except V.VenueError as e:
        got = str(e)
    check("V1 a venue asked for a credential Everyone can read REFUSES, as a VenueError, and "
          "does not read it", "readable by other accounts" in got and kv._key is None, got)
    K._windows_acl = K._posix_mode = (win([(ME, FULL, 0, 0), (SYSTEM, FULL, 0, 0)])
                                      if os.name == "nt" else posix(0o100600))
    check("V2 ...and reads the same file once it is this account's alone",
          kv._creds() == ("TESTKEY", "dGVzdA=="))
finally:
    K._windows_acl, K._posix_mode = keep_reader

# ---- P: key_scope.py makes no network call -----------------------------------------------
src = open(os.path.join(HERE, "key_scope.py"), encoding="utf-8").read()
code_lines = re.sub(r'"""[\s\S]*?"""', "", src).splitlines()          # docstrings out
net = [l for l in code_lines if not l.lstrip().startswith("#")
       and re.search(r"\b(urllib|urlopen|socket|http\.client|requests|_http|_private|_call)\b", l)]
check("P1 key_scope.py, outside its docstrings and comments, names no network or signing call",
      not net, net[:3])
check("P2 ...and imports only json and os at the top", re.findall(r"^import (\w+)", src, re.M) == ["json", "os"],
      re.findall(r"^import (\w+)", src, re.M))

n = len(results)
print(f"\nH9 venue keys: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
