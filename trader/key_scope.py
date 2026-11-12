#!/usr/bin/env python3
"""key_scope.py -- what an exchange key may do, and who else can read it.

H9, 2026-11-12. This module makes NO venue call and holds no signing code. It
JUDGES: given what a venue said about its key (venues.py fetches that, through
a function a test can replace), may a live order go? And given a credential
file, can any other account on this machine read it?

WHAT WAS MISSING. Nothing asked what an exchange key was allowed to do. The
README says "a trade-scoped exchange key ... with no withdrawal permission",
and that was a sentence: a key that could also withdraw or transfer worked
exactly the same, so the worst this program -- or whoever controls this
account -- could do with it was not bounded by anything the program checked.

Before any LIVE order the venue is now asked, and the order does not go if the
key can transfer or withdraw, cannot trade, or if the answer cannot be read:

  Coinbase   GET /api/v3/brokerage/key_permissions
             -> can_view, can_trade, can_transfer (booleans)
  Kraken     POST /0/private/GetApiKeyInfo
             -> result.permissions, a list of strings
  Robinhood  publishes no such endpoint (its Crypto Trading API lists accounts,
             market data, holdings and orders; read 2026-11-12). Its key's
             scope must be recorded BY HAND in KEY_SCOPE_FILE, a file this
             trader never writes; with no record, live Robinhood orders are
             refused.

WHY IT IS A FILE OF ITS OWN. test_f5_reserve.py W1 reads venues.py and
covenant_trader.py line by line for the vocabulary of money leaving -- the
permission names this module has to spell out in order to REFUSE them. Kept
here, W1 stays the plain scan it is: those two files still contain no such
word, and one appearing there is still a finding. test_h09_venue_keys.py pins
the other half: this file contains no network call.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_SCOPE_FILE = os.path.join(os.path.expanduser("~"), ".sentinel_witness", "key_scope.json")


class KeyScopeError(Exception):
    """A key's scope, or a credential file's readers, could not be established
    or are not acceptable. Always carries the why."""


# Kraken's permission strings (GetApiKeyInfo). KRAKEN_TRADE_ONLY is what a
# trade-only key needs, and nothing else: a string that is in none of the
# three sets refuses, so a permission Kraken adds next year is refused until
# somebody reads what it does.
KRAKEN_TRADE_ONLY = frozenset((
    "query-funds", "query-open-trades", "query-closed-trades", "modify-trades",
    "close-trades", "query-ledger", "export-data", "create-ws-token"))
KRAKEN_WITHDRAW = frozenset(("withdraw-funds", "add-withdraw-address", "update-withdraw-address"))
KRAKEN_TRANSFER = frozenset(("add-funds", "earn-funds"))


def from_kraken(result):
    """Kraken's GetApiKeyInfo `result` object -> the three answers."""
    perms = result.get("permissions") if isinstance(result, dict) else None
    if (not isinstance(perms, list) or not perms
            or any(not isinstance(x, str) or not x.strip() for x in perms)):
        raise KeyScopeError("kraken: GetApiKeyInfo returned no list of permissions")
    have = {x.strip() for x in perms}
    return {"can_trade": "modify-trades" in have,
            "can_withdraw": bool(have & KRAKEN_WITHDRAW),
            "can_transfer": bool(have & KRAKEN_TRANSFER),
            "also": sorted(have - KRAKEN_TRADE_ONLY - KRAKEN_WITHDRAW - KRAKEN_TRANSFER),
            "source": "kraken GetApiKeyInfo: " + ", ".join(sorted(have))}


def from_coinbase(js):
    """Advanced Trade's key_permissions object -> the three answers. Coinbase
    reports ONE can_transfer for deposit and withdrawal together, so a key
    that can transfer is treated as a key that can withdraw."""
    if not isinstance(js, dict) or any(not isinstance(js.get(k), bool)
                                       for k in ("can_trade", "can_transfer")):
        raise KeyScopeError("coinbase: key_permissions did not return can_trade and "
                            "can_transfer as true/false")
    return {"can_trade": js["can_trade"], "can_transfer": js["can_transfer"],
            "can_withdraw": js["can_transfer"], "also": [],
            "source": "coinbase key_permissions: can_trade=%s can_transfer=%s"
                      % (js["can_trade"], js["can_transfer"])}


