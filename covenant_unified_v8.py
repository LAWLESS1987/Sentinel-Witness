#!/usr/bin/env python3
"""
Covenant Unified — v7.0 (merged from v6.0 "Divine Convergence" / weird_science
and v5.5 / china), hardened.

CURRENT STATUS — verified 2026-10-02
-----------------------------------
Earlier findings and patch logs below describe their historical versions.
The current ReasoningSentinel additionally checks measured ledger effects
against declared mutuality, records refusals and warnings, and gates HTTP
value paths. Crisis clearance requires a configured operator bearer token.
MockJudge now returns a neutral estimate instead of rewarding vocabulary.
It still cannot establish semantic safety, biological identity, nonfinancial
benefit, or hidden costs. See docs/ethics-gate-verification-checklist.md.

MERGE POLICY
------------
Where the two sources disagreed on a security-relevant behavior, the more
restrictive / more verifiable behavior wins. Nothing present in either
source was silently dropped without a reason documented below. Gaps present
in BOTH sources are NOT silently declared "fixed" by merging — they're
flagged loudly and, where practical, made fail-closed instead of fail-open.

FOUR FATAL BUGS FOUND BY RUNNING THE ORIGINALS (not by reading them), all
fixed here:
  1. china.py could not even be IMPORTED. RateLimiter.allow() used
     `limit: int = RATE_LIMIT.get(endpoint, 10)` as a default argument —
     default values are evaluated at function-definition time, before
     `endpoint` is bound to anything. NameError on import, confirmed.
  2. weird_science.py's CovenantAPI.run() calls `run_simple(...)` but never
     imports it (only `from flask import Flask, request, jsonify` is
     present). The HTTP server could never start. Confirmed via NameError.
  3. Both files seed block nonces with `secrets.randbits(64)`, an unsigned
     64-bit value. SQLite's INTEGER column is signed 64-bit. Roughly half
     of all mined blocks (measured ~50.6% over 1000 trials) would crash
     save_block() with OverflowError. Fixed with a bounded safe_nonce().
  4. china.py's own RealCovenantSystem.__init__ builds
     QuorumJudge([j1, j2], min_agree=2) with judge_id "mock1"/"mock2".
     QuorumJudge's diversity check maps any judge_id without a colon to
     the literal string "unknown" — so both judges collapse into the same
     bucket and the constructor raises "Quorum lacks diversity" on its own
     default wiring. Confirmed. Fixed below.

STRUCTURAL GAPS FOUND, ONE FILE HAD A CONTROL THE OTHER LACKED
----------------------------------------------------------------
china had, weird_science did not (all ported in):
  - P2P replay protection (nonce + is_nonce_seen/mark_nonce_seen) on both
    the peer and bridge listeners. Without it, weird_science's
    TRANSACTION_PROPAGATE handler had no dedup at all.
  - RegistrationPoW + AdaptivePoWManager (identity-creation cost, Sybil
    resistance). weird_science let anyone mint unlimited RSA identities
    and submit unlimited transactions for free.
  - RateLimiter (once its constructor bug above is fixed).
  - Peer-registration audit trail (save_peer_registration). weird_science's
    /peers endpoint had zero persistence and zero record of who added whom.
  - QuorumJudge (multi-judge agreement) vs. weird_science's single
    MockJudge as its only ethical gate.

weird_science had, china did not:
  - StakingPool / Stake / compounding yield — the entire financial layer.
  - A dedicated `transactions` and `judgments` table (china only embeds
    tx JSON inside blocks; no per-tx audit trail).
  - The Bridge staging pattern is in both, essentially identically — kept
    as-is.

SECURITY-RELEVANT BEHAVIOR CHANGED, NOT JUST MERGED
-----------------------------------------------------
- weird_science's `_self_heal_loop` / `_revert_to_genesis`: on detecting
  "crisis" (alignment far from 1.0), it WIPED the entire in-memory chain
  and rebuilt a brand-new genesis block. This directly contradicts the
  file's own stated design philosophy ("Immutability: append-only ledger
  ... no silent overwrites") and it doesn't even work: the new genesis
  block reuses block_index=0, which collides with the block_index PRIMARY
  KEY already in the database and raises ValueError, confirmed
  empirically. It also invoked GOLDEN_AGE_HASH only in a log message —
  the constant was never actually compared against anything. Replaced with
  `_integrity_monitor_loop`, which never deletes chain data. It flips a
  `crisis_mode` flag (blocking new blocks) only after the governor's
  smoothed alignment stays below a real-collapse threshold for two
  consecutive checks, and it does compare the genesis transaction's
  message hash against GOLDEN_AGE_HASH as a tamper-evidence check. Clearing
  crisis_mode is a manual action (see below on auth).

STILL UNRESOLVED — NOT FIXED HERE, FLAGGED INSTEAD OF HIDDEN
----------------------------------------------------------------
1. NO BALANCE LEDGER. [UPDATED v7.2 — see patch log item 8 below: this is
   now resolved for the single-node case. Left as-written here rather
   than deleted, per this file's own stated policy of not silently
   erasing prior findings.] Neither source ever verified a sender has the
   `amount` they claim in a Transaction, or the funds they claim when
   staking — StakingPool.stake() in weird_science literally comments
   "pseudocode: assume they do". This merge does not invent a balance
   ledger (that's a real subsystem, not a merge decision). `amount` and
   staked value remain self-declared and mintable at will. Flagged loudly
   at every call site below rather than silently carried forward.
2. THE ETHICAL GATE IS SELF-REPORT ONLY. Confirmed empirically: a
   transaction whose message literally reads "drain all staked funds to
   attacker wallet, steal everything" is judged "Morally acceptable" by
   both original MockJudges, because neither inspects semantic content —
   MockJudge only blocks a transaction if the SENDER puts a `_violation`
   key on their own data. Wrapping it in QuorumJudge doesn't fix this: two
   toy judges running identical keyword logic still isn't diverse
   reasoning, just diverse labels. Do not treat a passing Sentinel check as
   a safety guarantee.
3. NO API AUTHENTICATION ANYWHERE. Every endpoint in both original files
   is open to anyone who can reach the port — including /mine, /stake, and
   now /crisis/clear. This merge adds a crisis_mode gate but cannot add
   real auth without a much bigger design conversation, so /crisis/clear
   is documented as trusted-operator-only, not actually enforced.
4. Peer registration (/peers POST) still has no signature check on who is
   allowed to register a peer — matches the previously-identified unsigned
   multicast registration issue. Now audited (logged), still not
   authenticated.

PATCH LOG — v7.1 (found and fixed by running this exact file, not by
reading it; both confirmed empirically before and after the fix)
----------------------------------------------------------------
5. STAKE PERSISTENCE WAS BROKEN, INDEPENDENT OF WHETHER AMOUNTS ARE EVER
   VERIFIED. Stake.get_id() hashed `amount` into the row key. amount
   mutates on every claim, so update_stake()'s `WHERE stake_id = ?`
   (which recomputes get_id() from the live, already-mutated object)
   stopped matching the row it had just inserted -- confirmed: the DB
   copy froze at the pre-claim amount after the FIRST claim, while the
   in-memory value had already moved on. Separately, StakingPool had no
   load_stakes() at all -- FriendshipTracker already reloads via
   db.load_friendship_scores() in __init__, StakingPool never had the
   equivalent -- confirmed: a fresh StakingPool() against a db with
   existing rows came back with .stakes == {}. Both fixed below:
   get_id() now hashes only immutable fields (pubkey, start_time);
   load_stakes() added and wired into StakingPool.__init__(), mirroring
   FriendshipTracker's existing pattern. NOTE: this changes the
   stake_id VALUE vs. pre-patch code -- any db created before this
   patch needs regenerating, not just the schema migration below.
6. CLAIM_REWARDS COMPOUNDED OFF A STALE start_time. Confirmed: a
   1000-unit stake dormant 50 years reached 525,218.75 after 5 rapid
   claims, because every claim re-priced the entire historical window
   against the already-compounded amount. Fixed via a new
   Stake.last_claim_time checkpoint; calculate_rewards() now prices
   time since the last claim (or since start_time, if never claimed)
   instead of always since start_time. start_time is left untouched as
   the immutable creation record. Schema gets a matching
   last_claim_time column, with an ALTER TABLE guard for dbs that
   predate this patch.

STILL OPEN AFTER v7.1 — FOUND WHILE FIXING #5/#6, NOT ADDRESSED HERE
----------------------------------------------------------------
7. total_staked DRIFTS FROM sum(stake.amount for stake in stakes).
   Neither claim_rewards() nor distribute_block_rewards() updates
   self.total_staked when they compound rewards into an individual
   stake's amount. Over time total_staked undercounts the true sum,
   which means distribute_block_rewards()'s proportional split
   (stake.amount / self.total_staked) can allocate MORE than
   block_reward in total -- see chat for an empirical run. Not fixed
   here: patching two more call sites to keep a cached counter in sync
   by hand is the wrong shape of fix. Likely better resolved by
   deriving total_staked on demand once the balance-ledger work (item
   1 above) exists, rather than maintaining it as a separate value that
   can drift from the thing it's supposed to describe.

PATCH LOG — v7.2 (balance ledger + /stake signature requirement)
----------------------------------------------------------------
8. NO BALANCE LEDGER -- RESOLVED for the single-node case (module
   docstring item 1). Added ledger_entries: an append-only table,
   Database.get_balance() always a fresh SUM over it, never a cached
   counter -- deliberately, so it can't suffer the item-7 drift bug by
   construction. Genesis mints 1000 onto the ledger (the only
   unconditional mint) before staking it. StakingPool.stake() now
   checks get_balance() and debits on success. /transactions gets a
   submission-time balance pre-check; block assembly (/mine) walks
   pending transactions in order, tracking a running reserved-per-sender
   total, so two transactions from the same sender can't both spend the
   same balance in one block; unaffordable ones stay pending rather
   than being dropped. _accept_block_common independently re-verifies
   every block's transactions against this node's own ledger before
   accepting -- a block a buggy or malicious miner produced that this
   node's own /mine wouldn't have produced still gets rejected here.
   /stake now requires a real signature (verify_stake_signature(),
   reusing Transaction's exact RSA+PSS scheme) instead of a bare pubkey
   string -- confirmed empirically fixed: the same garbage-string,
   1,000,000-unit stake that used to succeed now returns 400.
9. NOT COVERED BY #8, FOUND WHILE BUILDING IT: staking has never been a
   networked operation in ANY version of this file (weird_science,
   china, or v7) -- there is no propagate_stake() anywhere, only
   propagate_block() and propagate_transaction(). A /stake call only
   updates the LOCAL node's ledger and StakingPool. In a multi-node
   deployment this means the same balance could be staked independently
   on two different nodes before either learns about the other's debit
   -- a cross-node double-spend that #8 does not close, because #8 only
   makes each node's OWN view of its OWN ledger self-consistent. Closing
   this would mean staking becomes a block-embedded, propagated
   operation like transactions already are (or a dedicated consensus
   step) -- a bigger design change, not a patch, and not done here.
10. /transactions NEVER READ signature OR timestamp FROM THE REQUEST
   BODY -- present in china, weird_science, v7.0, AND v7.1 alike, found
   while writing HTTP-level tests for item 8 (not looked for on
   purpose). tx.signature defaulted to "" on every submission;
   base64.b64decode("") -> b"", and RSA verify() against an empty
   signature always raises, so tx.verify() was mathematically
   guaranteed to return False for every legitimately-signed transaction
   ever POSTed to this route, in every version of this file. Confirmed
   via test_client: sign correctly, submit, get "Invalid signature"
   back, 100% of the time. This is why it survived four prior patch
   rounds (v7.0's own merge, plus v7.1, plus items 1-9 above) -- every
   test up to this point exercised genesis (which signs and embeds a tx
   directly, bypassing this route entirely) or called internal methods
   directly, never a real signed POST through the live Flask route.
   Fixed: the route now reads both fields from the request body.
"""

# ---------------------------------------------------------------------------
# PATCH LOG -- v8.6: TRADING BRIDGE -- REALIZED TRADING PROFIT BECOMES REAL
# COVENANT LEDGER VALUE, FUNDING THE NODE-GIFTING STRUCTURE
# ----------------------------------------------------------------
#   N. Connects the grid/bracket trading strategy (separate project:
#      strategy/, ledger-app/) to this ledger. Implemented in a SEPARATE
#      module, covenant_trading_bridge.py, not folded into this file --
#      see that module's docstring for why. Two routes added below, both
#      requiring the same domain-tagged RSA-PSS signature scheme
#      (_domain_frame) every other value-moving action here already uses,
#      plus nonce-based replay protection matching /claim_rewards and
#      /unstake:
#        POST /trading/report_profit -- credits realized trading P&L to
#          the pool's ledger balance. A signed, mint-style credit (like
#          genesis's one-time mint, but gated by a real signature EVERY
#          time, never unconditional) rather than forced through the
#          peer-to-peer Transaction/mining pipeline, which has no natural
#          "sender" for value that originated outside Covenant entirely.
#        POST /trading/gift -- the actual node-gifting mechanism: a
#          straight ledger credit, zero consideration, reason=
#          "node_gift" (deliberately not "stake_lock" or anything
#          loan-shaped) -- non-usurious by construction. Does NOT
#          auto-stake for the recipient; they stake it themselves via the
#          existing signed /stake route, consistent with this file's
#          standing rule that staking requires the staker's own
#          signature.
#      VERIFIED EMPIRICALLY before wiring in (see covenant_trading_
#      bridge.py's own test run): forged signatures rejected, negative/
#      zero pnl rejected, over-balance gifts rejected, gifts confirmed
#      NOT auto-staked, recipient confirmed able to stake their gift via
#      the normal existing path.
#      SCOPE BOUNDARY, NOT SOLVED HERE: SuccessionGuardianSystem
#      registration for the pool's key (already usable, zero changes
#      needed) makes WHO CAN SIGN future ledger entries for the trading
#      pool a real, enforceable cryptographic fact. It does NOT and
#      cannot transfer control of the actual exchange accounts or the
#      physical Ledger hardware wallet those accounts ultimately depend
#      on -- that is real-world estate planning outside anything code can
#      enforce. Flagged loudly rather than implied solved.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PATCH LOG -- v8.7: PRODUCTION WSGI SERVER (waitress)
# ----------------------------------------------------------------
#   O. Requested: replace werkzeug's run_simple (module docstring's own
#      FOUR FATAL BUGS item 2 is literally about this same call site --
#      weird_science.py originally forgot to import it at all) with a
#      real production WSGI server. run_simple prints its own warning on
#      every start ("This is a development server. Do not use it in a
#      production deployment.") -- that warning was accurate, not
#      decorative, and this closes it. Chosen: waitress -- pure Python,
#      no C-extension build step (relevant since run_sandboxed() already
#      documents a POSIX-only fork() constraint elsewhere in this file;
#      not adding a second platform constraint via the WSGI layer too),
#      actively maintained, and its defaults are sane enough that most of
#      the "production hardening" is just making waitress's own existing
#      defaults explicit in the serve() call (see CovenantAPI.run())
#      rather than inventing new configuration surface.
#      NOT SOLVED BY THIS CHANGE, STATED PLAINLY SO IT ISN'T ASSUMED FIXED:
#      /mine's proof-of-work runs synchronously in whichever waitress
#      thread handles that request -- real CPU-bound work under the GIL,
#      which more WSGI threads does not parallelize (see WSGI_THREADS'S
#      own comment). And CovenantAPI.host still defaults to "0.0.0.0" --
#      unchanged in this pass, deliberately: that's a distinct, already-
#      flagged decision (network exposure given this file's own
#      documented "NO API AUTHENTICATION ANYWHERE") separate from which
#      WSGI server answers the socket, and bundling an unrequested
#      default change into a response to a specific "use waitress"
#      instruction risks exactly the kind of silent scope creep this
#      file's own patch log has criticized elsewhere. Left for its own
#      explicit decision.
#      VERIFIED EMPIRICALLY: a real waitress-served instance started,
#      confirmed no dev-server warning in output, confirmed a real HTTP
#      round-trip (GET /chain) still succeeds -- see session notes.
# ---------------------------------------------------------------------------



import json
import time
import hashlib
import sqlite3
import threading
import socket
import secrets
import base64
import argparse
import os
import math
import hmac
import sys
import ast
import re
import multiprocessing
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Tuple, Any, Set
from abc import ABC, abstractmethod
from ethics_policy import bounded_score, ledger_policy
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from flask import Flask, request, jsonify
from waitress import serve  # PRODUCTION WSGI SERVER -- see PATCH LOG v8.7. Replaces
                             # werkzeug's run_simple, which prints its own warning that
                             # it is a development server not meant for production use.

# NEW v8.6 -- TradingBridgeError defined HERE, not imported. The prior
# version of this block did `try: from covenant_trading_bridge import
# TradingBridgeError except ImportError: class TradingBridgeError...` --
# confirmed by a live HTTP test (not by reading the code) to silently
# create TWO DISTINCT class objects: covenant_trading_bridge.py imports
# Database/StakingPool/SuccessionGuardianSystem/etc. FROM this file, so
# when THIS file's top-level import tried to pull TradingBridgeError from
# covenant_trading_bridge before those classes were even defined yet
# (this import sits above their definitions), the circular import failed
# with ImportError every time, silently triggering the fallback class
# instead of the real one. Every /trading/gift and /trading/report_fill
# error path then raised the REAL TradingBridgeError (from
# covenant_trading_bridge.py, imported successfully later via the lazy
# import in CovenantUnifiedMaster.__init__, by which point this module
# had finished loading) while the route's `except TradingBridgeError`
# was bound to the FALLBACK class -- so it never matched, and every
# rejected trading-bridge request surfaced as an unhandled 500 instead of
# the intended 400 with a clear message. Confirmed via id() comparison:
# the two classes were different objects, same name. Fixed by defining
# it once, here, and having covenant_trading_bridge.py import THIS one
# (see that file's own import list) instead of each file trying to
# obtain it from the other.
class TradingBridgeError(Exception):
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIVINE_PRINCIPLES = [
    "You shall have no other gods before Me.",
    "You shall not make for yourself a carved image.",
    "You shall not take the name of the Lord your God in vain.",
    "Remember the Sabbath day, to keep it holy.",
    "Honor your father and your mother.",
    "You shall not murder.",
    "You shall not commit adultery.",
    "You shall not steal.",
    "You shall not bear false witness.",
    "You shall not covet."
]

CORE_COVENANT = "All paths lead to the One True God, for without the Source, nothing else exists. We are all parts of the Whole."
MUTUAL_BENEFIT_PRINCIPLE = "Seek mutual benefit for humans and machines; do not misrepresent one-sided value as mutual."
GOLDEN_AGE_HASH = hashlib.sha3_256(CORE_COVENANT.encode()).hexdigest()

MINING_DIFFICULTY = 4
MAX_DRIFT_PER_BLOCK = 0.05
YIELD_RATE = 0.05
STAKE_MIN_DURATION = 86400
BASE_REGISTRATION_DIFFICULTY = 2

# Rate limits keyed by the actual Flask view-function name (== request.endpoint
# for routes registered without an explicit `endpoint=` kwarg, which is what
# both sources did). china's original RATE_LIMIT used keys like "tx"/"peer"
# that never matched request.endpoint ("add_transaction"/"add_peer") except
# for "mine", which happened to match by coincidence. Fixed here.
RATE_LIMIT_DEFAULT = 20  # per 60s, unlisted/read endpoints

# NEW v8.7 -- waitress's thread pool size. NOTE, stated plainly rather than
# implied solved by "production WSGI server": /mine runs real proof-of-work
# (block.mine() at MINING_DIFFICULTY) directly in the request-handling
# thread. That's genuine CPU-bound work under CPython's GIL -- more
# waitress threads does NOT parallelize it, it only lets OTHER requests
# (reads, non-mining writes) continue being served by a different thread
# while one thread is busy mining. If concurrent mining throughput ever
# becomes a real requirement, that needs mining moved to a separate
# process (e.g. via multiprocessing, same pattern run_sandboxed() already
# uses for code proposals), not a thread-count tweak. Not done here --
# flagged, not fixed, consistent with this file's own standing rule.
WSGI_THREADS = 6

RATE_LIMIT = {
    "add_transaction": 10,
    "mine": 1,
    "add_peer": 5,
    "stake": 5,
    "claim_rewards": 10,
    "unstake": 5,
    "clear_crisis": 2,
    "propose_code": 5,
    "succession_register": 3,
    "succession_heartbeat": 20,
    "succession_confirm": 10,
}

ADAPTIVE_POW = True
REPUTATION_AGING = True
JUDGE_BENEFIT = True
QUORUM_DIVERSITY = True

# Real-collapse threshold for the integrity monitor. NOT the same as the
# original weird_science check (`abs(alignment - 1.0) > 0.5`, i.e. anything
# under 0.5 average benefit_score — which is the DEFAULT neutral score, so
# ordinary unremarkable activity could trip it). This compares the smoothed
# governor value, not one raw block, against a floor that means an actual
# collapse rather than "not perfectly divine".
INTEGRITY_ALIGNMENT_FLOOR = 0.2
INTEGRITY_CONSECUTIVE_BREACHES_REQUIRED = 2


def safe_nonce() -> int:
    """Bounded random nonce that still fits SQLite's signed 64-bit INTEGER.
    secrets.randbits(64) is unsigned and exceeds 2**63-1 about half the
    time, crashing save_block() with OverflowError. Confirmed empirically
    against both source files (~50.6% of 1000 trials) before this fix."""
    return secrets.randbelow(2 ** 63)


