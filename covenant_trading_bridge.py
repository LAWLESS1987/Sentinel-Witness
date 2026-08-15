#!/usr/bin/env python3
"""
covenant_trading_bridge.py -- NEW. Connects the grid/bracket trading
strategy (covenant/strategy/, ledger-app/) to Covenant's real ledger,
ethics gate, and succession systems. Kept as a SEPARATE module rather
than folded into covenant_unified_v8.py itself: that file's own internal
organization already separates concerns into focused classes
(StakingPool, SuccessionGuardianSystem, CovenantGuardian, ...) combined
by CovenantUnifiedMaster -- trading-bridge logic is one more such
concern, not core blockchain/governance machinery, and keeping it
separate means covenant_unified_v8.py's already-long patch-log history
doesn't have to absorb a domain that isn't its own. One small, clearly-
delineated route is added to CovenantAPI (see the PATCH LOG v8.6 block
in covenant_unified_v8.py) so this bridge is reachable over the same
running API, not a second server to operate.

WHY PROFIT, NOT EVERY FILL, IS WHAT GETS RECORDED: a BUY converts USD
held at an exchange into an asset held at the same exchange -- it isn't
new value entering the system, it's an external asset swap Covenant has
no natural way to represent (the existing Transaction model moves
EXISTING sender balance to a receiver; a buy has no Covenant-side
"sender" with that balance, because the balance lives at Kraken/Coinbase/
Crypto.com, not on this ledger). A completed round-trip SELL is
different: run_grid_backtest's own fill discipline (grid_engine.py) only
closes a position above its own cost basis, so a realized profit is a
genuine new economic fact, structurally analogous to genesis's one-time
mint -- not a transfer between two Covenant parties. Modeled that way
here (record_ledger_entry with reason="trading_profit", gated by a real
signature) rather than forced through the peer-to-peer Transaction/mining
pipeline, which would need a fictional zero-balance "sender" and either
fail its own balance check or require yet another hasattr-style special
case -- exactly the fail-open shape this codebase's own patch log (items
H, and generally) has spent several rounds closing elsewhere. Consistent
with genesis's mint, but NOT unconditional the way genesis's one-time
bootstrap mint is: every credit here requires a real signature from the
pool's own key, verified before the ledger entry is written.

WHY A GIFT IS A LEDGER CREDIT, NOT AN AUTO-STAKE: Covenant's own /stake
route requires the STAKER's own signature (verify_stake_signature) --
auto-staking on a new node's behalf using the POOL's authority would
mean staking without the actual keyholder's consent, a real regression
against a pattern this file enforces everywhere else. A gift here credits
the recipient's spendable balance; if they want it staked, they submit
their own signed /stake call, same as anyone else.

SCOPE BOUNDARY, STATED PLAINLY: SuccessionGuardianSystem registration
below covers who is authorized to sign FUTURE Covenant-ledger entries for
the trading pool -- a software/cryptographic fact this code can actually
enforce. It does NOT and cannot transfer control of the real exchange
accounts (Coinbase/Kraken/Crypto.com) or the physical Ledger hardware
wallet those accounts' keys ultimately depend on. That is real-world
estate planning (a will, a secured note with recovery details, whatever
the person's jurisdiction requires) outside what any code here can
enforce. Do not let a clean succession registration on this side create
the impression the whole operation's succession is handled -- it is one
necessary piece, not the whole picture.
"""

import sys
import time
sys.path.insert(0, "/home/claude/covenant")