def recorded(venue_name, path=None):
    """The operator's hand-written record of what `venue_name`'s key may do,
    for a venue that cannot be asked. KeyScopeError when there is none or it is
    not a complete record.

        {"robinhood": {"can_trade": true, "can_transfer": false,
                       "can_withdraw": false,
                       "by": "<who checked>", "at": "<when>",
                       "words": "<what they saw on the venue's key page>"}}

    Like the Rule 5 waiver it is a RECORD: three booleans and who, when and in
    what words, each a non-empty string. Anything less is no record. A record
    inside this trader's own folder is refused: the trader writes there."""
    p = path or KEY_SCOPE_FILE
    if os.path.realpath(p).startswith(os.path.realpath(HERE) + os.sep):
        raise KeyScopeError("the key-scope record %s is inside the trader's own folder" % p)
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise KeyScopeError("no hand-recorded key scope at %s (%s)" % (p, type(e).__name__)) from None
    row = data.get(venue_name) if isinstance(data, dict) else None
    if not isinstance(row, dict):
        raise KeyScopeError("%s holds no record for %s" % (p, venue_name))
    for k in ("can_trade", "can_transfer", "can_withdraw"):
        if not isinstance(row.get(k), bool):
            raise KeyScopeError("the %s record in %s has no true/false for %s" % (venue_name, p, k))
    for k in ("by", "at", "words"):
        if not isinstance(row.get(k), str) or not row[k].strip():
            raise KeyScopeError("the %s record in %s does not say %s" % (venue_name, p, k))
    return {"can_trade": row["can_trade"], "can_transfer": row["can_transfer"],
            "can_withdraw": row["can_withdraw"], "also": [],
            "source": "recorded by hand by %s on %s (%s)" % (row["by"], row["at"], p)}


def key_reasons(venue, fetch=None):
    """Why `venue`'s key may not place a live order. [] means it may.

    The key must be able to trade and unable to transfer or withdraw; an
    answer that cannot be had, or cannot be read as three booleans, refuses.
    `fetch` is handed to the venue's key_permissions(), which uses it in place
    of its own signed call."""
    name = getattr(venue, "name", "?")
    ask = getattr(venue, "key_permissions", None)
    if ask is None:
        return ["%s: this venue adapter cannot say what its key may do -- no live order" % name]
    try:
        p = ask(fetch) if fetch is not None else ask()
    except Exception as e:                                       # noqa: BLE001
        return ["%s: the key's permissions cannot be read (%s: %s) -- no live order"
                % (name, type(e).__name__, str(e)[:400])]
    if not isinstance(p, dict) or any(not isinstance(p.get(k), bool)
                                      for k in ("can_trade", "can_transfer", "can_withdraw")):
        return ["%s: the key's permissions cannot be read (not three true/false answers) "
                "-- no live order" % name]
    out = []
    if p["can_withdraw"]:
        out.append("%s: this key can WITHDRAW -- no live order; install a key that can only "
                   "trade (%s)" % (name, p.get("source", "")))
    if p["can_transfer"]:
        out.append("%s: this key can TRANSFER funds -- no live order; install a key that can "
                   "only trade (%s)" % (name, p.get("source", "")))
    if p.get("also"):
        out.append("%s: this key carries permissions outside a trade-only key's (%s) -- no "
                   "live order" % (name, ", ".join(str(x) for x in p["also"])))
    if not p["can_trade"]:
        out.append("%s: this key cannot trade (%s) -- no live order" % (name, p.get("source", "")))
    return out


# ---- who else can read the credential file -----------------------------------
# A key readable by another account on this machine is that account's key too.
# POSIX says so in the mode bits. Windows does not: os.stat reports 0o666 for
# every ordinary file whatever its ACL, so the mode is no evidence there and
# the file's DACL is read instead (advapi32, through ctypes -- no package).
_WIN_TRUSTED = {"S-1-5-18": "SYSTEM", "S-1-5-32-544": "Administrators"}
_WIN_READ = 0x00000001 | 0x80000000 | 0x10000000      # FILE_READ_DATA | GENERIC_READ | GENERIC_ALL
# OWNER RIGHTS. Not an account: an entry for it applies to whoever owns the
# file. Found by running the real reader on a temp file (test_h09 A11): a
# folder Python creates owner-only on Windows carries exactly this entry, and
# the first cut of this check reported it as a stranger.
_WIN_OWNER_RIGHTS = "S-1-3-4"