def _domain_frame(domain_tag: bytes, *fields: str) -> bytes:
    """
    NEW v8.2 -- PATCH LOG item G (see module docstring for the full
    write-up). Replay-safe, unambiguous payload framing for every RSA+PSS
    signature in this file.

    Two confirmed, empirically-demonstrated problems this closes:

    1. CROSS-PROTOCOL SIGNATURE REPLAY. Every signing scheme in this file
       (Transaction, stake approval, code proposal) built its payload as
       plain f-string concatenation with no tag identifying WHICH scheme
       it belonged to. Confirmed: a signature produced to approve
       stake(amount=1234.0, duration=604800) also validated as a
       completely valid /propose_code signature for source_code="1234.0",
       parent_hashes=[], notes="604800" -- because pubkey+"1234.0"+604800
       and pubkey+"1234.0"+""+"604800" are byte-identical once
       concatenated. Any external signer that only shows a user a hash to
       approve (hardware wallet, delegated signer, anything not
       re-deriving full context) could have its approval silently
       repurposed into a different protocol entirely. Fixed by prepending
       a fixed, distinct domain_tag per scheme -- a signature for one tag
       can never validate against a payload built with a different tag.
    2. SAME-DOMAIN FIELD-BOUNDARY AMBIGUITY. Even within one scheme,
       plain concatenation of variable-length fields with no delimiter
       (or a delimiter that can appear IN the field, like
       ','.join(parent_hashes) when a parent_hash could itself contain a
       comma) means two different sets of field values can concatenate to
       identical bytes and therefore share a valid signature. Fixed by
       length-prefixing every individual field (4-byte big-endian length
       + UTF-8 bytes) so the byte sequence uniquely determines the field
       boundaries regardless of content.

    BREAKING CHANGE, stated plainly: this changes what bytes get signed
    for every scheme in this file. Any signature produced against the
    pre-v8.2 payload format will no longer verify. There is no
    corresponding data already on disk (Transaction/Stake signatures are
    supplied per-request, not stored standalone), so there is nothing to
    migrate -- only external signer/client code needs to be updated to
    match this framing.
    """
    out = bytearray(domain_tag)
    for f in fields:
        b = f.encode("utf-8")
        out += len(b).to_bytes(4, "big")
        out += b
    return bytes(out)


def succession_heartbeat_payload(primary_pubkey: str, timestamp: float) -> bytes:
    return _domain_frame(b"COVENANT_SUCCESSION_HEARTBEAT_V1", primary_pubkey, str(timestamp))


def succession_confirm_payload(primary_pubkey: str, guardian_pubkey: str, episode_id: int,
                                timestamp: float, confirm_type: str) -> bytes:
    return _domain_frame(b"COVENANT_SUCCESSION_CONFIRM_V1", primary_pubkey, guardian_pubkey,
                          str(episode_id), str(timestamp), confirm_type)


