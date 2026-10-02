"""Exchange-evidenced local ledger credits and signed gifts.

Credits are local accounting entries, not exchange deposits or proof of reserves.
Succession only concerns ledger authorization, not external account ownership.
"""

import time
import sqlite3
import json
import hashlib
from contextlib import closing
from decimal import Decimal
from exchange_evidence import ConfiguredVerifier, EvidenceError, number, pool_fingerprint

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
                 succession: SuccessionGuardianSystem, verifier=None):
        self.db = db
        if sentinel.db is None:
            sentinel.db = db
        self.sentinel = sentinel
        self.staking_pool = staking_pool
        self.succession = succession

        self.configuration_error = None
        try:
            self.verifier = verifier if verifier is not None else ConfiguredVerifier.from_environment()
        except EvidenceError as exc:
            self.verifier = ConfiguredVerifier()
            self.configuration_error = str(exc)
        with closing(sqlite3.connect(self.db.db_path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS trading_used_orders (exchange TEXT NOT NULL, order_id TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(exchange, order_id))")
            conn.execute("CREATE TABLE IF NOT EXISTS trading_receipts (receipt TEXT PRIMARY KEY, evidence TEXT NOT NULL, timestamp REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS trading_gifts (receipt TEXT PRIMARY KEY)")

    @staticmethod
    def _amount(value):
        try:
            amount = number(value, 'amount', positive=True)
            if Decimal(str(float(amount))) != amount:
                raise EvidenceError('Amount cannot be represented by this ledger')
            return amount
        except EvidenceError as exc:
            raise TradingBridgeError(str(exc)) from None

    def report_realized_profit(self, pool_pubkey_pem: str, asset: str, exchange: str, external_ref: str,
                                pnl_usd: float, timestamp: float, signature_b64: str) -> dict:
        """Authenticate, independently calculate profit, and atomically consume orders."""
        claimed = self._amount(pnl_usd)
        self._amount(timestamp)
        if not verify_trading_profit_signature(pool_pubkey_pem, asset, exchange, external_ref,
                                               pnl_usd, timestamp, signature_b64):
            raise TradingBridgeError('Invalid trading-profit signature')
        try:
            if self.configuration_error:
                raise EvidenceError(self.configuration_error)
            evidence = self.verifier.verify(pool_pubkey_pem, asset, exchange, external_ref)
            if claimed != evidence.pnl:
                raise EvidenceError('Reported profit does not equal exchange proceeds minus cost and fees')
        except EvidenceError as exc:
            raise TradingBridgeError(str(exc)) from None
        record = json.dumps(evidence.record(), sort_keys=True, separators=(',', ':'))
        ref_id = 'trading_profit:' + hashlib.sha256(record.encode()).hexdigest()
        judgment = self.sentinel.evaluate_action(evidence.record(), ref_id,
                                                {pool_pubkey_pem: float(claimed)}, record=False)
        if judgment.violates:
            raise TradingBridgeError('Ethical gate rejected: ' + judgment.reasoning)
        try:
            with closing(sqlite3.connect(self.db.db_path, timeout=30)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                # Older bridge credits had no authenticated order receipt. Never
                # silently re-credit the same legacy external reference.
                legacy_refs = [f'trading_profit:{exchange}:{ref}' for ref in
                               (external_ref, evidence.buy.order_id, evidence.sell.order_id)]
                if conn.execute('SELECT 1 FROM ledger_entries WHERE reason=? AND ref_id IN (?,?,?)',
                                ('trading_profit', *legacy_refs)).fetchone():
                    raise TradingBridgeError('This report already has a legacy ledger credit')
                conn.execute('INSERT INTO trading_receipts VALUES (?,?,?)', (ref_id, record, time.time()))
                for order in (evidence.buy, evidence.sell):
                    conn.execute('INSERT INTO trading_used_orders VALUES (?,?,?)', (exchange, order.order_id, ref_id))
                conn.execute('INSERT INTO judgments (tx_id, violates, reasoning, principle_violated, judge_id, timestamp, warnings) VALUES (?,?,?,?,?,?,?)',
                             (ref_id, int(judgment.violates), judgment.reasoning, judgment.principle_violated, judgment.judge_id, time.time(), json.dumps(judgment.warnings)))
                conn.execute('INSERT INTO ledger_entries (pubkey,delta,reason,ref_id,timestamp) VALUES (?,?,?,?,?)',
                             (pool_pubkey_pem, float(claimed), 'trading_profit', ref_id, time.time()))
                balance = conn.execute('SELECT COALESCE(SUM(delta),0) FROM ledger_entries WHERE pubkey=?', (pool_pubkey_pem,)).fetchone()[0]
        except sqlite3.IntegrityError:
            raise TradingBridgeError('Order already credited or ledger constraint rejected the report') from None
        return {'credited': float(claimed), 'new_balance': balance, 'judgment': judgment.reasoning,
                'warnings': judgment.warnings, 'ref_id': ref_id}

    def gift_stake_to_new_node(self, pool_pubkey_pem: str, recipient_pubkey_pem: str, amount: float,
                                timestamp: float, signature_b64: str) -> dict:
        """Move a signed gift in one transaction, with permanent replay protection."""
        value = float(self._amount(amount))
        self._amount(timestamp)
        try:
            pool_fingerprint(recipient_pubkey_pem)
        except EvidenceError as exc:
            raise TradingBridgeError(str(exc)) from None
        if not verify_node_gift_signature(pool_pubkey_pem, recipient_pubkey_pem, amount, timestamp, signature_b64):
            raise TradingBridgeError('Invalid node-gift signature')
        ref_id = 'node_gift:' + hashlib.sha256(node_gift_payload(pool_pubkey_pem, recipient_pubkey_pem, amount, timestamp)).hexdigest()
        effects = {pool_pubkey_pem: -value}
        effects[recipient_pubkey_pem] = effects.get(recipient_pubkey_pem, 0) + value
        judgment = self.sentinel.evaluate_action({'action': 'gift', 'ethics': {'relationship': 'gift'}},
                                                ref_id, effects, record=False)
        if judgment.violates:
            raise TradingBridgeError('Ethical gate rejected: ' + judgment.reasoning)
        try:
            with closing(sqlite3.connect(self.db.db_path, timeout=30)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                legacy_ref = f'node_gift:{pool_pubkey_pem[:16]}:{recipient_pubkey_pem[:16]}:{timestamp}'
                if conn.execute('SELECT 1 FROM ledger_entries WHERE reason=? AND ref_id=?', ('node_gift_sent', legacy_ref)).fetchone():
                    raise TradingBridgeError('This gift already has a legacy ledger entry')
                conn.execute('INSERT INTO trading_gifts VALUES (?)', (ref_id,))
                balance = conn.execute('SELECT COALESCE(SUM(delta),0) FROM ledger_entries WHERE pubkey=?', (pool_pubkey_pem,)).fetchone()[0]
                if balance < value:
                    raise TradingBridgeError('Insufficient pool balance')
                for pubkey, delta, reason in ((pool_pubkey_pem, -value, 'node_gift_sent'), (recipient_pubkey_pem, value, 'node_gift_received')):
                    conn.execute('INSERT INTO ledger_entries (pubkey,delta,reason,ref_id,timestamp) VALUES (?,?,?,?,?)', (pubkey, delta, reason, ref_id, time.time()))
                conn.execute('INSERT INTO judgments (tx_id, violates, reasoning, principle_violated, judge_id, timestamp, warnings) VALUES (?,?,?,?,?,?,?)',
                             (ref_id, int(judgment.violates), judgment.reasoning, judgment.principle_violated, judgment.judge_id, time.time(), json.dumps(judgment.warnings)))
                balances = [conn.execute('SELECT COALESCE(SUM(delta),0) FROM ledger_entries WHERE pubkey=?', (key,)).fetchone()[0] for key in (pool_pubkey_pem, recipient_pubkey_pem)]
        except sqlite3.IntegrityError:
            raise TradingBridgeError('Gift already processed or ledger constraint rejected the gift') from None
        return {'gifted': value, 'pool_balance_after': balances[0], 'recipient_balance_after': balances[1],
                'warnings': judgment.warnings, 'ref_id': ref_id}

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