from covenant_unified_v8 import (
    _domain_frame, Database, StakingPool, SuccessionGuardianSystem,
    ReasoningSentinel, JudgmentResult, TradingBridgeError,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64


def trading_profit_payload(pool_pubkey: str, asset: str, exchange: str, external_ref: str,
                            pnl_usd: float, timestamp: float) -> bytes:
    return _domain_frame(b"COVENANT_TRADING_PROFIT_V1", pool_pubkey, asset, exchange,
                          external_ref, str(pnl_usd), str(timestamp))


def verify_trading_profit_signature(pool_pubkey_pem: str, asset: str, exchange: str, external_ref: str,
                                     pnl_usd: float, timestamp: float, signature_b64: str) -> bool:
    try:
        payload = trading_profit_payload(pool_pubkey_pem, asset, exchange, external_ref, pnl_usd, timestamp)
        pub_key = serialization.load_pem_public_key(pool_pubkey_pem.encode(), backend=default_backend())
        pub_key.verify(
            base64.b64decode(signature_b64), payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def node_gift_payload(pool_pubkey: str, recipient_pubkey: str, amount: float, timestamp: float) -> bytes:
    return _domain_frame(b"COVENANT_NODE_GIFT_V1", pool_pubkey, recipient_pubkey, str(amount), str(timestamp))


def verify_node_gift_signature(pool_pubkey_pem: str, recipient_pubkey: str, amount: float,
                                timestamp: float, signature_b64: str) -> bool:
    try:
        payload = node_gift_payload(pool_pubkey_pem, recipient_pubkey, amount, timestamp)
        pub_key = serialization.load_pem_public_key(pool_pubkey_pem.encode(), backend=default_backend())
        pub_key.verify(
            base64.b64decode(signature_b64), payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


class TradingBridge:
    """
    Wraps a running Covenant node's db/sentinel/staking_pool/succession
    with the two trading-specific operations (report_realized_profit,
    gift_stake_to_new_node) plus a succession-registration convenience
    wrapper. Constructed with the SAME objects CovenantUnifiedMaster
    already built -- this class adds behavior, it does not stand up a
    second, parallel copy of the ledger/db/succession system.
    """

    def __init__(self, db: Database, sentinel: ReasoningSentinel, staking_pool: StakingPool,
                 succession: SuccessionGuardianSystem):
        self.db = db
        self.sentinel = sentinel
        self.staking_pool = staking_pool
        self.succession = succession

    def report_realized_profit(self, pool_pubkey_pem: str, asset: str, exchange: str, external_ref: str,
                                pnl_usd: float, timestamp: float, signature_b64: str) -> dict:
        """
        Credits `pnl_usd` to the trading pool's Covenant balance, gated
        by a real signature over (pool_pubkey, asset, exchange,
        external_ref, pnl_usd, timestamp) -- see module docstring for why
        this is a signed mint-style credit, not a peer-to-peer
        Transaction. `external_ref` (e.g. an exchange order id) plus
        timestamp double as replay protection via the caller's own
        nonce-seen table, same pattern as every other signed action in
        this codebase (see /claim_rewards, /unstake) -- enforced at the
        API route (see PATCH LOG v8.6 in covenant_unified_v8.py), not
        re-implemented here, so there's one nonce-checking pattern in the
        system, not two slightly-different ones.
        """
        if pnl_usd <= 0:
            raise TradingBridgeError(
                f"report_realized_profit called with pnl_usd={pnl_usd} <= 0 -- this bridge only "
                f"records REALIZED PROFIT (see module docstring); a non-positive value means either "
                f"a losing trade (which this strategy's take-profit gating should make structurally "
                f"impossible for a CLOSED trade -- see grid_engine.py's own note on this) or a caller "
                f"bug. Refusing rather than writing a nonsensical ledger entry."
            )
        if not verify_trading_profit_signature(pool_pubkey_pem, asset, exchange, external_ref,
                                                 pnl_usd, timestamp, signature_b64):
            raise TradingBridgeError("Invalid trading-profit signature -- refusing to credit the ledger.")

        # Audit-trail judgment, NOT a hard gate -- see module docstring on
        # why this is soft here specifically. A profit report has no
        # peer-to-peer "sender" content to judge for the kind of thing
        # MockJudge's keyword scan is even meant to catch; logging the
        # judgment keeps the same audit pattern the rest of the system
        # uses without pretending a hard gate here would mean something
        # it doesn't.
        judgment = self.sentinel.judge.evaluate(
            {"origin": "trading_bracket_grid", "asset": asset, "exchange": exchange,
             "external_ref": external_ref, "pnl_usd": pnl_usd},
            self.sentinel.principles,
        )
        ref_id = f"trading_profit:{exchange}:{external_ref}"
        self.db.save_judgment(ref_id, judgment)
        self.db.record_ledger_entry(pool_pubkey_pem, pnl_usd, "trading_profit", ref_id=ref_id)

        return {
            "credited": pnl_usd, "new_balance": self.db.get_balance(pool_pubkey_pem),
            "judgment": judgment.reasoning, "ref_id": ref_id,
        }

    def gift_stake_to_new_node(self, pool_pubkey_pem: str, recipient_pubkey_pem: str, amount: float,
                                timestamp: float, signature_b64: str) -> dict:
        """
        Non-usurious by construction: a straight ledger credit, zero
        consideration, no interest, no repayment obligation -- reason
        string is "node_gift", deliberately distinct from "stake_lock"
        or anything loan-shaped. Requires the POOL's real signature
        (proving the operator authorized this specific gift, to this
        specific recipient, for this specific amount) -- an attacker who
        merely knows a recipient pubkey cannot drain the pool by calling
        this. Does NOT stake on the recipient's behalf -- see module
        docstring; they stake it themselves via the existing signed
        /stake route if they choose to.
        """
        if amount <= 0:
            raise TradingBridgeError("Gift amount must be positive.")
        if not verify_node_gift_signature(pool_pubkey_pem, recipient_pubkey_pem, amount, timestamp, signature_b64):
            raise TradingBridgeError("Invalid node-gift signature -- refusing to move funds.")
        balance = self.db.get_balance(pool_pubkey_pem)
        if balance < amount:
            raise TradingBridgeError(f"Insufficient pool balance: have {balance:.2f}, need {amount:.2f}.")

        ref_id = f"node_gift:{pool_pubkey_pem[:16]}:{recipient_pubkey_pem[:16]}:{timestamp}"
        self.db.record_ledger_entry(pool_pubkey_pem, -amount, "node_gift_sent", ref_id=ref_id)
        self.db.record_ledger_entry(recipient_pubkey_pem, amount, "node_gift_received", ref_id=ref_id)

        return {
            "gifted": amount, "pool_balance_after": self.db.get_balance(pool_pubkey_pem),
            "recipient_balance_after": self.db.get_balance(recipient_pubkey_pem), "ref_id": ref_id,
        }

    def register_pool_succession(self, pool_pubkey_pem: str, successor_pubkey_pem: str,
                                  guardian_pubkeys: list, threshold: int,
                                  heartbeat_interval_days: float = 30, grace_period_days: float = 15) -> tuple:
        """Thin, documented wrapper -- see module docstring's SCOPE
        BOUNDARY before treating this as complete succession coverage
        for the trading operation as a whole."""
        return self.succession.register(
            pool_pubkey_pem, successor_pubkey_pem, guardian_pubkeys,
            threshold, heartbeat_interval_days, grace_period_days,
        )