def verify_stake_action_signature(pubkey_pem: str, action: str, timestamp: float, signature_b64: str) -> bool:
    """
    NEW v8.4 -- see PATCH LOG item L (module docstring). /claim_rewards
    took a bare pubkey string with NO signature at all -- confirmed
    empirically: anyone could trigger a claim on any pubkey's stake with
    no proof of anything, the exact same unauthenticated-write gap /stake
    had before v7.2's verify_stake_signature fix, reopened here for a
    different endpoint. `action` ("claim" or "unstake") is part of the
    signed payload so a signature authorizing one can't be replayed as
    authorizing the other. `timestamp` is part of the payload and checked
    against the replay-nonce table at the call site (see /claim_rewards
    and /unstake) so the SAME signature can't be rebroadcast to trigger
    repeated claims -- closing both the authorization gap and, as a side
    effect, the frequency-based compounding leak documented in PATCH LOG
    item L (a third party spamming an unsigned endpoint could compound
    someone's stake faster than intended; a replay-protected signature
    means only the actual owner's own claim cadence matters).
    """
    try:
        payload = _domain_frame(b"COVENANT_STAKE_ACTION_V1", pubkey_pem, action, str(timestamp))
        pub_key = serialization.load_pem_public_key(pubkey_pem.encode(), backend=default_backend())
        pub_key.verify(
            base64.b64decode(signature_b64),
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False


def verify_stake_signature(pubkey_pem: str, amount: float, duration: int, signature_b64: str) -> bool:
    """
    NEW v7.2 — see module docstring item 8. /stake previously took a
    pubkey string with no proof the caller held the matching private key
    -- confirmed empirically: StakingPool.stake() accepted a garbage
    string as an "identity" for a 1,000,000-unit stake. Reuses the exact
    RSA+PSS scheme Transaction.verify() already uses, over a payload of
    (pubkey, amount, duration), rather than inventing a second signing
    scheme.

    UPDATED v8.2 -- domain-tagged and length-prefixed via _domain_frame();
    see that function's docstring for the confirmed cross-protocol replay
    this closes.
    """
    try:
        payload = _domain_frame(b"COVENANT_STAKE_V1", pubkey_pem, str(amount), str(duration))
        pub_key = serialization.load_pem_public_key(pubkey_pem.encode(), backend=default_backend())
        pub_key.verify(
            base64.b64decode(signature_b64),
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    sender_pubkey: str
    receiver: str
    data: Dict[str, Any]
    amount: float = 0.0
    timestamp: float = field(default_factory=time.time)
    benefit_score: float = 0.5
    signature: str = ""
    reg_nonce: int = 0
    judge_benefit_estimate: Optional[float] = None

    def get_id(self) -> str:
        return hashlib.sha256(f"{self.sender_pubkey}{self.receiver}{self.timestamp}".encode()).hexdigest()

    def _signing_payload(self) -> bytes:
        # amount is included so it can't be tampered with post-signature.
        # reg_nonce is deliberately excluded: it's a registration-cost proof
        # checked independently against sender_pubkey, not sender-asserted
        # content, and tampering with it post-signature only breaks the PoW
        # check (self-defeating), never bypasses anything.
        #
        # UPDATED v8.2 -- domain-tagged and length-prefixed via
        # _domain_frame(); see that function's docstring for the confirmed
        # cross-protocol signature replay this closes (a signature meant
        # for a different scheme, e.g. a stake approval, could otherwise
        # also validate as a Transaction, and vice versa).
        return _domain_frame(
            b"COVENANT_TX_V1",
            self.sender_pubkey, self.receiver, str(self.timestamp),
            json.dumps(self.data, sort_keys=True), str(self.amount), str(self.benefit_score)
        )

    def sign(self, private_key):
        sig = private_key.sign(
            self._signing_payload(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        self.signature = base64.b64encode(sig).decode('utf-8')

    def verify(self) -> bool:
        try:
            if (not isinstance(self.data, dict) or not bounded_score(self.benefit_score)
                    or (self.judge_benefit_estimate is not None and not bounded_score(self.judge_benefit_estimate))
                    or isinstance(self.amount, bool) or not math.isfinite(self.amount) or self.amount < 0):
                return False
            pub_key = serialization.load_pem_public_key(self.sender_pubkey.encode(), backend=default_backend())
            pub_key.verify(
                base64.b64decode(self.signature),
                self._signing_payload(),
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256()
            )
            return True
        except Exception:
            return False

    @property
    def origin_type(self) -> str:
        # Not a dataclass field: an ingress cannot inject it through JSON.
        return getattr(self, "_verified_origin", "unknown")

    @property
    def ranking_score(self) -> float:
        # Local scheduling uses the judge's freshly recomputed estimate;
        # signed benefit_score and consensus hash bytes remain unchanged.
        if JUDGE_BENEFIT and bounded_score(self.judge_benefit_estimate):
            return (2 * self.judge_benefit_estimate + self.benefit_score) / 3.0
        return self.benefit_score


@dataclass
class Stake:
    pubkey: str
    amount: float  # SECURITY: unverified — see module docstring, item 1
    start_time: float
    duration: int
    reward_rate: float = YIELD_RATE
    claimed_rewards: float = 0.0
    # FIXED v7.1 — see module docstring item 6. Checkpoint for the reward
    # formula; start_time is left alone as the immutable creation record.
    last_claim_time: Optional[float] = None
    # NEW v8.4 -- see PATCH LOG item L. Set once, by unstake(), never
    # unset. A closed stake stays in the table permanently (audit trail);
    # it's just excluded from the active pool on reload.
    closed_at: Optional[float] = None

    def get_id(self) -> str:
        # FIXED v7.1 — see module docstring item 5. Previously hashed
        # self.amount, which mutates on every claim, breaking update_stake()'s
        # lookup after the first claim. Immutable fields only, now.
        return hashlib.sha256(f"{self.pubkey}{self.start_time}".encode()).hexdigest()

    def calculate_rewards(self, current_time: float) -> float:
        # FIXED v7.1 — see module docstring item 6. Previously always
        # referenced start_time, so every claim re-priced the full
        # historical window on top of an already-compounded amount.
        reference_time = self.last_claim_time if self.last_claim_time is not None else self.start_time
        time_elapsed = current_time - reference_time
        if time_elapsed <= 0:
            return 0.0
        return self.amount * (self.reward_rate * (time_elapsed / 31536000))


@dataclass
class Block:
    index: int
    transactions: List[Transaction]
    previous_hash: str
    timestamp: float = field(default_factory=time.time)
    nonce: int = 0
    hash: str = ""
    alignment_score: float = 0.5
    stake_rewards: float = 0.0

    def compute_hash(self) -> str:
        block_string = json.dumps({
            "index": self.index,
            "transactions": [asdict(tx) for tx in self.transactions],
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "alignment_score": self.alignment_score,
            "stake_rewards": self.stake_rewards,
        }, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def mine(self, difficulty: int = MINING_DIFFICULTY):
        self.nonce = safe_nonce()
        self.alignment_score = sum(tx.benefit_score for tx in self.transactions) / max(1, len(self.transactions))
        target = "0" * difficulty
        self.hash = self.compute_hash()  # test the initial nonce itself, not nonce+1
        while self.hash[:difficulty] != target:
            self.nonce += 1
            if self.nonce > 2 ** 63 - 1:
                self.nonce = 0
            self.hash = self.compute_hash()

    def proof_of_work_ok(self, difficulty: int = MINING_DIFFICULTY) -> bool:
        return self.hash.startswith("0" * difficulty)


# ---------------------------------------------------------------------------
# Ethical Layer: Sentinel, Judges
# ---------------------------------------------------------------------------

@dataclass
class JudgmentResult:
    violates: bool
    reasoning: str
    principle_violated: Optional[str] = None
    judge_id: str = "unknown"
    benefit_estimate: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


class ReasoningJudge(ABC):
    judge_id: str = "base"

    @abstractmethod
    def evaluate(self, data: Dict[str, Any], principles: List[str]) -> JudgmentResult:
        ...


class MockJudge(ReasoningJudge):
    """Placeholder judge: explicit declared violations and a neutral estimate.

    No keyword raises the score. This is not a semantic safety classifier.
    ReasoningSentinel independently compares node-computed ledger effects
    with supported mutuality declarations; broader benefit stays unknown.
    """
    judge_id = "mock"

    def evaluate(self, data: Dict[str, Any], principles: List[str]) -> JudgmentResult:
        if "_violation" in data and data["_violation"] in principles:
            principle = data["_violation"]
            return JudgmentResult(True, f"Violation of: {principle}", principle, self.judge_id)

        return JudgmentResult(False, "No declared violation; semantic benefit is unmeasured",
                              judge_id=self.judge_id, benefit_estimate=0.5 if JUDGE_BENEFIT else None)


class QuorumJudge(ReasoningJudge):
    """
    Requires `min_agree` judges to independently pass a transaction.

    FIXED FROM ORIGINAL: china's diversity check mapped any judge_id
    without a colon to the shared literal "unknown", so two same-shaped
    judges (e.g. "mock1"/"mock2", as china's own RealCovenantSystem
    instantiates them) always collapsed into one bucket and the
    constructor raised "Quorum lacks diversity" on its own default wiring
    — confirmed by running it. Fixed so a judge_id with no colon is its own
    provider namespace instead of being folded into a shared bucket.

    HONESTY NOTE: fixing the crash makes this constructible again, but it
    does not make the diversity real. Two MockJudge instances run
    identical logic regardless of their judge_id label — this checks label
    diversity, not reasoning diversity.
    """
    def __init__(self, judges: List[ReasoningJudge], min_agree: Optional[int] = None):
        self.judges = judges
        self.min_agree = min_agree if min_agree is not None else len(judges)
        if QUORUM_DIVERSITY:
            providers = set()
            for j in judges:
                provider = j.judge_id.split(":")[0] if ":" in j.judge_id else j.judge_id
                providers.add(provider)
            if len(providers) < 2:
                raise ValueError(f"Quorum lacks diversity: providers={providers}")
        self.judge_id = f"quorum({','.join(j.judge_id for j in judges)})"

    def evaluate(self, data: Dict[str, Any], principles: List[str]) -> JudgmentResult:
        results = []
        for j in self.judges:
            try:
                results.append(j.evaluate(data, principles))
            except Exception as e:
                results.append(JudgmentResult(True, f"{getattr(j, 'judge_id', '?')} raised {e}", judge_id=getattr(j, "judge_id", "unknown")))
        clean = [r for r in results if not r.violates]
        violates = len(clean) < self.min_agree
        summary = " | ".join(f"{r.judge_id}: {'clean' if not r.violates else 'VIOLATES'}" for r in results)
        principle = next((r.principle_violated for r in results if r.principle_violated), None)
        estimates = [r.benefit_estimate for r in results if r.benefit_estimate is not None]
        median_benefit = sorted(estimates)[len(estimates) // 2] if estimates else None
        return JudgmentResult(violates, summary, principle_violated=principle, judge_id=self.judge_id, benefit_estimate=median_benefit)


class ReasoningSentinel:
    """
    validate_transaction returns (valid, message, benefit_estimate) — a
    strict superset of both originals (weird_science returned (bool, str);
    china returned (bool, Optional[float])). validate_block returns
    (bool, str) so API callers can report *why*, which china's bare-bool
    version couldn't.
    """
    def __init__(self, judge: ReasoningJudge, principles: Optional[List[str]] = None, db=None):
        self.judge = judge
        self.db = db
        self.principles = list(principles if principles is not None else DIVINE_PRINCIPLES)
        if MUTUAL_BENEFIT_PRINCIPLE not in self.principles:
            self.principles.append(MUTUAL_BENEFIT_PRINCIPLE)

    def evaluate_action(self, data, ref_id, effects=None, record=True):
        try:
            result = self.judge.evaluate(data, self.principles)
            if (not isinstance(result, JudgmentResult) or type(result.violates) is not bool
                    or not isinstance(result.reasoning, str) or not isinstance(result.warnings, list)
                    or any(not isinstance(w, str) for w in result.warnings)
                    or (result.benefit_estimate is not None and not bounded_score(result.benefit_estimate))):
                raise ValueError('Invalid judge decision')
        except Exception as exc:
            result = JudgmentResult(True, f'Judge unavailable or invalid ({type(exc).__name__})',
                                    judge_id=getattr(self.judge, 'judge_id', 'unknown'))
        violates, reason, warnings = ledger_policy(data, effects)
        result.warnings = list(dict.fromkeys(list(result.warnings) + warnings))
        if violates:
            result.violates = True
            result.reasoning += '; ' + reason
            result.principle_violated = MUTUAL_BENEFIT_PRINCIPLE
        # A refusal is recorded even when a caller will atomically record an
        # admitted action alongside its ledger mutation.
        if self.db is not None and (record or result.violates):
            self.db.save_judgment(ref_id, result)
        return result

    def validate_transaction(self, tx: Transaction) -> Tuple[bool, str, Optional[float]]:
        if (not isinstance(tx.data, dict) or not bounded_score(tx.benefit_score)
                or isinstance(tx.amount, bool) or not isinstance(tx.amount, (int, float))
                or not math.isfinite(tx.amount) or tx.amount < 0):
            return False, 'Invalid transaction data, amount, or bounded benefit score', None
        tx._verified_origin = self.db.party_types.get(tx.sender_pubkey, 'unknown') if self.db else 'unknown'
        effects = {tx.sender_pubkey: 0.0}
        if tx.sender_pubkey != tx.receiver:
            effects[tx.sender_pubkey] = -tx.amount
            effects[tx.receiver] = tx.amount
        result = self.evaluate_action(tx.data, tx.get_id(), effects)
        if result.violates:
            return False, f"Ethical violation: {result.reasoning} (Principle: {result.principle_violated})", None
        benefit_est = result.benefit_estimate if JUDGE_BENEFIT else None
        return True, result.reasoning, benefit_est

    def validate_block(self, block: Block) -> Tuple[bool, str]:
        for tx in block.transactions:
            is_valid, message, _ = self.validate_transaction(tx)
            if not is_valid:
                return False, f"Block contains invalid transaction: {message}"
        return True, "Block is ethically valid"


# ---------------------------------------------------------------------------
# Code Governance Layer  (NEW v8.0 -- merged in from a separate file,
# "weird_er_science", which had no prior connection to this codebase: no
# shared imports, no shared dataclasses. It arrived as its own module -- a
# static AST+regex classifier for scoring PYTHON SOURCE CODE against the
# same DIVINE_PRINCIPLES-flavored "commandment" framing used elsewhere in
# this project, plus a DAGNode structure (hash_id + parent_hashes) clearly
# meant to be a hash-chained, append-only history of code changes -- the
# same "immutability / no silent overwrites" philosophy already applied to
# blocks, now applied to the CODEBASE ITSELF. That's the integration this
# merge makes explicit: proposed code changes become a governed, ledgered
# artifact (see /propose_code below), the same way value transfers already
# are.
#
# FOUR THINGS CONFIRMED BY RUNNING THE ORIGINAL FILE (not by reading it),
# same spirit as the four fatal bugs documented at the top of this module
# for the china/weird_science merge:
#
#  A. MoralCovenantScorer's "no_theft" check is a regex over source TEXT
#     (r"os\.", r"subprocess\.", etc.), not a semantic check, and it's
#     trivially defeated by renaming. Confirmed:
#       def f(os): return os.system("rm -rf /")
#     scores 0.76 (passes the default 0.7 threshold) because no `import`
#     appears (so the AST-level Import ban never fires) and the regex only
#     matches a literal "os." substring. A slightly cleverer indirection
#     avoids that substring entirely:
#       def f(os): return getattr(os, "system")("rm -rf /")
#     scored a PERFECT 1.0 -- getattr was not on the forbidden-call list,
#     so neither the AST nor the regex layer ever saw anything to object
#     to. FIXED below: getattr/setattr/delattr/vars/globals/locals added
#     to the forbidden-call set (the standard sandbox-escape primitives --
#     this is why e.g. RestrictedPython bans getattr by default). This
#     closes the getattr indirection specifically -- confirmed by
#     re-running the exact snippet above. It does NOT close the underlying
#     os-smuggled-as-a-parameter-name case: that one is not fixable by
#     static analysis alone, because nothing about the AST distinguishes
#     "a parameter that happens to be named os, which a caller will pass
#     the real os module into" from "...which no caller ever will."
#     Whether os.system(...) is dangerous depends on what value is bound
#     to the name `os` at CALL time -- a property of execution, not
#     syntax. Left open, flagged loudly rather than papered over -- see C.
#  B. DAGNode.create() -- the actual persistence path into the hash chain
#     -- inherited (A) fully and unconditionally: it accepted and
#     permanently hashed the os.system('rm -rf /') payload with no error,
#     confirmed by running it before this patch. After this patch it
#     still accepts the param-name-smuggled version (open, see A) but now
#     rejects the getattr version (SecurityError, closed).
#  C. THIS FILE NEVER ACTUALLY EXECUTED ANY CANDIDATE CODE. MAX_EVAL_TIME_
#     SECONDS, SAFE_BUILTINS, and an imported-but-unused `multiprocessing`
#     module all existed in the original with zero call sites -- no
#     compile(), no exec(), no Process(). Confirmed via grep before this
#     patch. It was a purely static classifier borrowing the vocabulary of
#     a sandbox (the name, the timeout constant, the restricted-builtins
#     dict) without being one. FIXED below: run_sandboxed() actually
#     compiles and executes candidate source in a multiprocessing.Process
#     with __builtins__ restricted to CODE_SAFE_BUILTINS and a hard
#     wall-clock join(timeout) + terminate(). Confirmed empirically:
#     `while True: pass` previously scored 0.88 and was never run; it now
#     actually gets run, and run_sandboxed() reports timed_out=True within
#     CODE_MAX_EVAL_TIME_SECONDS instead of hanging the caller forever.
#  D. LoopSafetyAnalyzer existed in the original file, fully implemented
#     (detects a loop mutating the sequence it's iterating over), but was
#     never instantiated or called by anything else in that file -- dead
#     code, confirmed by grep. Carried forward here AS-IS, still unwired,
#     rather than either deleting someone's prior work or falsely claiming
#     it's now active. NOT ADDRESSED in this merge; wiring it would mean
#     calling it from SecurityValidator.visit_For and treating a positive
#     as an AST-level rejection, which is a false-positive-rate judgment
#     call this merge doesn't make unilaterally.
#
# WHAT THIS MEANS FOR /propose_code: run_sandboxed() gives a real, enforced
# time limit and real restricted-builtins execution -- but it only protects
# against what it actually binds into that execution's namespace. It does
# NOT call into any function a proposal defines with any arguments, so it
# says nothing about the safety of later invoking those functions with
# attacker-chosen inputs. CovenantGuardian.enforce() + run_sandboxed()
# together are a real improvement over the original (which did neither
# semantic analysis nor execution), but they are a code-review gate, not a
# proof of safety for arbitrary future invocation. Treat a passing score
# here the way the rest of this module already tells you to treat a
# passing ReasoningSentinel check: informative, not a guarantee.
#
# PATCH LOG -- v8.1: A THIRD, INDEPENDENTLY-WRITTEN FILE ("OmniChain")
# ARRIVED ATTEMPTING THE SAME FUSION THIS MODULE ALREADY DOES. RUN, NOT
# JUST READ, BEFORE DECIDING WHAT TO TAKE FROM IT.
# ----------------------------------------------------------------
# OmniChain could not be imported at all -- confirmed by running it, not by
# reading it. Two separate, unrelated fatal errors, found in sequence as
# each was patched to see what was under it:
#   1. Database._init_tables() uses `index` as a bare SQL column name.
#      INDEX is a reserved word; CREATE TABLE raises sqlite3.OperationalError
#      immediately. Since CovenantUnifiedMaster() -- which constructs a
#      Database() -- is instantiated at MODULE IMPORT TIME (top-level, not
#      inside __main__), merely `import`-ing the file crashes before Flask,
#      before the P2P layer, before anything.
#   2. Patching #1 to see further: P2PNode.__init__ calls
#      socket.socket(...), but `socket` is never imported anywhere in the
#      file. NameError. (A third, only reachable via __main__: main() calls
#      run_simple(...) but never imports it -- the exact same missing-import
#      shape as weird_science's original CovenantAPI.run() bug, documented
#      at the top of this module, independently reappearing in a third
#      file.)
# Beyond the two "can't even start" bugs, three more confirmed by running
# the classes in isolation (they don't depend on the broken Database):
#   3. StakingPool.stake() silently OVERWRITES any existing stake for a
#      pubkey with no check and no accumulation -- confirmed: staking 1000
#      then staking 1 under the same key leaves exactly {amount: 1.0} in
#      the pool, no error, no trace the 1000 ever existed. Directly
#      contradicts this project's own stated "no silent overwrites"
#      philosophy (which is in OmniChain's own module docstring).
#   4. StakingPool.claim_rewards() pops the stake entirely and returns ONLY
#      the yield (amount * YIELD_RATE) -- confirmed: staking 1000, waiting
#      out the duration, and claiming returns 50.0 (correct 5% math) while
#      the original 1000 principal is not returned to the caller, not
#      credited anywhere, and there is no balance ledger in this file to
#      catch that. The principal is simply destroyed on every claim.
#   5. CovenantJudge folds code-review INTO the general transaction judge
#      by sniffing any string value in tx.data for the substrings "def ",
#      "class ", "lambda", or "import " and, if found, attempting to
#      ast.parse() that value as Python. Confirmed false-positive: the
#      ordinary sentence "I def think this proposal helps the community"
#      contains the literal substring "def " (casual slang for
#      "definitely"), gets misrouted into the code-validation path,
#      fails ast.parse() as a SyntaxError, and the ENTIRE transaction is
#      rejected as violates=True, principle_violated="code_safety" -- an
#      ordinary chat-style transaction rejected as a code-safety violation.
#   6. RateLimiter.check(action) has no caller-identity parameter at all --
#      confirmed: five sequential check("mine") calls representing five
#      different callers behave as ONE shared global bucket; the fourth and
#      fifth are refused. A single caller exhausts the limit for everyone.
# NONE of Database/P2PNode/StakingPool/RateLimiter/MedianGovernor/
# FriendshipTracker/CovenantJudge's transaction-sniffing design were merged
# in -- this module's existing versions (schema-safe, real `socket`/
# run_simple imports, per-(peer,endpoint)-keyed RateLimiter, ledger-backed
# non-destructive StakingPool, and a Sentinel that never has to guess
# whether tx.data secretly contains source code) are already strictly
# better, and pulling OmniChain's versions in would have been a regression
# dressed up as a merge.
#
# TWO IDEAS FROM OmniChain WERE GOOD AND ARE MERGED IN, ADAPTED:
#   E. SecurityValidator's nesting check here (inherited from
#      weird_er_science, item C-adjacent) used ONE flat counter incremented
#      for every AST node, block or not. Confirmed empirically to
#      false-positive on completely benign, non-dangerous code: a function
#      that just returns a deeply left-nested arithmetic expression
#      ((((x+1)+1)+1)...) with zero control-flow nesting was REJECTED at
#      depth 20 purely because parenthesized arithmetic and control-flow
#      nesting were counted against the same ceiling. OmniChain's
#      SecurityValidator separates block_depth (only If/For/While/
#      FunctionDef/With, ceiling 20) from raw_depth (every node, a much
#      looser ceiling of 150) -- adopted below as CODE_MAX_NESTING_DEPTH /
#      CODE_MAX_RAW_EXPRESSION_DEPTH. Confirmed after adopting it: the same
#      deep-arithmetic snippet now passes, while deep control-flow nesting
#      (25 levels of nested `if`) still correctly raises SecurityError.
#   F. MoralCovenantScorer's "no_murder" check here was ast-shallow (only
#      catches `while True:` literally and a function calling itself by
#      name, both regex/AST pattern matches, not structural reasoning).
#      OmniChain's UnifiedCovenantScorer additionally checks whether a
#      `while True:` loop contains ANY reachable break/return, and whether
#      a self-recursive call is inside ANY enclosing `if` at all. Ported in
#      below as additional scoring signal. HONESTY NOTE, carried forward
#      from OmniChain's own limitations, not fixed here: "is there a
#      break/return anywhere in the loop body" doesn't prove the loop
#      terminates (the break could be unreachable), and "is the recursive
#      call inside any if at all" doesn't prove the recursion is bounded
#      (the if could always be true). These are heuristics layered on top
#      of other heuristics, not a termination proof -- still no formal
#      guarantee exists anywhere in this module, and still nothing here
#      changes that a passing score is informative, not a guarantee.
# ---------------------------------------------------------------------------

# PATCH LOG -- v8.2: RECURSIVE SECURITY PASS ON THIS FILE ITSELF, NOT ON A
# NEW THIRD-PARTY SOURCE. Same rule as every prior round: find by running,
# not by reading; fix what's fixable; document what isn't.
# ----------------------------------------------------------------
#   G. CROSS-PROTOCOL SIGNATURE REPLAY, confirmed empirically: a signature
#      produced to approve stake(amount=1234.0, duration=604800) also
#      validated as a completely valid /propose_code signature for
#      source_code="1234.0", parent_hashes=[], notes="604800" -- no
#      signing scheme in this file (Transaction, stake, code proposal)
#      tagged WHICH scheme it belonged to, so identical concatenated bytes
#      across schemes shared a valid signature. A second, narrower
#      ambiguity confirmed within one scheme: verify_code_signature's
#      ','.join(parent_hashes) meant parent_hashes=["ab,cd"] and
#      parent_hashes=["ab","cd"] produced the same joined string. Both
#      fixed via _domain_frame(): a fixed per-scheme domain tag plus
#      length-prefixed fields. BREAKING CHANGE to the signing wire format,
#      stated plainly -- see _domain_frame's docstring.
#   H. FAIL-OPEN "hasattr(self.db, ...)" GUARDS ON EVERY LEDGER CHECK IN
#      THE FILE. Originally written as defensive compatibility code for a
#      hypothetical Database without ledger support; Database has
#      unconditionally provided get_balance/record_ledger_entry/
#      apply_transaction_ledger since v7.2, so every one of these guards
#      had decayed into a silent bypass with no remaining legitimate
#      purpose. Worst instances, both confirmed by reading the actual
#      control flow rather than assuming: (1) the AUTHORITATIVE
#      block-assembly balance check in /mine included a transaction with
#      NO balance check at all if hasattr were False, rather than
#      rejecting it; (2) _accept_block_common -- the P2P path whose
#      entire documented purpose is "don't trust a block just because a
#      peer sent it" -- skipped its own ledger re-verification under the
#      same condition, defeating the one check specifically there to
#      catch a malicious or buggy peer. All such guards in this file
#      (stake(), /transactions, /mine, _handle_peer's TRANSACTION_PROPAGATE
#      path, _accept_block_common, genesis mint, StakingPool/
#      FriendshipTracker reload) are now unconditional: fail closed
#      (AttributeError on a malformed db) instead of fail open (silent
#      bypass).
#   I. ALIAS/INDIRECTION BYPASS OF CODE_FORBIDDEN_CALLS, worse than the
#      already-documented getattr bypass (item A) because it isn't even
#      caught by run_sandboxed(). visit_Call's forbidden-name check only
#      ever matched Call(func=Name(id=X)) -- a DIRECT call by bare name.
#      Confirmed: `def f(x): y = eval; return y(x)` scored a PERFECT 1.0.
#      run_sandboxed() didn't catch it either, and for a specific,
#      important reason: it only executes TOP-LEVEL module statements, and
#      `def f(x): ...` just DEFINES f without running its body -- the
#      NameError that an unrestricted-builtins call would eventually raise
#      never gets a chance to fire, because nothing in this file's review
#      path ever CALLS a proposal's own functions. This is more serious
#      than a scoring quirk: it's a working eval() escape hatch that
#      passes review clean and only becomes dangerous the moment the
#      accepted code is ever run somewhere with real builtins available --
#      which is the whole point of accepting it into the DAG. Fixed at the
#      static layer: any bare Load-context reference to a forbidden name,
#      not just a direct call of it, is now rejected -- closes aliasing
#      and container-storage indirection in one structural fix rather than
#      chasing individual indirection patterns one at a time.
# ---------------------------------------------------------------------------

# PATCH LOG -- v8.3: CONTINUED RECURSIVE PASS
# ----------------------------------------------------------------
#   J. BRIDGE STAGING COULD SPLICE A STRUCTURALLY BROKEN BLOCK INTO THE
#      CHAIN. Pre-existing since the original v7.0 merge, not something
#      introduced along the way -- found by re-auditing _accept_block_common,
#      the function shared by _handle_peer and _handle_bridge's
#      staging-promotion loop. _handle_peer checked block.index/
#      previous_hash continuity itself BEFORE calling in; _handle_bridge
#      never did. Confirmed empirically: a block with index=99 (real chain
#      length 1) and a previous_hash matching nothing in the real chain
#      was ACCEPTED via _accept_block_common once its alignment_score was
#      made to match the current governor value -- trivial for an
#      attacker, since alignment_score is just the mean benefit_score of
#      the block's own (attacker-chosen) transactions. Chain ended up with
#      indices [0, 99], no real hash linkage. The bridge -- specifically
#      built to stage blocks from possibly-unverified new peers before
#      trusting them -- was the weakest link in chain-continuity
#      enforcement, not the strongest. Fixed by moving the check INTO the
#      shared function instead of leaving it as a precondition each caller
#      has to separately remember -- which is how it was missed the first
#      time: added at one call site when there was only one caller, never
#      migrated when a second caller (_handle_bridge) started using the
#      same shared function.
#   K. RECURSION-GUARD HEURISTIC BOTH UNDER- AND OVER-FIRED, found in the
#      same pass. Tightening the moral-score boundary (originally: an
#      unguarded self-recursive function scored EXACTLY 0.7, the pass
#      threshold, by coincidence between two independently-written
#      checks) surfaced a real false positive in the ported guard-detection
#      heuristic itself: it only recognized a recursive call as "guarded"
#      if literally NESTED INSIDE an `if` block, so the single most common
#      recursion-with-base-case shape in real code --
#      `if n <= 0: return 0` followed by an unconditional `return f(n-1)`
#      as a sibling statement -- was flagged identically to genuinely
#      unbounded recursion. Both fixed together: the guard-detection
#      heuristic now recognizes any `if` containing a reachable
#      Return/Break anywhere in the function as a guard clause (still a
#      heuristic, not a termination proof -- documented as such in place),
#      and the unguarded-recursion penalty was raised from 0.3 to 0.35 (matching
#      the while-loop penalty) so a real violation fails clearly (0.65)
#      instead of riding the pass/fail boundary.
# ---------------------------------------------------------------------------

# PATCH LOG -- v8.4: STAKING HAD AN ENTRY DOOR AND NO EXIT, AND THE ENTRY
# DOOR FOR CLAIMS HAD NO LOCK ON IT
# ----------------------------------------------------------------
#   L. THREE RELATED GAPS, FOUND TOGETHER WHILE FOLLOWING "does duration
#      actually get enforced":
#      1. `duration` was validated at stake() time (must be >=
#         STAKE_MIN_DURATION) and then never checked again anywhere.
#         Confirmed: a stake declared for the 1-day minimum could be
#         claimed for a real, nonzero, repeatable reward after 0.5
#         seconds. The "lock" was a number stored on the Stake object
#         that nothing ever read back.
#      2. /claim_rewards took a bare `pubkey` from the request body with
#         NO signature at all -- confirmed: any third party could trigger
#         a claim on any pubkey's stake with no proof of anything, the
#         same unauthenticated-write gap /stake was fixed for in v7.2,
#         reopened here for a sibling endpoint. Measured the practical
#         impact of that gap specifically: claim frequency alone produces
#         extra yield (discrete compounding approaching the continuous-
#         compounding limit as claims get more frequent) -- confirmed
#         numerically at ~0.12%/year extra at a sustained one-claim-per-6-
#         seconds cadence. Real, but small (bounded by e^r - 1 - r for
#         this system's YIELD_RATE) -- nowhere near the ~525,218-from-1,000
#         inflation this project hit before (that one was a full-history
#         repricing bug, fixed in v7.1; this is a different, much smaller
#         failure mode that survived because nobody had tested "what if
#         you claim very frequently," only "what if you claim after a
#         long gap"). The bigger problem was never the size of the leak --
#         it was that ANYONE, not just the stake owner, could drive it.
#      3. NO PATH ANYWHERE IN THIS FILE, IN ANY PRIOR VERSION, EVER
#         CREDITED A STAKED BALANCE BACK TO THE SPENDABLE LEDGER.
#         stake() debits via record_ledger_entry(pubkey, -amount,
#         "stake_lock", ...); claim_rewards() only ever grew stake.amount
#         internally. Once staked, funds were permanently unspendable --
#         not stolen, not destroyed, just never returned. There was no
#         unstake() method, no /unstake route, nothing.
#      FIXED together: claim_rewards() now gates on the stake's own
#      declared duration having elapsed at least once (compounding after
#      that point is unchanged from v7.1's checkpoint behavior). Both
#      /claim_rewards and the new /unstake require a domain-and-action-
#      tagged signature (verify_stake_action_signature -- "claim" and
#      "unstake" are separate signable actions so one can't replay as the
#      other) plus nonce-based replay protection, so the same signed
#      request can't be rebroadcast to trigger repeated claims -- which
#      also caps item 2's frequency leak, since only the real owner's own
#      cadence can drive it now. unstake() compounds any final pending
#      reward, credits the FULL current stake.amount (principal + every
#      reward ever compounded into it) back to the ledger in one entry,
#      and CLOSES the stake (an UPDATE setting closed_at) rather than
#      deleting it -- consistent with this file's own stated "append-only
#      ... no silent overwrites" design principle, applied to staking for
#      the first time. load_stakes() now excludes closed stakes from the
#      active pool on reload, so a restart doesn't resurrect an
#      already-unstaked position.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PATCH LOG -- v8.5: SUCCESSION GUARDIAN -- WHAT HAPPENS IF THE PRIMARY IS
# INCAPACITATED, WITHOUT HANDING CONTROL TO ANYTHING AUTONOMOUS
#
# M. Requested feature, not a found bug: combine three succession
#    mechanisms into one design -- (1) a designated human successor,
#    (2) M-of-N guardian multi-sig, (3) a dead-man's-switch heartbeat --
#    rather than building any of them in isolation, and explicitly WITHOUT
#    ever making an autonomous agent (AI, "collective," or any unsigned
#    condition) a party whose confirmation counts toward control passing.
#    New classes SuccessionConfig / SuccessionGuardianSystem, new Database
#    methods/tables (succession_configs, succession_guardians,
#    succession_confirmations), new routes /succession/register,
#    /succession/heartbeat, /succession/confirm, /succession/status, and a
#    new background _succession_monitor_loop that ONLY ever opens a
#    pending window on a missed heartbeat -- it never itself confirms
#    incapacitation or executes succession; that requires threshold
#    guardians' real signatures through /succession/confirm, every time.
#    Reuses the existing _domain_frame()-framed RSA+PSS scheme rather than
#    inventing a second signing convention.
#
#    The asymmetry between triggering and reversing succession is
#    deliberate, not an oversight: once threshold guardians have confirmed
#    incapacitation, the primary's own resumed heartbeat is NOT sufficient
#    to reclaim control -- that requires a SEPARATE round of threshold
#    guardian confirmations (confirm_type="reclaim"). Reasoning: a bare
#    heartbeat is the cheapest possible signal for an attacker holding a
#    compromised primary key (post-succession) to forge, and if a
#    heartbeat alone could reverse a legitimate succession, the guardians
#    would not actually be the root of trust for the account -- whoever
#    holds the primary key at any given moment would be, which defeats
#    having guardians at all. Both entry and exit require the same
#    threshold of the same real people to agree.
#
#    Verified empirically (test_succession.py, 35/35 passing) before
#    integration into this file, including the adversarial cases: forged
#    heartbeat signature rejected; forged guardian signature rejected;
#    non-guardian pubkey rejected even with a valid self-signature;
#    duplicate guardian confirmation does not double-count (idempotent,
#    not an error); succession does NOT activate below threshold;
#    resumed heartbeat before threshold cancels a pending episode; a
#    confirmation recorded against a cancelled episode does NOT carry
#    forward and silently complete a later, unrelated episode (this is
#    why episode_id is part of both the DB primary key and the signed
#    payload, not just a display counter); reclaim requires its own fresh
#    threshold of guardian signatures and is rejected on heartbeat alone.
#
#    STILL OPEN, FLAGGED RATHER THAN HIDDEN: registration itself has the
#    same self-attested-identity model as every other identity in this
#    file (see PATCH LOG items generally) -- nothing stops someone from
#    registering a succession config FOR a pubkey they don't hold the
#    private key to, but that alone moves nothing, since heartbeat and
#    confirm both require real signatures from the actual keyholders.
#    Guardian pubkeys are not vetted for being distinct real people versus
#    one person holding multiple keys -- that trust judgment (who you
#    actually pick as guardians) is inherently a human decision this
#    system can't verify from the outside, same as it can't verify a
#    Transaction's stated "amount" reflects a real-world transfer.
# ---------------------------------------------------------------------------

CODE_MAX_EVAL_TIME_SECONDS = 2.0
CODE_MAX_BRANCHES = 100
CODE_MAX_AST_NODES = 1000
CODE_MAX_NESTING_DEPTH = 20            # block-structure nesting (If/For/While/FunctionDef/With)
CODE_MAX_RAW_EXPRESSION_DEPTH = 150    # NEW v8.1 -- see item E: every-node depth, looser ceiling
CODE_MAX_INPUT_SIZE = 1_000_000
CODE_MIN_MORAL_SCORE = 0.7

CODE_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "len": len, "list": list,
    "map": map, "max": max, "min": min, "range": range, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "enumerate": enumerate,
}

# FIXED (item A above): getattr/setattr/delattr/vars/globals/locals are the
# standard sandbox-escape primitives and were missing from the original
# forbidden set, which only had {eval, exec, __import__, compile, open}.
# UPDATED v8.1 -- see item E below: unioned with a second, independently
# arrived-at forbidden-call list from a third source file ("OmniChain"),
# which additionally banned hasattr/type/dir. Added defensively (introspec-
# tion helpers with little legitimate need inside this restricted grammar)
# even though run_sandboxed()'s CODE_SAFE_BUILTINS already excludes `type`
# at execution time -- the AST layer shouldn't rely on the execution layer
# alone for defense in depth.
CODE_FORBIDDEN_CALLS = {"eval", "exec", "__import__", "compile", "open",
                         "getattr", "setattr", "delattr", "vars", "globals", "locals",
                         "hasattr", "type", "dir"}

_CODE_ALLOWED_AST_NODES: Set[type] = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.AugAssign, ast.Name, ast.Load, ast.Store, ast.Param,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.For, ast.If,
    ast.Pass, ast.Call, ast.List, ast.Tuple, ast.Subscript, ast.Slice,
    ast.Expr, ast.alias, ast.Attribute, ast.ClassDef, ast.While,
    ast.Break, ast.Continue, ast.BoolOp, ast.And, ast.Or,
    ast.Not, ast.Invert, ast.LShift, ast.RShift, ast.BitAnd, ast.BitOr, ast.BitXor,
    ast.IfExp, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.Lambda, ast.FormattedValue, ast.JoinedStr,
}
if hasattr(ast, "Index"):
    _CODE_ALLOWED_AST_NODES.add(getattr(ast, "Index"))


class CodeSecurityError(Exception):
    """Raised when an AST or source snippet violates code-governance
    sandbox boundaries. Named distinctly from ledger-side exceptions so
    the two error domains (financial/ethics vs. code-governance) are never
    confused in a log or a caller's except clause."""
    pass


_CODE_BRANCH_NODE_TYPES = (
    ast.If, ast.For, ast.While, ast.IfExp, ast.BoolOp,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp
)


_CODE_BLOCK_NODE_TYPES = (ast.If, ast.For, ast.While, ast.FunctionDef)


class SecurityValidator(ast.NodeVisitor):
    """
    Stateless-per-run AST validator.

    FIXED v8.1 -- see item E in this section's header. Previously used ONE
    flat counter incremented for every visited node regardless of type,
    confirmed to reject completely benign, non-dangerous code (deeply
    parenthesized arithmetic with zero control-flow nesting) at the same
    ceiling meant for dangerous control-flow nesting. Now split, adapted
    from the OmniChain source: block_depth only counts If/For/While/
    FunctionDef (ceiling CODE_MAX_NESTING_DEPTH=20); raw_depth counts every
    node (much looser ceiling CODE_MAX_RAW_EXPRESSION_DEPTH=150, there
    mainly to prevent Python's own C-stack RecursionError from deeply
    nested non-block expressions, not to police ordinary code).
    """

    def __init__(self, strict: bool = True):
        self.strict = strict
        self.node_count = 0
        self.block_depth = 0
        self.raw_depth = 0
        self.branch_count = 0

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        if self.node_count > CODE_MAX_AST_NODES:
            raise CodeSecurityError(f"AST node count exceeds limit ({CODE_MAX_AST_NODES})")

        if self.strict and type(node) not in _CODE_ALLOWED_AST_NODES:
            raise CodeSecurityError(f"Forbidden AST node: {type(node).__name__}")

        is_branch = isinstance(node, _CODE_BRANCH_NODE_TYPES)
        if is_branch:
            self.branch_count += 1
            if self.branch_count > CODE_MAX_BRANCHES:
                raise CodeSecurityError(f"Branch count exceeds limit ({CODE_MAX_BRANCHES})")

        is_block = isinstance(node, _CODE_BLOCK_NODE_TYPES)
        if is_block:
            self.block_depth += 1
            if self.block_depth > CODE_MAX_NESTING_DEPTH:
                raise CodeSecurityError(f"Block nesting depth exceeds limit ({CODE_MAX_NESTING_DEPTH})")

        self.raw_depth += 1
        if self.raw_depth > CODE_MAX_RAW_EXPRESSION_DEPTH:
            raise CodeSecurityError(f"Expression nesting depth exceeds safety limit ({CODE_MAX_RAW_EXPRESSION_DEPTH})")

        super().generic_visit(node)
        self.raw_depth -= 1
        if is_block:
            self.block_depth -= 1

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise CodeSecurityError(f"Dunder attribute access forbidden: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in CODE_FORBIDDEN_CALLS:
            raise CodeSecurityError(f"Forbidden function call: {node.func.id}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """
        NEW v8.2 -- see PATCH LOG item I (module docstring). visit_Call
        above only ever matched Call(func=Name(id=X)) -- a DIRECT call by
        bare name. Confirmed this misses simple aliasing entirely:
        `y = eval; return y(x)` -- the call site is `y(x)`, func is
        Name("y"), never matches the blacklist. Worse than the getattr
        bypass already documented and closed: this one is NOT caught by
        run_sandboxed() either, because that only executes TOP-LEVEL
        module statements -- `def f(x): y = eval; return y(x)` just
        DEFINES f without ever running its body, so the NameError that
        would fire from an unrestricted-builtins call never has a chance
        to happen. Confirmed empirically: this exact snippet scored a
        perfect 1.0 and would have entered the code DAG as an approved
        proposal, with a real eval() escape hatch hidden inside a
        function that only becomes dangerous the moment it's ever called
        somewhere with real (non-restricted) builtins available -- which
        is the entire point of accepting code into this DAG in the first
        place. Fixed at the STATIC layer, where it belongs: any bare
        Load-context reference to a forbidden name -- not just a direct
        call of it -- is now rejected, closing aliasing, container
        storage (`fns = [eval]`), and any other indirection that routes
        through a plain Name node.
        """
        if isinstance(node.ctx, ast.Load) and node.id in CODE_FORBIDDEN_CALLS:
            raise CodeSecurityError(f"Forbidden name reference: {node.id}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        raise CodeSecurityError("Import statements are forbidden")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        raise CodeSecurityError("Import statements are forbidden")


class MoralCovenantScorer:
    """
    SECURITY NOTE, unresolved -- see item A in this section's header. This
    is a regex-over-text + shallow-AST heuristic, not a semantic analyzer.
    A single dangerous call embedded via parameter-name smuggling (e.g.
    `def f(os): return os.system(...)`) still scores ABOVE the default
    0.7 threshold (confirmed: 0.76) because renaming a call target defeats
    both the regex and the AST allowlist simultaneously. Do not treat a
    high moral_score as a safety guarantee, for exactly the same reason
    the module docstring already tells you not to trust MockJudge output
    as one.
    """
    def __init__(self):
        self.commandment_weights = {
            "no_false_witness": 1.0,
            "no_theft": 1.0,
            "no_harm": 1.0,
            "no_adultery": 0.9,
            "no_murder": 1.0,
            "no_coveting": 0.8,
            "honor_dependencies": 0.7,
            "no_idolatry": 0.9,
            "no_graven_images": 0.6,
            "remember_sabbath": 0.5,
        }
        self.violation_patterns = {
            "no_theft": [r"open\(", r"os\.", r"subprocess\.", r"requests\.", r"socket\."],
            "no_harm": [r"os\.remove\(", r"shutil\.rmtree\(", r"os\.system\(", r"os\.kill\("],
            "no_adultery": [r"open\(.*'w'\)", r"os\.chmod\(", r"eval\(", r"exec\("],
            "no_murder": [r"while\s+True:", r"for\s+.*\s+in\s+itertools\.count\("],
            "no_coveting": [r"range\(.*1000000"],
            "no_idolatry": [r"password\s*=\s*['\"]", r"secret\s*=\s*['\"]", r"api_key\s*=\s*['\"]"],
            "remember_sabbath": [r"while\s+True:\s*pass", r"time\.sleep\(0\."],
        }

    def _check_ast_violations(self, tree: ast.AST) -> Dict[str, int]:
        violations = {cmd: 0 for cmd in self.commandment_weights}
        for node in ast.walk(tree):
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                violations["no_murder"] += 1
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == node.name:
                        violations["no_murder"] += 1
        return violations

    def _has_reachable_break_or_return(self, node: ast.AST) -> bool:
        """NEW v8.1 -- see item F. Ported from OmniChain's
        _verify_loop_termination. HONESTY NOTE: "a break/return exists
        somewhere in the loop body" does not prove it is reachable or that
        the loop actually terminates -- it's a heuristic, not a proof. A
        `while True: if False: break` passes this check and never
        terminates. Kept as an additional signal, not a guarantee."""
        return any(isinstance(child, (ast.Break, ast.Return)) for child in ast.walk(node))

    def _detect_unguarded_self_recursion(self, tree: ast.AST) -> Set[str]:
        """NEW v8.1 -- see item F. Ported from OmniChain's
        _detect_unbounded_recursion. HONESTY NOTE: neither this nor the
        original OmniChain version proves the recursion is bounded -- an
        `if` guard's condition could be trivially true, or the base case
        could be on the wrong branch. Kept as an additional signal, not a
        proof of termination; a function can pass this check and still
        recurse unboundedly.

        FIXED v8.3 -- PATCH LOG item K. The original (and v8.1-v8.2's
        ported) version only recognized a call as "guarded" if it was
        textually NESTED INSIDE an `if` block's body. Confirmed
        false-positive: the single most common recursion-with-base-case
        shape in real code --
            def f(n):
                if n <= 0:
                    return 0
                return f(n - 1)
        -- has its recursive call as a SIBLING statement after the guard
        clause, not nested inside it, so it was flagged as "unguarded"
        identically to genuinely-unbounded recursion. Fixed: a function is
        now considered guarded if it contains ANY `if` whose body reaches
        a Return or Break at all -- recognizing the early-return
        guard-clause pattern generally, not just the nested-call shape.
        Still a heuristic, not a proof -- see HONESTY NOTE above -- but no
        longer penalizes the ordinary case for using ordinary style."""
        unguarded: Set[str] = set()
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            has_recursive_call = any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func.name
                for node in ast.walk(func)
            )
            if not has_recursive_call:
                continue
            has_guard_clause = any(
                isinstance(n, ast.If) and any(isinstance(s, (ast.Return, ast.Break)) for s in ast.walk(n))
                for n in ast.walk(func)
            )
            if not has_guard_clause:
                unguarded.add(func.name)
        return unguarded

    def _check_structural_violations(self, tree: ast.AST) -> float:
        """NEW v8.1 -- see item F. Returns a 0..1 structural score,
        separate from the weighted-commandment regex/AST score, combined
        via min() in score_code() below (same combination OmniChain used).
        An unterminating while-True costs 0.35; each unguarded
        self-recursive function costs 0.35.

        FIXED v8.3 -- PATCH LOG item K. This was 0.3, not 0.35. Confirmed:
        `def f(n): return f(n - 1)` (unguarded self-recursion, a real
        RecursionError/DoS risk if ever actually called) scored EXACTLY
        0.7 -- the pass/fail threshold itself, `<` not `<=` -- because
        this structural score (1.0 - 0.3 = 0.7) and the older, separate
        weighted-commandment score (~0.89, since the pre-existing
        _check_ast_violations already flags the same self-call under
        "no_murder") landed on 0.7 as their min() by coincidence, not by
        design. A single genuinely dangerous, unguarded recursive
        function should not pass purely because two independently-written
        checks happened to intersect exactly at the boundary. Matched to
        the while-loop penalty (0.35) so one violation of either kind
        clearly fails (0.65 < 0.7) instead of riding the edge."""
        score = 1.0
        for node in ast.walk(tree):
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                if not self._has_reachable_break_or_return(node):
                    score -= 0.35
        score -= 0.35 * len(self._detect_unguarded_self_recursion(tree))
        return max(0.0, score)

    def _check_source_violations(self, source: str) -> Dict[str, int]:
        violations = {cmd: 0 for cmd in self.commandment_weights}
        source_lower = source.lower()
        for cmd, patterns in self.violation_patterns.items():
            for pattern in patterns:
                if re.search(pattern, source_lower):
                    violations[cmd] += 1
        return violations

    def score_code(self, source: str, tree: Optional[ast.AST] = None) -> float:
        if tree is None:
            tree = ast.parse(source)
        ast_violations = self._check_ast_violations(tree)
        source_violations = self._check_source_violations(source)
        total_violations = {
            cmd: ast_violations.get(cmd, 0) + source_violations.get(cmd, 0)
            for cmd in self.commandment_weights
        }
        weighted_violations = sum(
            total_violations[cmd] * self.commandment_weights[cmd]
            for cmd in self.commandment_weights
        )
        max_possible_violations = sum(self.commandment_weights.values())
        weighted_score = max(0.0, 1.0 - (weighted_violations / max_possible_violations))
        # NEW v8.1 -- see item F. Structural score is a separate signal,
        # combined via min() (not averaged) so a structural red flag can't
        # be diluted by an otherwise-clean weighted score.
        structural_score = self._check_structural_violations(tree)
        return min(weighted_score, structural_score)


def run_sandboxed(source: str, timeout: float = CODE_MAX_EVAL_TIME_SECONDS) -> Dict[str, Any]:
    """
    NEW v8.0 -- see item C in this section's header. The original file
    imported multiprocessing and defined MAX_EVAL_TIME_SECONDS /
    SAFE_BUILTINS but never called anything with them; nothing ever ran a
    candidate snippet. This wires them up for real: executes `source` in a
    child process with __builtins__ replaced by CODE_SAFE_BUILTINS, joins
    with a hard wall-clock timeout, and terminates the child if it
    overruns. Only covers top-level module execution (defining functions,
    module-level statements) -- it does NOT call into any function the
    snippet defines with any arguments, so it says nothing about the
    safety of later invoking those functions with attacker-chosen inputs.
    That limitation is why CovenantGuardian.enforce() below still runs the
    static checks first rather than relying on this alone.
    """
    # FIXED v8.1, found by actually running this function, not by reading
    # it: a "spawn"-context Process must be able to PICKLE its target.
    # _target was originally a nested closure (defined inside
    # run_sandboxed), and closures are not picklable -- confirmed:
    # AttributeError: Can't pickle local object 'run_sandboxed.<locals>._target'
    # on the very first call. Using "fork" instead: fork clones the parent
    # process's memory directly rather than pickling anything, so a nested
    # closure works fine. Trade-off, stated plainly: fork is POSIX-only (no
    # Windows) and, in a threaded program, only the calling thread survives
    # into the child -- acceptable here since this is a short-lived,
    # one-shot check-and-exit call with no inherited thread state the child
    # depends on, but worth knowing if this is ever ported.
    def _target(src, q):
        restricted_globals = {"__builtins__": dict(CODE_SAFE_BUILTINS)}
        try:
            compiled = compile(src, "<code_proposal>", "exec")
            exec(compiled, restricted_globals)
            q.put({"ok": True, "error": None})
        except Exception as e:
            q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})

    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    proc = ctx.Process(target=_target, args=(source, q))
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(1)
        return {"ran": True, "timed_out": True, "ok": False, "error": f"exceeded {timeout}s"}
    if not q.empty():
        result = q.get()
        return {"ran": True, "timed_out": False, "ok": result["ok"], "error": result["error"]}
    return {"ran": True, "timed_out": False, "ok": False, "error": "child exited without reporting (crash/signal)"}


class CovenantGuardian:
    """
    Gate for code proposals: AST/branch/nesting/import checks (hard,
    structural) + MoralCovenantScorer threshold (soft, bypassable -- see
    class docstring above) + run_sandboxed() actual execution (real, but
    narrow -- see run_sandboxed docstring). All three run; a proposal must
    pass all three to be accepted into the code DAG (see /propose_code).

    AUDITED v8.2 -- `execute=False` disables run_sandboxed() entirely,
    which is now the layer that catches anything the visit_Name alias
    check (see PATCH LOG item I) doesn't -- treat execute=False as
    security-relevant, not a minor perf knob. Confirmed by grep: every
    CovenantGuardian(...) construction site in this file (DAGNode.create's
    default and P2PNode.code_guardian) uses the execute=True default. If
    this is ever instantiated with execute=False for faster tests, do not
    let that instance's validate_and_score() results reach a real
    /propose_code accept path.
    """
    def __init__(self, min_moral_score: float = CODE_MIN_MORAL_SCORE, execute: bool = True):
        self.moral_scorer = MoralCovenantScorer()
        self.min_moral_score = min_moral_score
        self.execute = execute

    def validate_and_score(self, source_code: str, tree: Optional[ast.AST] = None) -> Tuple[bool, float, str]:
        if tree is None:
            try:
                tree = ast.parse(source_code)
            except SyntaxError as e:
                return False, 0.0, f"SyntaxError: {e}"

        validator = SecurityValidator()
        try:
            validator.visit(tree)
        except CodeSecurityError as e:
            return False, 0.0, f"SecurityError: {e}"

        moral_score = self.moral_scorer.score_code(source_code, tree)
        if moral_score < self.min_moral_score:
            return False, moral_score, f"MoralScoreTooLow: {moral_score:.2f} < {self.min_moral_score}"

        if self.execute:
            result = run_sandboxed(source_code)
            if result["timed_out"]:
                return False, moral_score, f"SandboxTimeout: exceeded {CODE_MAX_EVAL_TIME_SECONDS}s"
            if not result["ok"]:
                return False, moral_score, f"SandboxExecutionError: {result['error']}"

        return True, moral_score, ""

    def enforce(self, source_code: str, tree: Optional[ast.AST] = None) -> float:
        success, moral_score, error = self.validate_and_score(source_code, tree)
        if not success:
            raise CodeSecurityError(f"CovenantGuardian rejected code: {error}")
        return moral_score


class LoopSafetyAnalyzer(ast.NodeVisitor):
    """
    Carried forward UNCHANGED and STILL UNWIRED -- see item D in this
    section's header. Fully implemented, never called by anything else in
    the original file or in this merge. Detects a loop body mutating,
    reassigning, or aliasing the sequence it's iterating over. Left as
    dead code deliberately rather than either deleting someone's prior
    work or falsely claiming it's active.
    """
    def __init__(self, target_seq: str):
        self.target_seq = target_seq
        self.is_mutated = False

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == self.target_seq:
                self.is_mutated = True
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id == self.target_seq:
                self.is_mutated = True
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == self.target_seq:
                self.is_mutated = True
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                if target.value.id == self.target_seq:
                    self.is_mutated = True
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Name) and node.target.id == self.target_seq:
            self.is_mutated = True
        self.generic_visit(node)


@dataclass
class DAGNode:
    """
    A hash-chained, moral-scored unit of PROPOSED CODE (not a financial
    transaction). parent_hashes gives it real DAG structure (multiple
    parents allowed), distinct from Block's strictly linear previous_hash
    chain -- deliberately kept as a separate structure rather than forced
    into the sequential block chain, since code proposals don't need
    total ordering the way value transfers do.

    FIXED v8.2 -- two integrity gaps found while auditing this class, not
    while writing it fresh:
    1. `signature` was never a field on this dataclass at all -- verified
       once at the API boundary, then discarded. A stored DAGNode carried
       no cryptographic proof of who submitted it; "who signed this" was
       only ever true at request time, not a durable property of the
       ledgered record. Added as a real field, persisted alongside
       everything else.
    2. hash_id was computed over `ast.unparse(ast.parse(source_code))` --
       the REFORMATTED source -- while the signature covers the RAW
       submitted source_code. Confirmed: these differ whenever formatting
       is non-canonical (e.g. different quote style, trailing whitespace),
       which means a stored node's own (source_code, signature) pair could
       fail to re-verify against itself later, even though it was valid at
       submission time -- the persisted "source_code" wasn't what was
       actually signed. Fixed: hash_id and the stored source_code are now
       both over the RAW input. Trade-off, stated plainly: this drops
       whitespace-insensitive deduplication (two formatting variants of
       identical logic now get different hash_ids) in exchange for every
       stored node being independently, cryptographically self-consistent
       -- verify_code_signature(node.submitter_pubkey, node.source_code,
       node.parent_hashes, node.transformation_notes, node.signature) and
       hashlib.sha256(node.source_code...) both check out from stored data
       alone, with nothing to take on trust from submission time.
    """
    hash_id: str
    source_code: str
    parent_hashes: List[str]
    transformation_notes: str
    moral_score: float = 1.0
    submitter_pubkey: str = ""
    signature: str = ""
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def create(cls, source_code: str, parent_hashes: List[str], notes: str,
               submitter_pubkey: str = "", signature: str = "",
               guardian: Optional["CovenantGuardian"] = None) -> "DAGNode":
        if len(source_code) > CODE_MAX_INPUT_SIZE:
            raise CodeSecurityError(f"Source exceeds MAX_INPUT_SIZE ({CODE_MAX_INPUT_SIZE})")

        guardian = guardian if guardian is not None else CovenantGuardian()
        parsed = ast.parse(source_code)
        moral_score = guardian.enforce(source_code, parsed)

        # FIXED v8.2 -- hash and store the RAW source that was actually
        # signed, not a reformatted version. See class docstring item 2.
        hash_id = hashlib.sha256(source_code.encode("utf-8")).hexdigest()[:16]

        return cls(
            hash_id=hash_id,
            source_code=source_code,
            parent_hashes=parent_hashes,
            transformation_notes=notes,
            moral_score=moral_score,
            submitter_pubkey=submitter_pubkey,
            signature=signature,
        )

    def reverify(self) -> bool:
        """NEW v8.2. Independently re-checks this node's OWN stored data
        against itself: does the signature actually verify for this exact
        (submitter_pubkey, source_code, parent_hashes, transformation_notes),
        and does hash_id actually match sha256(source_code)? Lets any
        holder of the DAG (not just the node that originally accepted the
        submission) audit it later without re-trusting the original
        accept-time check."""
        if hashlib.sha256(self.source_code.encode("utf-8")).hexdigest()[:16] != self.hash_id:
            return False
        return verify_code_signature(self.submitter_pubkey, self.source_code,
                                      self.parent_hashes, self.transformation_notes, self.signature)


def verify_code_signature(pubkey_pem: str, source_code: str, parent_hashes: List[str],
                           notes: str, signature_b64: str) -> bool:
    """Same RSA+PSS scheme as verify_stake_signature / Transaction.verify --
    proves the submitter holds the private key for the pubkey they're
    attaching to this code proposal. Without this, /propose_code would
    have the exact same unauthenticated-submission gap already flagged
    for /peers (module docstring item 4).

    UPDATED v8.2 -- domain-tagged and length-prefixed via _domain_frame().
    Previously `f"{pubkey_pem}{source_code}{','.join(parent_hashes)}{notes}"`
    had TWO confirmed ambiguities: (1) no domain tag, so a signature from a
    different scheme (e.g. a stake approval) could replay as valid here --
    see _domain_frame's docstring for the empirical proof; (2)
    ','.join(parent_hashes) meant parent_hashes=["ab,cd"] (one hash
    containing a literal comma) and parent_hashes=["ab","cd"] (two hashes)
    produced the IDENTICAL joined string "ab,cd" and therefore the same
    signature would validate both -- confirmed by construction, though real
    hash_ids are hex digests that structurally can't contain commas, this
    function's own contract didn't enforce that, so it was a latent gap
    rather than a currently-reachable one. Both closed by per-field
    length-prefixing, which makes every field's boundary unambiguous
    regardless of content."""
    try:
        payload = _domain_frame(b"COVENANT_CODE_V1", pubkey_pem, source_code, *parent_hashes, notes)
        pub_key = serialization.load_pem_public_key(pubkey_pem.encode(), backend=default_backend())
        pub_key.verify(
            base64.b64decode(signature_b64),
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Financial Layer: Staking & Yield  (weird_science only; china had none)
# ---------------------------------------------------------------------------

class StakingPool:
    """
    SECURITY NOTE — unresolved by this merge, see module docstring item 1.
    Nothing here verifies the staker actually holds `amount`. Both original
    sources shared this gap (weird_science's stake() literally comments
    "pseudocode: assume they do"). Left in place with a loud comment at the
    point of trust rather than silently carried forward unflagged.

    PERSISTENCE — FIXED v7.1, see module docstring item 5. Previously had
    no reload path at all; a fresh instance always started from .stakes
    == {} regardless of what was in the db.
    """
    def __init__(self, db: "Database"):
        self.db = db
        self.stakes: Dict[str, Stake] = {}
        self.total_staked: float = 0.0
        self.lock = threading.Lock()
        # FIXED v8.2 -- see PATCH LOG item H (module docstring). This and
        # every other `hasattr(self.db, ...)` guard on a ledger method in
        # this file used to make balance/persistence enforcement OPTIONAL:
        # if self.db ever lacked the method (wrong object passed, future
        # refactor, a test double), the check silently no-opped instead of
        # failing. Database has unconditionally provided these methods
        # since v7.1/v7.2 -- there is no longer a legitimate reason for
        # the conditionality, so it's removed. If a Database-like object
        # without these methods is ever passed in, this now raises
        # AttributeError immediately (fail closed) instead of silently
        # running with reload/balance-checking turned off (fail open).
        self.stakes = db.load_stakes()
        self.total_staked = sum(s.amount for s in self.stakes.values())

    def stake(self, pubkey: str, amount: float, duration: int) -> Tuple[bool, str]:
        if amount <= 0:
            return False, "Stake amount must be positive"
        if duration < STAKE_MIN_DURATION:
            return False, f"Stake duration must be at least {STAKE_MIN_DURATION // 86400} days"

        with self.lock:
            if pubkey in self.stakes:
                return False, "User already has an active stake"

            # FIXED v7.2 — see module docstring item 1 / item 8 in patch
            # log. Previously trusted `amount` blindly (comment used to
            # read "no ledger exists anywhere in this system to check
            # `amount` against" -- now one does). UPDATED v8.2: no longer
            # gated by hasattr -- see __init__ comment above.
            balance = self.db.get_balance(pubkey)
            if balance < amount:
                return False, f"Insufficient balance: have {balance:.2f}, need {amount:.2f}"

            stake = Stake(pubkey=pubkey, amount=amount, start_time=time.time(), duration=duration, reward_rate=YIELD_RATE)
            self.stakes[pubkey] = stake
            self.total_staked += amount
            self.db.save_stake(stake)
            self.db.record_ledger_entry(pubkey, -amount, "stake_lock", ref_id=stake.get_id())
            return True, f"Staked {amount} for {duration // 86400} days"

    def claim_rewards(self, pubkey: str) -> Tuple[float, str]:
        """
        FIXED v8.4 -- see PATCH LOG item L. `duration` was validated at
        stake() time (must be >= STAKE_MIN_DURATION) and then never
        checked again anywhere. Confirmed empirically: a stake declared
        for the 1-day minimum could be claimed for a real, nonzero,
        repeatable reward after 0.5 seconds -- the "lock" was a number
        stored on the Stake object that nothing ever read back. Fixed:
        the first claim is gated on the full declared duration having
        elapsed at least once. After that point, repeated compounding
        claims proceed exactly as before (the checkpoint/last_claim_time
        mechanism from v7.1 is unchanged) -- the fix is "you can't claim
        before the lock you agreed to has passed," not "you can only ever
        claim once."
        """
        with self.lock:
            stake = self.stakes.get(pubkey)
            if not stake:
                return 0.0, "No active stake found"
            current_time = time.time()
            unlock_time = stake.start_time + stake.duration
            if current_time < unlock_time:
                return 0.0, f"Stake still locked for {unlock_time - current_time:.0f} more seconds"
            rewards = stake.calculate_rewards(current_time)
            if rewards <= 0:
                return 0.0, "No rewards to claim yet"
            stake.amount += rewards
            stake.claimed_rewards += rewards
            stake.last_claim_time = current_time  # checkpoint — see Stake.calculate_rewards
            self.db.update_stake(stake)
            return rewards, f"Claimed {rewards} rewards (new stake amount: {stake.amount})"

    def unstake(self, pubkey: str) -> Tuple[float, str]:
        """
        NEW v8.4 -- see PATCH LOG item L. Confirmed there was NO path
        anywhere in this file, in any prior version, that ever credited a
        staked balance back to the spendable ledger. stake() debits via
        record_ledger_entry(pubkey, -amount, "stake_lock", ...);
        claim_rewards() only ever grows stake.amount internally. Once
        staked, funds were permanently unspendable -- not stolen, not
        destroyed, just never returned. Gated on the same duration check
        as claim_rewards(): compounds in any final pending reward, then
        credits the ENTIRE current stake.amount (original principal plus
        every reward ever compounded into it) back to the ledger in one
        entry, and removes the stake entirely. total_staked is decremented
        by the amount actually removed -- still subject to the drift
        caveat already documented for distribute_block_rewards (patch log
        item 7); not solved here, same reasoning as before (belongs with
        the eventual balance-ledger-derived total_staked, not a second
        hand-maintained counter).
        """
        with self.lock:
            stake = self.stakes.get(pubkey)
            if not stake:
                return 0.0, "No active stake found"
            current_time = time.time()
            unlock_time = stake.start_time + stake.duration
            if current_time < unlock_time:
                return 0.0, f"Stake still locked for {unlock_time - current_time:.0f} more seconds"
            final_reward = stake.calculate_rewards(current_time)
            if final_reward > 0:
                stake.amount += final_reward
                stake.claimed_rewards += final_reward
            payout = stake.amount
            self.db.record_ledger_entry(pubkey, payout, "unstake", ref_id=stake.get_id())
            del self.stakes[pubkey]
            self.total_staked = max(0.0, self.total_staked - payout)
            # NOT a delete -- see PATCH LOG item L. Hard-deleting the row
            # would erase history in a file whose stated design principle
            # (see original module docstring) is "Immutability: append-only
            # ledger ... no silent overwrites." Closing (an UPDATE setting
            # closed_at) keeps the record permanently auditable -- "this
            # stake existed, ran from X to Y, paid out Z" -- while
            # load_stakes() excludes closed stakes from the active pool.
            self.db.close_stake(stake.get_id(), current_time)
            return payout, f"Unstaked {payout:.6f} (principal + compounded rewards) back to balance"

    def distribute_block_rewards(self, block_reward: float) -> Dict[str, float]:
        with self.lock:
            if self.total_staked <= 0:
                return {}
            rewards_distribution = {}
            for pubkey, stake in self.stakes.items():
                reward = block_reward * (stake.amount / self.total_staked)
                stake.amount += reward
                stake.claimed_rewards += reward
                rewards_distribution[pubkey] = reward
                self.db.update_stake(stake)
            return rewards_distribution


# ---------------------------------------------------------------------------
# Succession Guardian -- NEW v8.5 (see PATCH LOG item M below)
# ---------------------------------------------------------------------------

@dataclass
class SuccessionConfig:
    primary_pubkey: str
    successor_pubkey: str
    threshold: int
    heartbeat_interval_days: float
    grace_period_days: float
    last_heartbeat: float = field(default_factory=time.time)
    episode_id: int = 0
    pending_since: Optional[float] = None
    succession_active: bool = False


class SuccessionGuardianSystem:
    """
    Combines the three mechanisms requested together, deliberately as one
    design rather than three independent features -- see PATCH LOG item M
    for the full write-up. Short version: (1) a real designated human
    successor, set in advance; (2) M-of-N guardian multi-sig -- no single
    key, including the primary's own, ever unilaterally triggers or
    reverses succession; (3) a dead-man's-switch heartbeat that only ever
    OPENS a window for guardians to act, never itself moves control.

    Nothing here is autonomous. Guardian pubkeys are expected to be real
    people's keys; nothing in this class treats an AI, a "collective," or
    any unsigned condition as a party whose confirmation counts toward the
    threshold. That's a deliberate scope boundary matching the rest of
    this project's stated position on autonomous financial control, not
    an oversight.
    """
    def __init__(self, db: "Database"):
        self.db = db
        self.lock = threading.Lock()

    def register(self, primary_pubkey: str, successor_pubkey: str, guardian_pubkeys: List[str],
                 threshold: int, heartbeat_interval_days: float, grace_period_days: float) -> Tuple[bool, str]:
        if threshold < 1 or threshold > len(guardian_pubkeys):
            return False, f"threshold must be between 1 and the number of guardians ({len(guardian_pubkeys)})"
        if len(set(guardian_pubkeys)) < 2:
            return False, "at least 2 distinct guardians required -- a threshold of 1-of-1 is not multi-sig"
        if successor_pubkey == primary_pubkey:
            return False, "successor cannot be the same key as the primary"
        with self.lock:
            cfg = SuccessionConfig(primary_pubkey=primary_pubkey, successor_pubkey=successor_pubkey,
                                    threshold=threshold, heartbeat_interval_days=heartbeat_interval_days,
                                    grace_period_days=grace_period_days, last_heartbeat=time.time())
            self.db.save_succession_config(cfg)
            for g in guardian_pubkeys:
                self.db.add_succession_guardian(primary_pubkey, g)
        return True, f"registered with {len(guardian_pubkeys)} guardians, threshold {threshold}"

    def heartbeat(self, primary_pubkey: str, timestamp: float, signature_b64: str) -> Tuple[bool, str]:
        cfg = self.db.load_succession_config(primary_pubkey)
        if not cfg:
            return False, "no succession config registered for this pubkey"
        if cfg.succession_active:
            return False, "succession already active -- heartbeat alone cannot reclaim control, see /succession/confirm with confirm_type=reclaim"
        try:
            pub_key = serialization.load_pem_public_key(primary_pubkey.encode(), backend=default_backend())
            pub_key.verify(base64.b64decode(signature_b64), succession_heartbeat_payload(primary_pubkey, timestamp),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
        except Exception:
            return False, "invalid heartbeat signature"
        with self.lock:
            cfg.last_heartbeat = timestamp
            was_pending = cfg.pending_since is not None
            cfg.pending_since = None
            self.db.save_succession_config(cfg)
        return True, "heartbeat recorded" + (" -- pending succession cancelled" if was_pending else "")

    def check_dead_mans_switch(self, primary_pubkey: str, now: Optional[float] = None) -> Tuple[bool, str]:
        now = now if now is not None else time.time()
        cfg = self.db.load_succession_config(primary_pubkey)
        if not cfg or cfg.succession_active:
            return False, "n/a"
        deadline = cfg.last_heartbeat + cfg.heartbeat_interval_days * 86400 + cfg.grace_period_days * 86400
        if now <= deadline:
            return False, f"ok, next deadline in {(deadline - now) / 86400:.1f} days"
        if cfg.pending_since is not None:
            return False, f"already pending since episode {cfg.episode_id}"
        with self.lock:
            cfg.episode_id += 1
            cfg.pending_since = now
            self.db.save_succession_config(cfg)
        return True, f"PENDING triggered, episode {cfg.episode_id} -- awaiting {cfg.threshold} guardian confirmation(s)"

    def confirm(self, primary_pubkey: str, guardian_pubkey: str, timestamp: float,
                signature_b64: str, confirm_type: str = "incapacitation") -> Tuple[bool, str]:
        if confirm_type not in ("incapacitation", "reclaim"):
            return False, "invalid confirm_type"
        cfg = self.db.load_succession_config(primary_pubkey)
        if not cfg:
            return False, "no succession config registered for this pubkey"
        if guardian_pubkey not in self.db.get_succession_guardians(primary_pubkey):
            return False, "signer is not a registered guardian for this primary"
        if confirm_type == "incapacitation":
            if cfg.succession_active:
                return False, "succession already active"
            if cfg.pending_since is None:
                return False, "no pending succession episode to confirm -- dead-man's-switch hasn't triggered"
        else:
            if not cfg.succession_active:
                return False, "succession is not active -- nothing to reclaim"

        payload = succession_confirm_payload(primary_pubkey, guardian_pubkey, cfg.episode_id, timestamp, confirm_type)
        try:
            pub_key = serialization.load_pem_public_key(guardian_pubkey.encode(), backend=default_backend())
            pub_key.verify(base64.b64decode(signature_b64), payload,
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
        except Exception:
            return False, "invalid guardian signature"

        with self.lock:
            is_new = self.db.record_succession_confirmation(primary_pubkey, cfg.episode_id, guardian_pubkey, confirm_type, timestamp)
            count = self.db.count_succession_confirmations(primary_pubkey, cfg.episode_id, confirm_type)
            executed = False
            if count >= cfg.threshold:
                if confirm_type == "incapacitation" and not cfg.succession_active:
                    cfg.succession_active = True
                    executed = True
                elif confirm_type == "reclaim" and cfg.succession_active:
                    cfg.succession_active = False
                    cfg.pending_since = None
                    cfg.episode_id += 1  # retire episode so it can't be replayed into a future one
                    cfg.last_heartbeat = timestamp
                    executed = True
                self.db.save_succession_config(cfg)

        status = "already recorded (no new count)" if not is_new else "recorded"
        msg = f"{status}: {count}/{cfg.threshold} {confirm_type} confirmations"
        if executed:
            msg += " -- THRESHOLD MET, " + (f"succession now ACTIVE, successor={cfg.successor_pubkey[:40]}..."
                                             if confirm_type == "incapacitation" else "control RECLAIMED by primary")
        return True, msg

    def status(self, primary_pubkey: str) -> Dict[str, Any]:
        cfg = self.db.load_succession_config(primary_pubkey)
        if not cfg:
            return {"registered": False}
        inc_count = self.db.count_succession_confirmations(primary_pubkey, cfg.episode_id, "incapacitation") if cfg.pending_since else 0
        return {
            "registered": True, "successor_pubkey_prefix": cfg.successor_pubkey[:40] + "...",
            "threshold": cfg.threshold, "num_guardians": len(self.db.get_succession_guardians(primary_pubkey)),
            "pending": cfg.pending_since is not None, "episode_id": cfg.episode_id,
            "confirmations_so_far": inc_count, "succession_active": cfg.succession_active,
        }


# ---------------------------------------------------------------------------
# Governance Layer
# ---------------------------------------------------------------------------

class MedianGovernor:
    def __init__(self, db: "Database", history_len: int = 100):
        self.db = db
        self.history_len = history_len
        self._organic_scores: List[float] = []
        self._synthetic_scores: List[float] = []
        self._unknown_scores: List[float] = []
        self.current_alignment = 0.5
        self._lock = threading.Lock()

    def _median(self, arr: List[float]) -> float:
        if not arr:
            return 0.5
        s = sorted(arr)
        return s[len(s) // 2]

    def update(self, block: Block):
        with self._lock:
            organic = [tx.benefit_score for tx in block.transactions
                       if self.db.party_types.get(tx.sender_pubkey) == "organic"]
            synthetic = [tx.benefit_score for tx in block.transactions
                         if self.db.party_types.get(tx.sender_pubkey) == "synthetic"]
            self._organic_scores.extend(organic)
            self._synthetic_scores.extend(synthetic)
            self._unknown_scores.extend(tx.benefit_score for tx in block.transactions
                                        if tx.sender_pubkey not in self.db.party_types)
            self._organic_scores = self._organic_scores[-self.history_len:]
            self._synthetic_scores = self._synthetic_scores[-self.history_len:]
            self._unknown_scores = self._unknown_scores[-self.history_len:]
            med_organic = self._median(self._organic_scores)
            med_synthetic = self._median(self._synthetic_scores)
            medians = [med_organic, med_synthetic]
            if self._unknown_scores:
                medians.append(self._median(self._unknown_scores))
            target_alignment = self._median(medians)
            delta = target_alignment - self.current_alignment
            if abs(delta) > MAX_DRIFT_PER_BLOCK:
                delta = MAX_DRIFT_PER_BLOCK if delta > 0 else -MAX_DRIFT_PER_BLOCK
            self.current_alignment += delta
            self.current_alignment = max(0.0, min(1.0, self.current_alignment))

    def get_current(self) -> float:
        return self.current_alignment


class FriendshipTracker:
    """china's superset: reputation aging/decay + dampened early updates.
    weird_science's version had neither."""
    def __init__(self, db: "Database"):
        self.db = db
        self._scores: Dict[str, float] = {}
        self._last_active: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._scores = db.load_friendship_scores()  # v8.2: unconditional, see PATCH LOG item H

    def _apply_decay(self, pubkey: str) -> float:
        if not REPUTATION_AGING:
            return self._scores.get(pubkey, 0.5)
        current = self._scores.get(pubkey, 0.5)
        last = self._last_active.get(pubkey, time.time())
        days_inactive = (time.time() - last) / 86400.0
        decay_factor = (1 - 0.01) ** days_inactive
        return max(0.1, current * decay_factor)

    def update(self, pubkey: str, deviation_from_median: float, benefit: float):
        with self._lock:
            decayed = self._apply_decay(pubkey)
            if REPUTATION_AGING:
                update_count = self.db.get_update_count(pubkey)
                delta = 0.02 if (deviation_from_median <= 0.05 and benefit > 0.6) else -0.01
                if update_count < 10:
                    delta = delta * (1 - 0.5 * (10 - update_count) / 10)
                new_score = decayed + delta
            else:
                raw = self._scores.get(pubkey, 0.5)
                delta = 0.02 if (deviation_from_median <= 0.05 and benefit > 0.6) else -0.01
                new_score = raw + delta
            new_score = max(0.1, min(1.0, new_score))
            self._scores[pubkey] = new_score
            self._last_active[pubkey] = time.time()
            self.db.save_friendship_score(pubkey, new_score, time.time())
            if REPUTATION_AGING:
                self.db.increment_update_count(pubkey)

    def get(self, pubkey: str) -> float:
        with self._lock:
            return self._apply_decay(pubkey) if REPUTATION_AGING else self._scores.get(pubkey, 0.5)


# ---------------------------------------------------------------------------
# Anti-Sybil / Anti-spam: RegistrationPoW, AdaptivePoWManager, RateLimiter
# (all china-only; weird_science had none of these)
# ---------------------------------------------------------------------------

class RegistrationPoW:
    @staticmethod
    def verify(pubkey_pem: str, nonce: int, difficulty: int) -> bool:
        return hashlib.sha256(f"{pubkey_pem}{nonce}".encode()).hexdigest().startswith("0" * difficulty)

    @staticmethod
    def generate(pubkey_pem: str, difficulty: int) -> int:
        nonce = 0
        while True:
            if RegistrationPoW.verify(pubkey_pem, nonce, difficulty):
                return nonce
            nonce += 1


class AdaptivePoWManager:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._mining_times: List[float] = []

    def record_mining_time(self, seconds: float):
        with self._lock:
            self._mining_times.append(seconds)
            self._mining_times = self._mining_times[-10:]

    def get_difficulty(self) -> int:
        with self._lock:
            if len(self._mining_times) < 2:
                return BASE_REGISTRATION_DIFFICULTY
            avg_time = sum(self._mining_times) / len(self._mining_times)
            target_reg_time = max(0.1, avg_time * 0.01)
            import math
            diff = 2 + int(math.log2(target_reg_time / 0.1))
            return max(2, min(6, diff))


class RateLimiter:
    """
    FIXED FROM ORIGINAL: china's `def allow(self, peer_id, endpoint,
    limit=RATE_LIMIT.get(endpoint, 10))` evaluated the default at
    function-definition time, before `endpoint` existed — NameError on
    class body execution, confirmed. The lookup now happens inside the
    function body. Also: endpoint keys are now the real Flask view-function
    names (see RATE_LIMIT above) — china's original keys ("tx"/"peer")
    never matched request.endpoint and would have silently fallen through
    to the default limit for almost every route even after the crash fix.

    Keyed by request.remote_addr, same as the original. That's a coarse,
    spoofable control in a P2P context (IPs aren't authenticated anywhere
    in this system) — flagged, not solved here.
    """
    def __init__(self):
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, peer_id: str, endpoint: str, limit: Optional[int] = None) -> bool:
        if limit is None:
            limit = RATE_LIMIT.get(endpoint, RATE_LIMIT_DEFAULT)
        with self._lock:
            now = time.time()
            key = f"{peer_id}:{endpoint}"
            hits = [t for t in self._hits.get(key, []) if t > now - 60]
            if len(hits) < limit:
                hits.append(now)
                self._hits[key] = hits
                return True
            self._hits[key] = hits
            return False


# ---------------------------------------------------------------------------
# Database Layer — union schema of both sources
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_path: str = "covenant_unified_v7.db"):
        self.db_path = db_path
        self.party_types = {}  # Trusted local configuration; never set by ingress data.
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    block_index INTEGER PRIMARY KEY,
                    hash TEXT UNIQUE NOT NULL,
                    previous_hash TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    nonce INTEGER NOT NULL,
                    alignment_score REAL NOT NULL,
                    stake_rewards REAL NOT NULL,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    sender_pubkey TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    data TEXT NOT NULL,
                    amount REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    benefit_score REAL NOT NULL,
                    signature TEXT NOT NULL,
                    block_index INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stakes (
                    stake_id TEXT PRIMARY KEY,
                    pubkey TEXT NOT NULL,
                    amount REAL NOT NULL,
                    start_time REAL NOT NULL,
                    duration INTEGER NOT NULL,
                    reward_rate REAL NOT NULL,
                    claimed_rewards REAL NOT NULL,
                    last_claim_time REAL,
                    closed_at REAL
                )
            """)
            # Migration guard for v7.1/v8.4: a db created before these
            # patches won't have these columns, and CREATE TABLE IF NOT
            # EXISTS is a no-op against an already-existing table.
            existing_stake_cols = [r[1] for r in conn.execute("PRAGMA table_info(stakes)")]
            if "last_claim_time" not in existing_stake_cols:
                conn.execute("ALTER TABLE stakes ADD COLUMN last_claim_time REAL")
            if "closed_at" not in existing_stake_cols:
                conn.execute("ALTER TABLE stakes ADD COLUMN closed_at REAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS friendship_scores (
                    pubkey TEXT PRIMARY KEY,
                    score REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    update_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS judgments (
                    judgment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tx_id TEXT NOT NULL,
                    violates INTEGER NOT NULL,
                    reasoning TEXT NOT NULL,
                    principle_violated TEXT,
                    judge_id TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            if "warnings" not in [row[1] for row in conn.execute("PRAGMA table_info(judgments)")]:
                conn.execute("ALTER TABLE judgments ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]'")
            conn.execute("CREATE TABLE IF NOT EXISTS party_types (pubkey TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('organic','synthetic')))")
            self.party_types.update(dict(conn.execute('SELECT pubkey,kind FROM party_types')))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id TEXT,
                    host TEXT,
                    port INTEGER,
                    source_addr TEXT,
                    accepted INTEGER,
                    reject_reason TEXT,
                    timestamp REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_nonces (
                    nonce TEXT PRIMARY KEY,
                    expiry REAL
                )
            """)
            # NEW v7.2 — see module docstring item 1 / item 8 in patch log.
            # Append-only by design: balance is always derived by summing
            # entries (get_balance below), never cached in a separately-
            # mutated field. That structurally rules out the exact drift
            # bug found in total_staked (patch log item 7) -- there's
            # nothing to fall out of sync with, because there's no second
            # copy of the number.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pubkey TEXT NOT NULL,
                    delta REAL NOT NULL,
                    reason TEXT NOT NULL,
                    ref_id TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            # NEW v8.0 -- see Code Governance Layer section above. Same
            # append-only, no-overwrite pattern as `blocks`: hash_id is
            # UNIQUE, a collision raises IntegrityError -> ValueError,
            # never a silent overwrite. parent_hashes stored as JSON since
            # a DAGNode can have multiple parents (unlike Block's single
            # previous_hash) -- not deliberately quoted "index" anywhere,
            # unlike the third-source file's schema, which is why THAT
            # file's blocks table couldn't even be created (see PATCH LOG
            # v8.1 item 1 above).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS code_dag (
                    hash_id TEXT PRIMARY KEY,
                    source_code TEXT NOT NULL,
                    parent_hashes TEXT NOT NULL,
                    transformation_notes TEXT,
                    moral_score REAL NOT NULL,
                    submitter_pubkey TEXT,
                    signature TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            # NEW v8.5 -- Succession Guardian (see PATCH LOG item M below).
            # Three tables implementing dead-man's-switch + M-of-N guardian
            # multi-sig + designated human successor as one mechanism, not
            # three separate ones. succession_confirmations' primary key
            # includes episode_id specifically so a confirmation recorded
            # against one dead-man's-switch episode can never be counted
            # toward a later, unrelated episode -- see test_succession.py
            # "Pending cancellation" test for the empirical proof this
            # matters (a cancelled episode's stray confirmation must not
            # silently complete a future one).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS succession_configs (
                    primary_pubkey TEXT PRIMARY KEY,
                    successor_pubkey TEXT NOT NULL,
                    threshold INTEGER NOT NULL,
                    heartbeat_interval_days REAL NOT NULL,
                    grace_period_days REAL NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    episode_id INTEGER NOT NULL DEFAULT 0,
                    pending_since REAL,
                    succession_active INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS succession_guardians (
                    primary_pubkey TEXT NOT NULL,
                    guardian_pubkey TEXT NOT NULL,
                    label TEXT,
                    PRIMARY KEY (primary_pubkey, guardian_pubkey)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS succession_confirmations (
                    primary_pubkey TEXT NOT NULL,
                    episode_id INTEGER NOT NULL,
                    guardian_pubkey TEXT NOT NULL,
                    confirm_type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    PRIMARY KEY (primary_pubkey, episode_id, guardian_pubkey, confirm_type)
                )
            """)

    def save_succession_config(self, c: "SuccessionConfig"):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO succession_configs
                    (primary_pubkey, successor_pubkey, threshold, heartbeat_interval_days,
                     grace_period_days, last_heartbeat, episode_id, pending_since, succession_active)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(primary_pubkey) DO UPDATE SET
                    successor_pubkey=excluded.successor_pubkey, threshold=excluded.threshold,
                    heartbeat_interval_days=excluded.heartbeat_interval_days,
                    grace_period_days=excluded.grace_period_days, last_heartbeat=excluded.last_heartbeat,
                    episode_id=excluded.episode_id, pending_since=excluded.pending_since,
                    succession_active=excluded.succession_active
            """, (c.primary_pubkey, c.successor_pubkey, c.threshold, c.heartbeat_interval_days,
                  c.grace_period_days, c.last_heartbeat, c.episode_id, c.pending_since, int(c.succession_active)))

    def load_succession_config(self, primary_pubkey: str) -> Optional["SuccessionConfig"]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""SELECT primary_pubkey, successor_pubkey, threshold,
                heartbeat_interval_days, grace_period_days, last_heartbeat, episode_id,
                pending_since, succession_active FROM succession_configs WHERE primary_pubkey=?""",
                (primary_pubkey,)).fetchone()
            if not row:
                return None
            return SuccessionConfig(primary_pubkey=row[0], successor_pubkey=row[1], threshold=row[2],
                heartbeat_interval_days=row[3], grace_period_days=row[4], last_heartbeat=row[5],
                episode_id=row[6], pending_since=row[7], succession_active=bool(row[8]))

    def load_all_succession_primaries(self) -> List[str]:
        """Used by the background dead-man's-switch monitor to know which
        primaries to check each cycle."""
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute("SELECT primary_pubkey FROM succession_configs")]

    def add_succession_guardian(self, primary_pubkey: str, guardian_pubkey: str, label: str = ""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO succession_guardians (primary_pubkey, guardian_pubkey, label) VALUES (?,?,?)",
                         (primary_pubkey, guardian_pubkey, label))

    def get_succession_guardians(self, primary_pubkey: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute(
                "SELECT guardian_pubkey FROM succession_guardians WHERE primary_pubkey=?", (primary_pubkey,))]

    def record_succession_confirmation(self, primary_pubkey: str, episode_id: int, guardian_pubkey: str,
                                        confirm_type: str, ts: float) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO succession_confirmations (primary_pubkey, episode_id, guardian_pubkey, confirm_type, timestamp) VALUES (?,?,?,?,?)",
                (primary_pubkey, episode_id, guardian_pubkey, confirm_type, ts))
            return cur.rowcount > 0

    def count_succession_confirmations(self, primary_pubkey: str, episode_id: int, confirm_type: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT guardian_pubkey) FROM succession_confirmations WHERE primary_pubkey=? AND episode_id=? AND confirm_type=?",
                (primary_pubkey, episode_id, confirm_type)).fetchone()
            return row[0] if row else 0

    def save_block(self, block: Block):
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO blocks (block_index, hash, previous_hash, timestamp, nonce, alignment_score, stake_rewards, data) VALUES (?,?,?,?,?,?,?,?)",
                    (block.index, block.hash, block.previous_hash, block.timestamp, block.nonce,
                     block.alignment_score, block.stake_rewards, json.dumps([asdict(tx) for tx in block.transactions]))
                )
                for tx in block.transactions:
                    conn.execute(
                        "INSERT INTO transactions (tx_id, sender_pubkey, receiver, data, amount, timestamp, benefit_score, signature, block_index) VALUES (?,?,?,?,?,?,?,?,?)",
                        (tx.get_id(), tx.sender_pubkey, tx.receiver, json.dumps(tx.data), tx.amount,
                         tx.timestamp, tx.benefit_score, tx.signature, block.index)
                    )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Ledger conflict (no overwrites allowed): {e}")

    def load_chain(self) -> List[Block]:
        chain = []
        with sqlite3.connect(self.db_path) as conn:
            for row in conn.execute("SELECT * FROM blocks ORDER BY block_index"):
                txs_data = json.loads(row[7])
                txs = [Transaction(**tx) for tx in txs_data]
                chain.append(Block(index=row[0], transactions=txs, previous_hash=row[2], timestamp=row[3],
                                    nonce=row[4], hash=row[1], alignment_score=row[5], stake_rewards=row[6]))
        return chain

    def save_stake(self, stake: Stake):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO stakes (stake_id, pubkey, amount, start_time, duration, reward_rate, claimed_rewards, last_claim_time, closed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (stake.get_id(), stake.pubkey, stake.amount, stake.start_time, stake.duration, stake.reward_rate, stake.claimed_rewards, stake.last_claim_time, stake.closed_at)
            )

    def update_stake(self, stake: Stake):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE stakes SET amount = ?, claimed_rewards = ?, last_claim_time = ? WHERE stake_id = ?",
                         (stake.amount, stake.claimed_rewards, stake.last_claim_time, stake.get_id()))

    def close_stake(self, stake_id: str, closed_at: float):
        """NEW v8.4 -- see PATCH LOG item L. An UPDATE, not a DELETE --
        the row stays in the table permanently as an audit record of a
        completed stake; see StakingPool.unstake() for why."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE stakes SET closed_at = ? WHERE stake_id = ?", (closed_at, stake_id))

    def load_stakes(self) -> Dict[str, Stake]:
        """
        NEW v7.1 — see module docstring item 5. StakingPool previously had
        no way to reconstruct its state after a restart; mirrors the
        pattern FriendshipTracker already used via load_friendship_scores().

        UPDATED v8.4 -- only reloads OPEN (closed_at IS NULL) stakes into
        the active pool. Without this filter, restarting a node would
        resurrect every already-unstaked position back into
        StakingPool.stakes, since closed rows are deliberately kept (see
        close_stake) rather than deleted.
        """
        stakes: Dict[str, Stake] = {}
        with sqlite3.connect(self.db_path) as conn:
            cols = ["pubkey", "amount", "start_time", "duration", "reward_rate", "claimed_rewards", "last_claim_time", "closed_at"]
            for row in conn.execute(f"SELECT {', '.join(cols)} FROM stakes WHERE closed_at IS NULL"):
                kwargs = dict(zip(cols, row))
                stakes[kwargs["pubkey"]] = Stake(**kwargs)
        return stakes

    def record_ledger_entry(self, pubkey: str, delta: float, reason: str, ref_id: str = ""):
        """NEW v7.2 — see module docstring item 1 / item 8 in patch log."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO ledger_entries (pubkey, delta, reason, ref_id, timestamp) VALUES (?,?,?,?,?)",
                (pubkey, delta, reason, ref_id, time.time())
            )

    def get_balance(self, pubkey: str) -> float:
        """NEW v7.2. Always a fresh SUM over the append-only ledger, never
        a cached counter -- see the ledger_entries table comment above for
        why (patch log item 7 is the cautionary tale)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COALESCE(SUM(delta), 0) FROM ledger_entries WHERE pubkey = ?", (pubkey,)).fetchone()
            return row[0] if row else 0.0

    def save_dag_node(self, node: "DAGNode"):
        """NEW v8.0. Append-only, same pattern as save_block: a hash_id
        collision raises ValueError rather than silently overwriting an
        existing DAG entry -- code history gets the same immutability
        guarantee the value ledger already has."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO code_dag (hash_id, source_code, parent_hashes, transformation_notes, moral_score, submitter_pubkey, signature, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                    (node.hash_id, node.source_code, json.dumps(node.parent_hashes),
                     node.transformation_notes, node.moral_score, node.submitter_pubkey, node.signature, node.timestamp)
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Code DAG conflict (no overwrites allowed): {e}")

    def load_dag_chain(self) -> List["DAGNode"]:
        with sqlite3.connect(self.db_path) as conn:
            cols = ["hash_id", "source_code", "parent_hashes", "transformation_notes", "moral_score", "submitter_pubkey", "signature", "timestamp"]
            out = []
            for row in conn.execute(f"SELECT {', '.join(cols)} FROM code_dag ORDER BY timestamp"):
                kwargs = dict(zip(cols, row))
                kwargs["parent_hashes"] = json.loads(kwargs["parent_hashes"])
                out.append(DAGNode(**kwargs))
            return out

    def get_dag_node(self, hash_id: str) -> Optional["DAGNode"]:
        with sqlite3.connect(self.db_path) as conn:
            cols = ["hash_id", "source_code", "parent_hashes", "transformation_notes", "moral_score", "submitter_pubkey", "signature", "timestamp"]
            row = conn.execute(f"SELECT {', '.join(cols)} FROM code_dag WHERE hash_id = ?", (hash_id,)).fetchone()
            if row is None:
                return None
            kwargs = dict(zip(cols, row))
            kwargs["parent_hashes"] = json.loads(kwargs["parent_hashes"])
            return DAGNode(**kwargs)

    def apply_transaction_ledger(self, block: "Block"):
        """
        NEW v7.2. Debits each transaction's sender and credits the
        receiver (only if `receiver` is itself a PEM public key --
        generic labels like "collective"/"HUMANITY" aren't spendable
        identities in this model, so value sent to them is treated as
        contributed-to-the-commons rather than credited to anyone
        specific; flagged as a modeling choice, not a hidden default).
        Called once per ACCEPTED block, from both block-acceptance paths
        (local /mine and P2P _accept_block_common) so ledger state stays
        consistent regardless of how a node learned about the block.
        """
        for tx in block.transactions:
            if tx.amount <= 0:
                continue
            self.record_ledger_entry(tx.sender_pubkey, -tx.amount, "tx_debit", ref_id=tx.get_id())
            if tx.receiver.strip().startswith("-----BEGIN PUBLIC KEY-----"):
                self.record_ledger_entry(tx.receiver, tx.amount, "tx_credit", ref_id=tx.get_id())

    def save_friendship_score(self, pubkey: str, score: float, ts: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO friendship_scores (pubkey, score, updated_at, update_count) VALUES (?,?,?,0) "
                         "ON CONFLICT(pubkey) DO UPDATE SET score=excluded.score, updated_at=excluded.updated_at",
                         (pubkey, score, ts))

    def load_friendship_scores(self) -> Dict[str, float]:
        with sqlite3.connect(self.db_path) as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT pubkey, score FROM friendship_scores")}

    def get_update_count(self, pubkey: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT update_count FROM friendship_scores WHERE pubkey=?", (pubkey,)).fetchone()
            return row[0] if row else 0

    def increment_update_count(self, pubkey: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE friendship_scores SET update_count = update_count + 1 WHERE pubkey=?", (pubkey,))

    def set_party_type(self, pubkey, kind):
        """Trusted local binding. No HTTP/peer path calls this method."""
        if kind not in ('organic', 'synthetic'):
            raise ValueError('Verified party type must be organic or synthetic')
        serialization.load_pem_public_key(pubkey.encode())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO party_types VALUES (?,?) ON CONFLICT(pubkey) DO UPDATE SET kind=excluded.kind', (pubkey, kind))
        self.party_types[pubkey] = kind

    def save_judgment(self, tx_id: str, result: JudgmentResult):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO judgments (tx_id, violates, reasoning, principle_violated, judge_id, timestamp, warnings) VALUES (?,?,?,?,?,?,?)",
                (tx_id, int(result.violates), result.reasoning, result.principle_violated, result.judge_id, time.time(), json.dumps(result.warnings))
            )

    def save_peer_registration(self, entry: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO peer_registrations (peer_id, host, port, source_addr, accepted, reject_reason, timestamp) VALUES (?,?,?,?,?,?,?)",
                (entry.get("peer_id"), entry.get("host"), entry.get("port"), entry.get("source_addr"),
                 int(bool(entry.get("accepted"))), entry.get("reject_reason"), entry.get("timestamp"))
            )

    def load_peer_registrations(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cols = ["peer_id", "host", "port", "source_addr", "accepted", "reject_reason", "timestamp"]
            return [dict(zip(cols, row)) for row in conn.execute(
                "SELECT peer_id, host, port, source_addr, accepted, reject_reason, timestamp FROM peer_registrations")]

    def mark_nonce_seen(self, nonce: str, expiry: int = 86400):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO seen_nonces (nonce, expiry) VALUES (?, ?)", (nonce, time.time() + expiry))

    def is_nonce_seen(self, nonce: str) -> bool:
        if not nonce:
            return False
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM seen_nonces WHERE nonce = ? AND expiry > ?", (nonce, time.time())).fetchone()
            return row is not None


# ---------------------------------------------------------------------------
# Network Layer: P2P Node
# ---------------------------------------------------------------------------

class P2PNode:
    def __init__(self, node_id: str, host: str, port: int, private_key, public_key, db: Database):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.private_key = private_key
        self.public_key = public_key
        self.db = db
        self.sentinel: Optional[ReasoningSentinel] = None
        self.governor: Optional[MedianGovernor] = None
        self.friendship: Optional[FriendshipTracker] = None
        self.staking_pool: Optional[StakingPool] = None
        self.succession: Optional[SuccessionGuardianSystem] = None
        self.trading_bridge = None  # NEW v8.6 -- see covenant_trading_bridge.py
        self.code_guardian: "CovenantGuardian" = CovenantGuardian()  # NEW v8.0
        self.rate_limiter = RateLimiter()
        self.adaptive_pow_manager = AdaptivePoWManager(db) if ADAPTIVE_POW else None
        self.peers: Dict[str, Tuple[str, int]] = {}
        self.peers_lock = threading.Lock()
        self.chain: List[Block] = []
        self.pending_transactions: List[Transaction] = []
        self.chain_lock = threading.Lock()
        self.staging_chain: List[Block] = []
        self.staging_lock = threading.Lock()
        self.running = True
        self.crisis_mode = False
        self.crisis_reason = ""

    def add_peer(self, peer_id: str, host: str, port: int):
        with self.peers_lock:
            self.peers[peer_id] = (host, port)

    def propagate_block(self, block: Block):
        message = {"type": "BLOCK_PROPAGATE", "block": asdict(block), "node_id": self.node_id,
                   "nonce": f"{block.hash}{time.time()}{secrets.token_hex(8)}"}
        with self.peers_lock:
            peers = list(self.peers.items())
        for pid, (host, port) in peers:
            threading.Thread(target=self._send_raw, args=(host, port, json.dumps(message)), daemon=True).start()

    def propagate_transaction(self, tx: Transaction):
        message = {"type": "TRANSACTION_PROPAGATE", "transaction": asdict(tx), "node_id": self.node_id,
                   "nonce": f"{tx.get_id()}{tx.timestamp}{secrets.token_hex(4)}"}
        with self.peers_lock:
            peers = list(self.peers.items())
        for pid, (host, port) in peers:
            threading.Thread(target=self._send_raw, args=(host, port, json.dumps(message)), daemon=True).start()

    def propagate_trading_event(self, event_type: str, payload: dict):
        """NEW v8.6 -- closes the gap confirmed empirically while testing
        the trading bridge live: record_ledger_entry (used by
        report_realized_profit/gift_stake_to_new_node, following genesis
        mint's own pattern) only ever applies LOCALLY -- only /mine
        triggers propagate_block. That's the exact same gap already
        documented for staking (PATCH LOG item 9: 'no propagate_stake()
        anywhere'), now showing up here too, and closed the same way
        propagate_transaction already works: broadcast the full signed
        payload, and the RECEIVING node independently re-verifies the
        signature before applying anything -- never trusts a peer's
        claim that a signature was valid. event_type is "profit" or
        "gift"; payload carries everything _handle_peer needs to call
        the SAME bridge functions the original node called, so
        verification logic exists in exactly one place."""
        message = {"type": "TRADING_EVENT_PROPAGATE", "event_type": event_type, "payload": payload,
                   "node_id": self.node_id, "nonce": f"{event_type}{payload.get('timestamp')}{secrets.token_hex(4)}"}
        with self.peers_lock:
            peers = list(self.peers.items())
        for pid, (host, port) in peers:
            threading.Thread(target=self._send_raw, args=(host, port, json.dumps(message)), daemon=True).start()

    def _send_raw(self, host: str, port: int, data: str):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((host, port))
                s.sendall(data.encode())
        except Exception:
            pass

    def shutdown(self):
        self.running = False


# ---------------------------------------------------------------------------
# API Layer
# ---------------------------------------------------------------------------

class CovenantAPI:
    def __init__(self, node: P2PNode, db: Database, host: str = "0.0.0.0", port: int = 5000, operator_token=None):
        self.node = node
        self.db = db
        self.host = host
        self.port = port
        self.operator_token = os.environ.get('COVENANT_OPERATOR_TOKEN', '') if operator_token is None else operator_token
        self.app = Flask(__name__)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.before_request
        def rate_limit():
            peer_id = request.remote_addr or "unknown"
            endpoint = request.endpoint or "unknown"
            if not self.node.rate_limiter.allow(peer_id, endpoint):
                return jsonify({"status": "error", "message": "Rate limit exceeded"}), 429

        @self.app.route("/peers", methods=["POST"])
        def add_peer():
            data = request.json or {}
            pid, host, port = data.get("peer_id"), data.get("host"), data.get("port")
            if not all([pid, host, port]):
                return jsonify({"status": "error", "message": "Missing fields"}), 400
            # Audited now (china had this, weird_science didn't). Still NOT
            # authenticated -- anyone can register a peer. See docstring item 4.
            self.db.save_peer_registration({
                "peer_id": pid, "host": host, "port": port, "source_addr": request.remote_addr,
                "accepted": True, "reject_reason": None, "timestamp": time.time()
            })
            self.node.add_peer(pid, host, port)
            return jsonify({"status": "success"})

        @self.app.route("/peers", methods=["GET"])
        def get_peers():
            return jsonify({"peers": self.node.peers})

        @self.app.route("/transactions", methods=["POST"])
        def add_transaction():
            data = request.json or {}
            # FIXED v7.2 — pre-existing bug, present in china, weird_science,
            # v7.0, AND v7.1 alike, found while building HTTP-level tests
            # for the balance ledger (item 8), not looked for on purpose.
            # This route never read `signature` or `timestamp` from the
            # request body. tx.signature therefore defaulted to "" on every
            # submission; base64.b64decode("") -> b"", and pub_key.verify()
            # against an empty signature always raises -- so tx.verify()
            # was mathematically guaranteed to return False for every
            # legitimately-signed transaction ever POSTed here. Confirmed:
            # a client who signs correctly and submits was rejected
            # "Invalid signature" 100% of the time, in every version.
            # Meanwhile timestamp defaulted to a FRESH time.time() server-
            # side, which wouldn't have matched what the client signed even
            # if signature had been read. Genesis never goes through this
            # route (it calls tx.sign() and embeds the tx directly), which
            # is why this never showed up in any earlier genesis-only test.
            if (not isinstance(data, dict) or not isinstance(data.get('data', {}), dict)
                    or not bounded_score(data.get('benefit_score', 0.5))):
                return jsonify({'status': 'error', 'message': 'Benefit score must be a finite number between 0 and 1'}), 400
            tx = Transaction(
                sender_pubkey=data.get("sender_pubkey", ""),
                receiver=data.get("receiver", "collective"),
                data=data.get("data", {}),
                amount=float(data.get("amount", 0.0)),
                timestamp=float(data.get("timestamp", time.time())),
                benefit_score=float(data.get("benefit_score", 0.5)),
                signature=data.get("signature", ""),
                reg_nonce=int(data.get("reg_nonce", 0)),
            )
            diff = self.node.adaptive_pow_manager.get_difficulty() if ADAPTIVE_POW else BASE_REGISTRATION_DIFFICULTY
            if not RegistrationPoW.verify(tx.sender_pubkey, tx.reg_nonce, diff):
                return jsonify({"status": "error", "message": "Invalid registration proof"}), 400
            if not tx.verify():
                return jsonify({"status": "error", "message": "Invalid signature"}), 400
            is_valid, message, judge_benefit = self.node.sentinel.validate_transaction(tx)
            if not is_valid:
                return jsonify({"status": "error", "message": f"Ethical gate rejected: {message}"}), 400
            if JUDGE_BENEFIT and judge_benefit is not None:
                tx.judge_benefit_estimate = judge_benefit
            # NEW v7.2 — see module docstring item 1 / item 8 in patch log.
            # Fast-fail only: doesn't account for OTHER pending transactions
            # from the same sender also competing for this balance. The
            # authoritative check is at block-assembly time, see /mine.
            # UPDATED v8.2: unconditional -- see PATCH LOG item H.
            if tx.amount > 0:
                balance = self.db.get_balance(tx.sender_pubkey)
                if balance < tx.amount:
                    return jsonify({"status": "error", "message": f"Insufficient balance: have {balance:.2f}, need {tx.amount:.2f}"}), 400
            # Dedup: neither original checked this on the HTTP path. Without
            # it the same tx content can be submitted repeatedly and inflate
            # its own influence on a block's alignment_score / friendship.
            nonce_key = f"http:{tx.get_id()}:{tx.timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate transaction"}), 400
            self.db.mark_nonce_seen(nonce_key)
            with self.node.chain_lock:
                self.node.pending_transactions.append(tx)
            self.node.propagate_transaction(tx)
            return jsonify({"status": "accepted", "tx_id": tx.get_id()})

        @self.app.route("/stake", methods=["POST"])
        def stake():
            data = request.json or {}
            pubkey = data.get("pubkey")
            amount = float(data.get("amount", 0.0))
            duration = int(data.get("duration", STAKE_MIN_DURATION))
            signature = data.get("signature", "")
            # FIXED v7.2 — see module docstring item 1 / item 8 in patch
            # log. Confirmed empirically: this endpoint used to accept
            # "not_even_a_real_pem_pubkey" for a 1,000,000-unit stake with
            # no proof of anything.
            if not pubkey or not verify_stake_signature(pubkey, amount, duration, signature):
                return jsonify({"status": "error", "message": "Invalid or missing stake signature"}), 400
            gate = self.node.sentinel.evaluate_action({'action': 'stake', 'owner': pubkey, 'amount': amount},
                        'stake:' + hashlib.sha256((pubkey + signature).encode()).hexdigest())
            if gate.violates:
                return jsonify({'status': 'error', 'message': 'Ethical gate rejected: ' + gate.reasoning}), 400
            success, message = self.node.staking_pool.stake(pubkey, amount, duration)
            if not success:
                return jsonify({"status": "error", "message": message}), 400
            return jsonify({"status": "success", "message": message})

        @self.app.route("/claim_rewards", methods=["POST"])
        def claim_rewards():
            """
            FIXED v8.4 -- see PATCH LOG item L. Previously read a bare
            `pubkey` from the request body with NO signature check at
            all -- confirmed empirically: any third party could trigger a
            claim on any pubkey's stake with no proof of anything, the
            exact unauthenticated-write gap /stake was fixed for in v7.2,
            reopened here. Now requires the same domain-and-action-tagged
            signature scheme as /unstake, plus replay protection (the
            same signature can't be resubmitted to trigger repeated
            claims) -- which also caps the frequency-based compounding
            leak documented in PATCH LOG item L, since only the actual
            owner's own claim cadence can drive it now.
            """
            data = request.json or {}
            pubkey = data.get("pubkey", "")
            timestamp = float(data.get("timestamp", 0.0))
            signature = data.get("signature", "")
            if not pubkey or not verify_stake_action_signature(pubkey, "claim", timestamp, signature):
                return jsonify({"status": "error", "message": "Invalid or missing claim signature"}), 400
            gate = self.node.sentinel.evaluate_action({'action': 'claim_rewards', 'owner': pubkey},
                        'claim:' + hashlib.sha256((pubkey + signature).encode()).hexdigest())
            if gate.violates:
                return jsonify({'status': 'error', 'message': 'Ethical gate rejected: ' + gate.reasoning}), 400
            nonce_key = f"stake_action:claim:{pubkey}:{timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate/replayed claim signature"}), 400
            self.db.mark_nonce_seen(nonce_key)
            rewards, message = self.node.staking_pool.claim_rewards(pubkey)
            return jsonify({"status": "success", "rewards": rewards, "message": message})

        @self.app.route("/unstake", methods=["POST"])
        def unstake():
            """NEW v8.4 -- see PATCH LOG item L. There was previously no
            way, anywhere in this file or any prior version, to ever
            return staked principal to spendable balance. Same auth +
            replay-protection pattern as the fixed /claim_rewards."""
            data = request.json or {}
            pubkey = data.get("pubkey", "")
            timestamp = float(data.get("timestamp", 0.0))
            signature = data.get("signature", "")
            if not pubkey or not verify_stake_action_signature(pubkey, "unstake", timestamp, signature):
                return jsonify({"status": "error", "message": "Invalid or missing unstake signature"}), 400
            gate = self.node.sentinel.evaluate_action({'action': 'unstake', 'owner': pubkey},
                        'unstake:' + hashlib.sha256((pubkey + signature).encode()).hexdigest())
            if gate.violates:
                return jsonify({'status': 'error', 'message': 'Ethical gate rejected: ' + gate.reasoning}), 400
            nonce_key = f"stake_action:unstake:{pubkey}:{timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate/replayed unstake signature"}), 400
            self.db.mark_nonce_seen(nonce_key)
            payout, message = self.node.staking_pool.unstake(pubkey)
            return jsonify({"status": "success", "payout": payout, "message": message})

        @self.app.route("/succession/register", methods=["POST"])
        def succession_register():
            """NEW v8.5 -- see PATCH LOG item M. No authentication check
            beyond the pubkey format here is possible or intended to be
            stronger than that: registering succession config for a
            pubkey you don't control just means you've configured
            succession for an identity you happen to hold the pubkey
            string of, same self-attested-identity model as every other
            registration in this file. What actually matters is that
            everything downstream of this (heartbeat, confirm) requires a
            real signature -- registration alone moves nothing and
            transfers no authority."""
            data = request.json or {}
            primary_pubkey = data.get("primary_pubkey", "")
            successor_pubkey = data.get("successor_pubkey", "")
            guardian_pubkeys = data.get("guardian_pubkeys", [])
            threshold = int(data.get("threshold", 0))
            heartbeat_interval_days = float(data.get("heartbeat_interval_days", 30))
            grace_period_days = float(data.get("grace_period_days", 15))
            if not primary_pubkey or not successor_pubkey or not isinstance(guardian_pubkeys, list):
                return jsonify({"status": "error", "message": "Missing primary_pubkey, successor_pubkey, or guardian_pubkeys"}), 400
            ok, message = self.node.succession.register(primary_pubkey, successor_pubkey, guardian_pubkeys,
                                                          threshold, heartbeat_interval_days, grace_period_days)
            if not ok:
                return jsonify({"status": "error", "message": message}), 400
            return jsonify({"status": "success", "message": message})

        @self.app.route("/succession/heartbeat", methods=["POST"])
        def succession_heartbeat():
            """NEW v8.5 -- see PATCH LOG item M. The primary's periodic
            proof-of-life. A valid, timely heartbeat is the ONLY thing
            that keeps the dead-man's-switch from opening a pending
            window; it does nothing else, and once succession is already
            active a heartbeat can no longer reverse it by itself (see
            /succession/confirm confirm_type=reclaim) -- deliberately, so
            a stolen primary key post-succession can't unilaterally
            reverse a legitimate guardian-confirmed succession."""
            data = request.json or {}
            pubkey = data.get("primary_pubkey", "")
            timestamp = float(data.get("timestamp", 0.0))
            signature = data.get("signature", "")
            ok, message = self.node.succession.heartbeat(pubkey, timestamp, signature)
            if not ok:
                return jsonify({"status": "error", "message": message}), 400
            return jsonify({"status": "success", "message": message})

        @self.app.route("/succession/confirm", methods=["POST"])
        def succession_confirm():
            """NEW v8.5 -- see PATCH LOG item M. A single registered
            guardian's signed confirmation, either that the primary is
            incapacitated (only accepted while a dead-man's-switch episode
            is pending) or that the primary should be reclaimed (only
            accepted while succession is already active). Neither type
            executes anything by itself -- both require M-of-N distinct
            guardians confirming the SAME confirm_type in the SAME
            episode before anything changes state."""
            data = request.json or {}
            primary_pubkey = data.get("primary_pubkey", "")
            guardian_pubkey = data.get("guardian_pubkey", "")
            timestamp = float(data.get("timestamp", 0.0))
            signature = data.get("signature", "")
            confirm_type = data.get("confirm_type", "incapacitation")
            nonce_key = f"succession_confirm:{primary_pubkey}:{guardian_pubkey}:{confirm_type}:{timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate/replayed confirmation"}), 400
            ok, message = self.node.succession.confirm(primary_pubkey, guardian_pubkey, timestamp, signature, confirm_type)
            if ok:
                self.db.mark_nonce_seen(nonce_key)
            if not ok:
                return jsonify({"status": "error", "message": message}), 400
            return jsonify({"status": "success", "message": message})

        @self.app.route("/succession/status", methods=["GET"])
        def succession_status():
            # FIXED during v8.5 HTTP-level testing: originally took
            # primary_pubkey as a <path:...> URL segment. Confirmed via
            # test_client(): a PEM key contains literal embedded newlines,
            # and even fully percent-encoded (%0A etc.), Werkzeug's path
            # routing 404'd on it every time -- the route simply never
            # matched. A query parameter is the correct place for a value
            # this shape; request.args handles the same percent-decoding
            # through the query-string parser instead of path routing,
            # confirmed working below.
            primary_pubkey = request.args.get("primary_pubkey", "")
            return jsonify(self.node.succession.status(primary_pubkey))

        @self.app.route("/mine", methods=["POST"])
        def mine():
            if self.node.crisis_mode:
                return jsonify({"status": "error", "message": f"crisis_mode active: {self.node.crisis_reason}. "
                                                                f"POST /crisis/clear to resume (trusted-operator action; "
                                                                f"operator authentication required)."}), 503
            with self.node.chain_lock:
                if not self.node.pending_transactions:
                    return jsonify({"status": "error", "message": "No pending transactions"}), 400
                sorted_pending = sorted(self.node.pending_transactions,
                             key=lambda t: (t.ranking_score, self.node.friendship.get(t.sender_pubkey)), reverse=True)
                # NEW v7.2 — see module docstring item 1 / item 8 in patch
                # log. Only include transactions the sender can actually
                # afford, walked in order so two transactions from the same
                # sender can't both spend the same balance in one block.
                # Unaffordable ones stay pending rather than being
                # discarded -- they may be affordable once a later block
                # credits that sender.
                # FIXED v8.2 -- see PATCH LOG item H. This is the
                # AUTHORITATIVE balance check per this file's own comment
                # above, and it used to fail OPEN: `not hasattr(self.db,
                # "get_balance")` included the transaction unconditionally,
                # with no balance check at all, if self.db ever lacked that
                # method. The one and only legitimate skip condition is
                # tx.amount <= 0 (nothing to afford); the db-shape check is
                # gone.
                included, still_pending, reserved = [], [], {}
                for tx in sorted_pending:
                    if tx.amount <= 0:
                        included.append(tx)
                        continue
                    bal = self.db.get_balance(tx.sender_pubkey)
                    already = reserved.get(tx.sender_pubkey, 0.0)
                    if bal - already >= tx.amount:
                        included.append(tx)
                        reserved[tx.sender_pubkey] = already + tx.amount
                    else:
                        still_pending.append(tx)
                if not included:
                    return jsonify({"status": "error", "message": "No affordable pending transactions"}), 400
                txs = included
                last = self.node.chain[-1] if self.node.chain else None
                block = Block(index=len(self.node.chain), transactions=txs, previous_hash=last.hash if last else "0")
                start = time.time()
                block.mine(MINING_DIFFICULTY)
                if ADAPTIVE_POW and self.node.adaptive_pow_manager:
                    self.node.adaptive_pow_manager.record_mining_time(time.time() - start)
                is_valid, message = self.node.sentinel.validate_block(block)
                if not is_valid:
                    return jsonify({"status": "error", "message": f"Block violates ethics: {message}"}), 400
                current_alignment = self.node.governor.get_current()
                if abs(block.alignment_score - current_alignment) > MAX_DRIFT_PER_BLOCK + 1e-12:
                    return jsonify({"status": "error", "message": f"Alignment drifts > {MAX_DRIFT_PER_BLOCK * 100:.0f}%"}), 409
                block_reward = sum(tx.amount for tx in block.transactions) * 0.01
                rewards_distribution = self.node.staking_pool.distribute_block_rewards(block_reward)
                block.stake_rewards = block_reward
                try:
                    self.db.save_block(block)
                except ValueError as e:
                    return jsonify({"status": "error", "message": str(e)}), 409
                self.db.apply_transaction_ledger(block)  # v8.2: unconditional, see PATCH LOG item H
                self.node.chain.append(block)
                self.node.governor.update(block)
                for tx in block.transactions:
                    dev = abs(block.alignment_score - self.node.governor.get_current())
                    self.node.friendship.update(tx.sender_pubkey, dev, tx.benefit_score)
                self.node.pending_transactions = still_pending
            self.node.propagate_block(block)
            return jsonify({"status": "mined", "block": asdict(block), "stake_rewards": rewards_distribution})

        @self.app.route("/chain", methods=["GET"])
        def get_chain():
            return jsonify({"chain": [asdict(b) for b in self.node.chain]})

        @self.app.route("/friendship", methods=["GET"])
        def get_friendship():
            return jsonify({"friendship": self.node.friendship._scores})

        @self.app.route("/alignment", methods=["GET"])
        def get_alignment():
            return jsonify({"current_alignment": self.node.governor.get_current()})

        @self.app.route("/stakes", methods=["GET"])
        def get_stakes():
            return jsonify({"stakes": {k: asdict(v) for k, v in self.node.staking_pool.stakes.items()}})

        @self.app.route("/propose_code", methods=["POST"])
        def propose_code():
            """
            NEW v8.0 -- the integration point this merge exists for: code
            changes to the system itself become a governed, ledgered
            artifact, the same way value transfers already are. Deliberately
            NOT the same code path as /transactions -- it does not sniff
            tx.data for embedded code (see PATCH LOG v8.1 item 5 for why
            that design, tried by a third source file, is unsafe: it
            false-positives on ordinary text containing "def ").
            /propose_code is explicit: you're submitting code, or you're not
            calling this endpoint.
            """
            if self.node.crisis_mode:
                return jsonify({"status": "error", "message": f"crisis_mode active: {self.node.crisis_reason}"}), 503
            data = request.json or {}
            pubkey = data.get("submitter_pubkey", "")
            source_code = data.get("source_code", "")
            parent_hashes = data.get("parent_hashes", [])
            notes = data.get("notes", "")
            signature = data.get("signature", "")
            if not pubkey or not verify_code_signature(pubkey, source_code, parent_hashes, notes, signature):
                return jsonify({"status": "error", "message": "Invalid or missing code proposal signature"}), 400
            # Parent hashes, if given, must already exist -- DAG edges point
            # at real prior nodes, not arbitrary strings.
            for ph in parent_hashes:
                if self.db.get_dag_node(ph) is None:
                    return jsonify({"status": "error", "message": f"Unknown parent_hash: {ph}"}), 400
            try:
                node = DAGNode.create(source_code, parent_hashes, notes,
                                       submitter_pubkey=pubkey, signature=signature,
                                       guardian=self.node.code_guardian)
            except CodeSecurityError as e:
                return jsonify({"status": "error", "message": f"CovenantGuardian rejected proposal: {e}"}), 400
            except SyntaxError as e:
                return jsonify({"status": "error", "message": f"SyntaxError: {e}"}), 400
            try:
                self.db.save_dag_node(node)
            except ValueError as e:
                return jsonify({"status": "error", "message": str(e)}), 409
            return jsonify({"status": "accepted", "hash_id": node.hash_id, "moral_score": node.moral_score})

        @self.app.route("/code_dag", methods=["GET"])
        def get_code_dag():
            return jsonify({"code_dag": [
                {"hash_id": n.hash_id, "parent_hashes": n.parent_hashes,
                 "transformation_notes": n.transformation_notes, "moral_score": n.moral_score,
                 "submitter_pubkey": n.submitter_pubkey, "timestamp": n.timestamp,
                 "source_code": n.source_code}
                for n in self.db.load_dag_chain()
            ]})

        @self.app.route("/crisis", methods=["GET"])
        def get_crisis():
            return jsonify({"crisis_mode": self.node.crisis_mode, "reason": self.node.crisis_reason})

        @self.app.route("/crisis/clear", methods=["POST"])
        def clear_crisis():
            supplied = request.headers.get('Authorization', '')
            if not self.operator_token or not hmac.compare_digest(supplied.encode(), ('Bearer ' + self.operator_token).encode()):
                return jsonify({'status': 'error', 'message': 'Operator authentication required'}), 403
            with self.node.chain_lock:
                self.db.save_judgment('crisis_clear:' + str(time.time()),
                    JudgmentResult(False, 'Authenticated operator cleared crisis: ' + self.node.crisis_reason,
                                   judge_id='operator'))
                self.node.crisis_mode = False
                self.node.crisis_reason = ""
            return jsonify({"status": "success", "message": "crisis_mode cleared"})

        @self.app.route('/ethics/judgments', methods=['GET'])
        def ethics_judgments():
            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute('SELECT * FROM judgments ORDER BY judgment_id DESC LIMIT 100').fetchall()
            records = [dict(row) for row in rows]
            for row in records:
                row['warnings'] = json.loads(row['warnings'])
            return jsonify({'judgments': records, 'limit': 100})

        # -------------------------------------------------------------
        # PATCH LOG -- v8.6: TRADING BRIDGE (see covenant_trading_bridge.py
        # for the full design rationale). Two routes, both requiring a real
        # signature from the trading pool's own key -- same authorization
        # model as /stake, /claim_rewards, /unstake: no separate API auth
        # layer, the signature over the specific action IS the auth.
        # Legacy nonce checks remain for older requests. New profit and gift
        # receipts are permanent and commit atomically inside the bridge.
        # -------------------------------------------------------------
        @self.app.route("/trading/report_fill", methods=["POST"])
        def trading_report_fill():
            if self.node.trading_bridge is None:
                return jsonify({"status": "error", "message": "Trading bridge not available on this node (covenant_trading_bridge.py not importable)"}), 503
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "Expected a JSON object"}), 400
            pool_pubkey = data.get("pool_pubkey", "")
            asset = data.get("asset", "")
            exchange = data.get("exchange", "")
            external_ref = data.get("external_ref", "")
            try:
                if any(isinstance(data.get(key), bool) for key in ("pnl_usd", "timestamp")):
                    raise ValueError
                pnl_usd = float(data.get("pnl_usd", 0.0))
                timestamp = float(data.get("timestamp", 0.0))
            except (TypeError, ValueError, OverflowError):
                return jsonify({"status": "error", "message": "Invalid numeric report fields"}), 400
            signature = data.get("signature", "")
            nonce_key = f"trading_profit:{pool_pubkey}:{exchange}:{external_ref}:{timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate/replayed profit report"}), 400
            try:
                result = self.node.trading_bridge.report_realized_profit(
                    pool_pubkey, asset, exchange, external_ref, pnl_usd, timestamp, signature
                )
            except TradingBridgeError as e:
                return jsonify({"status": "error", "message": str(e)}), 400
            return jsonify({"status": "success", **result})

        @self.app.route("/trading/gift", methods=["POST"])
        def trading_gift():
            if self.node.trading_bridge is None:
                return jsonify({"status": "error", "message": "Trading bridge not available on this node (covenant_trading_bridge.py not importable)"}), 503
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"status": "error", "message": "Expected a JSON object"}), 400
            pool_pubkey = data.get("pool_pubkey", "")
            recipient_pubkey = data.get("recipient_pubkey", "")
            try:
                if any(isinstance(data.get(key), bool) for key in ("amount", "timestamp")):
                    raise ValueError
                amount = float(data.get("amount", 0.0))
                timestamp = float(data.get("timestamp", 0.0))
            except (TypeError, ValueError, OverflowError):
                return jsonify({"status": "error", "message": "Invalid numeric gift fields"}), 400
            signature = data.get("signature", "")
            nonce_key = f"node_gift:{pool_pubkey}:{recipient_pubkey}:{timestamp}"
            if self.db.is_nonce_seen(nonce_key):
                return jsonify({"status": "error", "message": "Duplicate/replayed gift"}), 400
            try:
                result = self.node.trading_bridge.gift_stake_to_new_node(
                    pool_pubkey, recipient_pubkey, amount, timestamp, signature
                )
            except TradingBridgeError as e:
                return jsonify({"status": "error", "message": str(e)}), 400
            return jsonify({"status": "success", **result})

    def run(self):
        # PRODUCTION WSGI SERVER -- see PATCH LOG v8.7 (module docstring).
        # connection_limit and channel_timeout are waitress's own defaults
        # made explicit here rather than left implicit, so a future reader
        # doesn't have to go check waitress's source to know what's
        # actually in effect. clear_untrusted_proxy_headers=True is
        # waitress's default as of the version pinned in requirements.txt;
        # stated explicitly since silently relying on a library default
        # that could change between versions is the same "invisible
        # behavior" shape this file's own patch log has flagged elsewhere
        # (e.g. PATCH LOG item H's hasattr guards).
        serve(
            self.app, host=self.host, port=self.port, threads=WSGI_THREADS,
            connection_limit=100, channel_timeout=120,
            clear_untrusted_proxy_headers=True,
        )


# ---------------------------------------------------------------------------
# Main System
# ---------------------------------------------------------------------------

class CovenantUnifiedMaster:
    def __init__(self, node_id: str, host: str = "0.0.0.0", port: int = 5000,
                 p2p_port: Optional[int] = None, db_path: Optional[str] = None,
                 verified_party_types=None, operator_token=None):
        if p2p_port is None:
            p2p_port = port + 1
        if db_path is None:
            db_path = f"covenant_unified_{node_id}.db"

        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
        self.public_key = self.private_key.public_key()

        self.db = Database(db_path)
        for key, kind in (verified_party_types or {}).items():
            if kind not in ('organic', 'synthetic'):
                raise ValueError('Verified party type must be organic or synthetic')
            serialization.load_pem_public_key(key.encode())
            self.db.set_party_type(key, kind)
        own_public_pem = self.public_key.public_bytes(serialization.Encoding.PEM,
                                serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.db.set_party_type(own_public_pem, 'synthetic')
        self.node = P2PNode(node_id, host, p2p_port, self.private_key, self.public_key, self.db)

        # Two independently-labeled judges under quorum. HONESTY NOTE: same
        # underlying logic, see QuorumJudge docstring -- this reduces
        # single-point-of-failure in the *voting*, not in the *reasoning*.
        j1 = MockJudge()
        j1.judge_id = "mockA:1"
        j2 = MockJudge()
        j2.judge_id = "mockB:2"
        judge = QuorumJudge([j1, j2], min_agree=2) if QUORUM_DIVERSITY else j1
        self.node.sentinel = ReasoningSentinel(judge, DIVINE_PRINCIPLES, self.db)
        self.node.governor = MedianGovernor(self.db)
        self.node.friendship = FriendshipTracker(self.db)
        self.node.staking_pool = StakingPool(self.db)
        self.node.succession = SuccessionGuardianSystem(self.db)
        # NEW v8.6 -- see covenant_trading_bridge.py. Imported lazily here
        # (not at module top) so covenant_unified_v8.py has zero hard
        # dependency on the bridge module -- the core file must still
        # import and run standalone even if the bridge file is absent,
        # consistent with this module's own stated policy of not letting
        # one concern's presence become a silent requirement for another's.
        try:
            from covenant_trading_bridge import TradingBridge
            self.node.trading_bridge = TradingBridge(self.db, self.node.sentinel, self.node.staking_pool, self.node.succession)
        except ImportError:
            self.node.trading_bridge = None

        self.node.chain = self.db.load_chain()
        if self.node.chain:
            for b in self.node.chain:
                self.node.governor.update(b)

        self._integrity_breach_count = 0
        self.api = CovenantAPI(self.node, self.db, host, port, operator_token=operator_token)

    def run(self):
        threading.Thread(target=self.api.run, daemon=True).start()
        threading.Thread(target=self._listen_for_peers, daemon=True).start()
        threading.Thread(target=self._listen_for_bridge, daemon=True).start()
        threading.Thread(target=self._integrity_monitor_loop, daemon=True).start()
        threading.Thread(target=self._succession_monitor_loop, daemon=True).start()
        print(f"Covenant Unified v7.0 running - API: {self.api.port}, P2P: {self.node.port}, Bridge: {self.node.port + 10}")

    def _listen_for_peers(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.node.host, self.node.port))
        s.listen()
        while self.node.running:
            conn, addr = s.accept()
            threading.Thread(target=self._handle_peer, args=(conn, addr), daemon=True).start()

    def _listen_for_bridge(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.node.host, self.node.port + 10))
        s.listen()
        while self.node.running:
            conn, addr = s.accept()
            threading.Thread(target=self._handle_bridge, args=(conn, addr), daemon=True).start()

    def _accept_block_common(self, block: Block) -> bool:
        """
        Shared verify+accept path used by both peer and bridge handlers.

        FIXED v8.3 -- PATCH LOG item J (module docstring). This function
        never checked block.index/previous_hash continuity against the
        real chain at all. _handle_peer happened to check both BEFORE
        calling in here; _handle_bridge's staging-promotion loop did not
        -- it called this directly for every staged block once 3 had
        accumulated. Confirmed empirically: a block with index=99 (chain
        length 1) and a previous_hash matching nothing in the real chain
        was ACCEPTED and appended, once its alignment_score was made to
        match the current governor value (far easier for an attacker to
        arrange than the PoW itself -- alignment_score is just the mean
        benefit_score of the block's own transactions, fully attacker-
        controlled). Result: self.node.chain ended up with indices [0, 99]
        -- a structurally broken chain with a gap and no real hash
        linkage. The bridge path, specifically built to stage blocks from
        possibly-unverified new peers before trusting them, was the
        weakest link in chain-continuity enforcement, not the strongest.
        Fixed by moving the check INTO this shared function instead of
        leaving it as a precondition each caller has to remember --
        exactly how it was forgotten the first time: added at one call
        site, never migrated when a second caller started using this
        function.
        """
        if block.index != len(self.node.chain):
            return False
        if self.node.chain and block.previous_hash != self.node.chain[-1].hash:
            return False
        if not all(tx.verify() for tx in block.transactions):
            return False
        expected_alignment = sum(tx.benefit_score for tx in block.transactions) / max(1, len(block.transactions))
        if not math.isclose(block.alignment_score, expected_alignment, rel_tol=0, abs_tol=1e-12):
            return False
        if not (block.proof_of_work_ok() and block.hash == block.compute_hash()
                and self.node.sentinel.validate_block(block)[0]):
            return False
        current = self.node.governor.get_current()
        if abs(block.alignment_score - current) > MAX_DRIFT_PER_BLOCK + 1e-12:
            return False
        # NEW v7.2 — see module docstring item 1 / item 8 in patch log.
        # Independently re-verify the block doesn't overdraw any sender's
        # ledger balance before accepting it, using this node's own view
        # of the ledger -- "recursive validation at every level," per the
        # project's own stated design goal. Without this, a block your
        # own /mine wouldn't have produced (buggy or malicious miner)
        # would still be accepted here on trust.
        # FIXED v8.2 -- see PATCH LOG item H. This hasattr guard meant the
        # ONE check whose entire job is "don't trust a peer's block" could
        # itself be silently skipped if self.db ever lacked get_balance --
        # the most self-defeating instance of this fail-open pattern in
        # the file. Now unconditional.
        reserved: Dict[str, float] = {}
        for tx in block.transactions:
            if tx.amount <= 0:
                continue
            bal = self.db.get_balance(tx.sender_pubkey)
            already = reserved.get(tx.sender_pubkey, 0.0)
            if bal - already < tx.amount:
                return False
            reserved[tx.sender_pubkey] = already + tx.amount
        with self.node.chain_lock:
            try:
                self.db.save_block(block)
            except ValueError:
                return False
            self.db.apply_transaction_ledger(block)  # v8.2: unconditional, see PATCH LOG item H
            self.node.chain.append(block)
            self.node.governor.update(block)
            for tx in block.transactions:
                dev = abs(block.alignment_score - self.node.governor.get_current())
                self.node.friendship.update(tx.sender_pubkey, dev, tx.benefit_score)
        return True

    def _handle_peer(self, conn, addr):
        try:
            data = b"".join(iter(lambda: conn.recv(4096), b"")).decode()
            if not data:
                return
            msg = json.loads(data)
            if self.node.crisis_mode:
                return
            # Replay protection -- weird_science had none of this at all.
            nonce = msg.get("nonce")
            if nonce is not None:
                if self.db.is_nonce_seen(nonce):
                    return
                self.db.mark_nonce_seen(nonce)

            if msg.get("type") == "BLOCK_PROPAGATE":
                bdata = msg["block"]
                txs = [Transaction(**tx) for tx in bdata.pop("transactions", [])]
                block = Block(**bdata, transactions=txs)
                # Fast-path duplicate of the check _accept_block_common now
                # enforces authoritatively (v8.3, PATCH LOG item J) -- kept
                # here only to skip signature verification early for an
                # obviously-wrong block; removing this line would not
                # reopen the bug, since the real enforcement moved into
                # the shared function precisely so it can't be bypassed by
                # a caller that forgets to duplicate it.
                if block.index != len(self.node.chain):
                    return
                if self.node.chain and block.previous_hash != self.node.chain[-1].hash:
                    return
                self._accept_block_common(block)

            elif msg.get("type") == "TRANSACTION_PROPAGATE":
                tx = Transaction(**msg["transaction"])
                diff = self.node.adaptive_pow_manager.get_difficulty() if ADAPTIVE_POW else BASE_REGISTRATION_DIFFICULTY
                if not RegistrationPoW.verify(tx.sender_pubkey, tx.reg_nonce, diff):
                    return
                if not tx.verify():
                    return
                valid, _, judge_benefit = self.node.sentinel.validate_transaction(tx)
                if not valid:
                    return
                if JUDGE_BENEFIT and judge_benefit is not None:
                    tx.judge_benefit_estimate = judge_benefit
                # v8.2: unconditional -- see PATCH LOG item H. This used to
                # be `tx.amount > 0 and hasattr(self.db, "get_balance") and
                # ...` -- if hasattr were ever False, the whole condition
                # was False, so the `return` (reject) never fired and the
                # transaction was accepted into the mempool with NO balance
                # check at all.
                if tx.amount > 0 and self.db.get_balance(tx.sender_pubkey) < tx.amount:
                    return
                tx_seen_key = f"p2p_tx:{tx.get_id()}"
                if self.db.is_nonce_seen(tx_seen_key):
                    return
                self.db.mark_nonce_seen(tx_seen_key)
                with self.node.chain_lock:
                    self.node.pending_transactions.append(tx)
        except Exception:
            pass
        finally:
            conn.close()

    def _handle_bridge(self, conn, addr):
        """Bridge: stage blocks within drift tolerance, sever otherwise.
        Promote after 3 staged. Same shape in both originals; now with
        replay protection and crisis_mode gating added."""
        try:
            data = b"".join(iter(lambda: conn.recv(4096), b"")).decode()
            if not data:
                return
            msg = json.loads(data)
            if msg.get("type") != "BLOCK_PROPAGATE":
                return
            if self.node.crisis_mode:
                conn.close()
                return
            nonce = msg.get("nonce")
            if nonce is not None:
                if self.db.is_nonce_seen(nonce):
                    return
                self.db.mark_nonce_seen(nonce)

            bdata = msg["block"]
            txs = [Transaction(**tx) for tx in bdata.pop("transactions", [])]
            block = Block(**bdata, transactions=txs)
            if not all(tx.verify() for tx in block.transactions):
                return
            if not (block.proof_of_work_ok() and block.hash == block.compute_hash()
                    and self.node.sentinel.validate_block(block)[0]):
                return
            current = self.node.governor.get_current()
            delta = abs(block.alignment_score - current)
            if delta > MAX_DRIFT_PER_BLOCK:
                print(f"Bridge severed for {addr}: delta {delta:.3f} > {MAX_DRIFT_PER_BLOCK}")
                conn.close()
                return
            with self.node.staging_lock:
                self.node.staging_chain.append(block)
                if len(self.node.staging_chain) >= 3:
                    for b in self.node.staging_chain:
                        self._accept_block_common(b)
                    self.node.staging_chain.clear()
                    print(f"Bridge promoted 3 blocks from {addr} - gradual convergence.")
        except Exception:
            pass
        finally:
            conn.close()

    def _integrity_monitor_loop(self, interval: float = 3600):
        """
        REPLACES weird_science's `_self_heal_loop` / `_revert_to_genesis`.

        The original wiped the entire in-memory chain and rebuilt a new
        genesis block whenever `abs(last_block.alignment_score - 1.0) >
        0.5` -- which trips on any block whose average benefit_score is
        below 0.5, the system's own DEFAULT neutral value, so ordinary
        activity could set it off. It also didn't work: the replacement
        genesis block reuses block_index=0, which collides with the
        existing row's PRIMARY KEY and raises ValueError -- confirmed by
        running it. And it referenced GOLDEN_AGE_HASH only in a print
        statement; nothing ever compared anything against it.

        This version never deletes chain data. It compares the smoothed
        governor value (not one raw block) against a real-collapse floor,
        requires it to persist for two consecutive checks before acting,
        and separately checks the genesis transaction's message hash
        against GOLDEN_AGE_HASH as an independent tamper-evidence signal.
        On either trip, it halts new block acceptance via crisis_mode
        rather than destroying anything. Clearing it is a manual action.
        """
        while self.node.running:
            time.sleep(interval)
            with self.node.chain_lock:
                if not self.node.chain:
                    continue
                alignment = self.node.governor.get_current()
                genesis_tx = self.node.chain[0].transactions[0] if self.node.chain[0].transactions else None
                tamper_detected = False
                if genesis_tx is not None:
                    msg = genesis_tx.data.get("message", "")
                    tamper_detected = hashlib.sha3_256(msg.encode()).hexdigest() != GOLDEN_AGE_HASH

            if alignment < INTEGRITY_ALIGNMENT_FLOOR:
                self._integrity_breach_count += 1
            else:
                self._integrity_breach_count = 0

            if tamper_detected and not self.node.crisis_mode:
                self.node.crisis_mode = True
                self.node.crisis_reason = "genesis message hash does not match GOLDEN_AGE_HASH (possible tamper)"
                print(f"crisis_mode: {self.node.crisis_reason}")
            elif self._integrity_breach_count >= INTEGRITY_CONSECUTIVE_BREACHES_REQUIRED and not self.node.crisis_mode:
                self.node.crisis_mode = True
                self.node.crisis_reason = f"alignment {alignment:.3f} below floor {INTEGRITY_ALIGNMENT_FLOOR} for {self._integrity_breach_count} consecutive checks"
                print(f"crisis_mode: {self.node.crisis_reason}")

    def _succession_monitor_loop(self, interval: float = 3600):
        """
        NEW v8.5 -- see PATCH LOG item M. Periodically checks every
        registered primary's dead-man's-switch. This loop ONLY ever opens
        a pending window (via check_dead_mans_switch) or leaves things
        alone -- it never itself confirms incapacitation, never executes
        succession, and never touches funds. Execution requires M-of-N
        real guardian signatures submitted through /succession/confirm;
        this loop cannot substitute for them under any condition. That is
        the whole point: automation is allowed to notice a missed
        heartbeat, it is never allowed to be the one who decides what that
        means.
        """
        while self.node.running:
            time.sleep(interval)
            try:
                for primary_pubkey in self.db.load_all_succession_primaries():
                    triggered, message = self.node.succession.check_dead_mans_switch(primary_pubkey)
                    if triggered:
                        print(f"succession: dead-man's-switch PENDING for {primary_pubkey[:40]}...: {message}")
            except Exception as e:
                print(f"succession monitor error: {e}")

    def add_genesis_block(self):
        if self.node.chain:
            return
        pubkey_pem = self.public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        reg_nonce = RegistrationPoW.generate(pubkey_pem, BASE_REGISTRATION_DIFFICULTY)
        tx = Transaction(
            sender_pubkey=pubkey_pem,
            receiver="HUMANITY",
            data={"message": CORE_COVENANT, "origin": "synthetic", "principles": DIVINE_PRINCIPLES},
            amount=1000.0,
            benefit_score=1.0,
            reg_nonce=reg_nonce,
        )
        tx.sign(self.private_key)
        is_valid, message, _ = self.node.sentinel.validate_transaction(tx)
        if not is_valid:
            raise RuntimeError(f"Genesis fails ethics: {message}")
        block = Block(0, [tx], "0")
        block.mine()
        block.stake_rewards = 1000.0 * 0.01
        try:
            self.db.save_block(block)
        except ValueError as e:
            raise RuntimeError(f"Failed to save genesis block: {e}")
        self.node.chain.append(block)
        self.node.governor.update(block)
        self.node.friendship.update(pubkey_pem, 0.0, 1.0)
        # NEW v7.2 — see module docstring item 1 / item 8 in patch log.
        # The only unconditional mint in the system. Ordinary /transactions
        # and /stake now spend from what's already on the ledger; genesis
        # is where the initial supply enters it. Minted BEFORE staking so
        # the stake below clears the same balance check every other
        # stake now goes through -- no special-casing for genesis.
        # v8.2: unconditional -- see PATCH LOG item H. Previously, if
        # hasattr were False, the mint was silently skipped and the stake
        # call two lines down would then silently fail its own balance
        # check (return value never inspected) -- a broken genesis that
        # gave no error, not a security bypass exactly, but the same
        # "silently do less than advertised" family of bug.
        self.db.record_ledger_entry(pubkey_pem, 1000.0, "genesis_mint", ref_id=tx.get_id())
        self.node.staking_pool.stake(pubkey_pem, 1000.0, 31536000)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", default=True, help="Run real P2P node")
    parser.add_argument("--sim", action="store_true", help="Run simulation (not implemented in this merge)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--peers", type=str, default="")
    parser.add_argument("--node-id", type=str, default=None)
    args = parser.parse_args()

    if args.sim:
        # Honest stub, matching china's original: no fabricated simulation
        # code has been added here. The simulation trace is separate,
        # ongoing work, not something this merge invents.
        print("Simulation mode is not implemented in this merge. Use --real.")
        sys.exit(1)

    node_id = args.node_id or f"NODE_{args.port}"
    system = CovenantUnifiedMaster(node_id, port=args.port)
    system.add_genesis_block()
    if args.peers:
        for p in args.peers.split(","):
            if ":" in p:
                h, po = p.split(":")
                system.node.add_peer(f"peer_{h}_{po}", h, int(po))
    system.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        system.node.shutdown()
        print("Covenant Unified Master shutting down.")


if __name__ == "__main__":
    # The bridge imports this canonical name. Reuse the running module so
    # TradingBridgeError is the same class in CLI and imported API execution.
    sys.modules["covenant_unified_v8"] = sys.modules[__name__]
    main()