def _windows_acl(path):
    """{"me": sid, "owner": sid, "aces": None | [(sid, mask, type, flags)]} for
    `path`. aces None is a NULL DACL: no access control at all. OSError on any
    failure to read it."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PVOID = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                          ctypes.POINTER(PVOID), ctypes.POINTER(PVOID),
                                          ctypes.POINTER(PVOID), ctypes.POINTER(PVOID),
                                          ctypes.POINTER(PVOID)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.ConvertSidToStringSidW.argtypes = [PVOID, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    adv.GetAclInformation.argtypes = [PVOID, PVOID, wintypes.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = wintypes.BOOL
    adv.GetAce.argtypes = [PVOID, wintypes.DWORD, ctypes.POINTER(PVOID)]
    adv.GetAce.restype = wintypes.BOOL
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, PVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.GetTokenInformation.restype = wintypes.BOOL
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.LocalFree.argtypes = [PVOID]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    def sid_text(psid):
        out = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
        try:
            return out.value
        finally:
            k32.LocalFree(ctypes.cast(out, PVOID))

    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        need = wintypes.DWORD(0)
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))
        buf = ctypes.create_string_buffer(need.value or 256)
        if not adv.GetTokenInformation(token, 1, buf, len(buf), ctypes.byref(need)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        me = sid_text(ctypes.cast(buf, ctypes.POINTER(PVOID)).contents)
    finally:
        k32.CloseHandle(token)

    owner, dacl, sd = PVOID(), PVOID(), PVOID()
    rc = adv.GetNamedSecurityInfoW(path, 1, 0x1 | 0x4, ctypes.byref(owner), None,
                                   ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc != 0:
        raise OSError(rc, "GetNamedSecurityInfoW failed")
    try:
        got = {"me": me, "owner": sid_text(owner), "aces": None}
        if not dacl.value:
            return got

        class _Size(ctypes.Structure):
            _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                        ("AclBytesFree", wintypes.DWORD)]
        info = _Size()
        if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise OSError(ctypes.get_last_error(), "GetAclInformation failed")
        aces = []
        for i in range(info.AceCount):
            ace = PVOID()
            if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                raise OSError(ctypes.get_last_error(), "GetAce failed")
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            ace_flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            # ACCESS_ALLOWED_ACE (0) and ACCESS_DENIED_ACE (1) carry the SID at +8.
            sid = sid_text(PVOID(ace.value + 8)) if ace_type in (0, 1) else None
            aces.append((sid, mask, ace_type, ace_flags))
        got["aces"] = aces
        return got
    finally:
        k32.LocalFree(sd)


def _posix_mode(path):
    st = os.stat(path)
    return {"mode": st.st_mode, "uid": st.st_uid, "me": os.getuid()}


def credential_shared_with(path, reader=None):
    """Who, other than this account (and on Windows SYSTEM and the
    Administrators group), can read `path` -- as a list of names; [] is nobody.
    KeyScopeError when that cannot be told. `reader` is the function that reads
    the file's ACL (Windows) or mode (elsewhere); a test hands in a fake."""
    windows = os.name == "nt"
    try:
        info = (reader or (_windows_acl if windows else _posix_mode))(path)
    except Exception as e:                                       # noqa: BLE001
        raise KeyScopeError("who can read %s cannot be determined (%s: %s)"
                            % (path, type(e).__name__, str(e)[:120])) from None
    if not isinstance(info, dict):
        raise KeyScopeError("who can read %s cannot be determined" % path)
    if "mode" in info:                                           # POSIX
        out = []
        if info.get("uid") != info.get("me"):
            out.append("its owner (uid %s), which is not this account" % info.get("uid"))
        if info["mode"] & 0o040:
            out.append("its group")
        if info["mode"] & 0o004:
            out.append("everyone")
        return out
    me, owner, aces = info.get("me"), info.get("owner"), info.get("aces")
    trusted = dict(_WIN_TRUSTED)
    if me:
        trusted[me] = "this account"
    if aces is None:
        return ["everyone (the file has no access control list at all)"]
    out = []
    if owner not in trusted:
        out.append("its owner %s, which is not this account" % owner)
    for sid, mask, ace_type, flags in aces:
        if flags & 0x08:                    # INHERIT_ONLY: does not apply to this file
            continue
        if ace_type == 1:                   # a DENY entry gives nobody anything
            continue
        if ace_type != 0 or sid is None:
            raise KeyScopeError("%s carries an access entry of a kind this check cannot "
                                "read (type %s)" % (path, ace_type))
        if sid == _WIN_OWNER_RIGHTS:        # "whoever owns this file": judged as the owner is
            sid = owner
        if mask & _WIN_READ and sid not in trusted and sid not in out:
            out.append(sid)
    return out


def refuse_shared_credential(path, reader=None):
    """KeyScopeError if the credential at `path` is readable by another
    account. Called before a credential is read, by every venue."""
    who = credential_shared_with(path, reader)
    if who:
        raise KeyScopeError("credential %s is readable by other accounts (%s) -- refusing to "
                            "use it; restrict the file to your own account" % (path, "; ".join(who)))
