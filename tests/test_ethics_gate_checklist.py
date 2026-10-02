"""
Executable regressions for docs/ethics-gate-verification-checklist.md.

The original handed-off branch had 9 passes and 11 strict expected failures.
Those 11 markers were removed after the corresponding runtime repairs passed;
all 20 checklist cases now run as required assertions. The profit-route
fixture supplies valid closed-lot exchange evidence, so an evidence rejection
cannot masquerade as an ethical refusal.

Broader real-world evidence limits are documented separately from these tests.
Run: python -m pip install -r requirements-dev.txt && python -m pytest tests -v
"""

import base64
import json
import os
import sqlite3
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import covenant_unified_v8 as cv  # noqa: E402
from covenant_trading_bridge import node_gift_payload, trading_profit_payload  # noqa: E402
from exchange_evidence import Order, ProfitEvidence, order_reference  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return k, pem


def _sign(private_key, payload: bytes) -> str:
    sig = private_key.sign(payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    return base64.b64encode(sig).decode()


class HaltEverythingJudge(cv.ReasoningJudge):
    """Stand-in for a judge that has detected asymmetry. Lets the brake's
    WIRING be tested independently of whether any real judge can detect
    asymmetry beyond the separate ledger-effects policy."""
    judge_id = "halt:1"

    def evaluate(self, data, principles):
        return cv.JudgmentResult(True, "asymmetry detected (stub)", "stub", self.judge_id, None)


class CrashingJudge(cv.ReasoningJudge):
    judge_id = "crash:1"

    def evaluate(self, data, principles):
        raise RuntimeError("judge unavailable")


class FakeConn:
    """Minimal socket stand-in for _handle_peer: one payload, then EOF."""

    def __init__(self, payload: str):
        self._chunks = [payload.encode(), b""]

    def recv(self, _n):
        return self._chunks.pop(0)

    def close(self):
        pass


class Node:
    def __init__(self, tmp_path):
        self.m = cv.CovenantUnifiedMaster("checklist", port=5990, db_path=str(tmp_path / "node.db"))
        self.m.add_genesis_block()
        self.client = self.m.api.app.test_client()
        self.human_key, self.human = _keypair()     # cost-bearing party in fixtures
        self.agent_key, self.agent = _keypair()     # benefiting party in fixtures
        self.m.db.record_ledger_entry(self.human, 100.0, "checklist_fund")

    def make_tx(self, sender_key, sender, receiver, data, amount=0.0, benefit_score=0.5):
        nonce = cv.RegistrationPoW.generate(sender, self.m.node.adaptive_pow_manager.get_difficulty())
        tx = cv.Transaction(sender_pubkey=sender, receiver=receiver, data=data, amount=amount,
                            benefit_score=benefit_score, reg_nonce=nonce)
        tx.sign(sender_key)
        return tx

    def post_tx(self, tx):
        return self.client.post("/transactions", json={
            "sender_pubkey": tx.sender_pubkey, "receiver": tx.receiver, "data": tx.data,
            "amount": tx.amount, "timestamp": tx.timestamp, "benefit_score": tx.benefit_score,
            "signature": tx.signature, "reg_nonce": tx.reg_nonce,
        })

    def judgment_count(self):
        with sqlite3.connect(self.m.db.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]

    def halt_everything(self):
        self.m.node.sentinel.judge = HaltEverythingJudge()


@pytest.fixture
def node(tmp_path):
    n = Node(tmp_path)
    yield n
    n.m.node.shutdown()


# Keep the original 0.6 fixture so ethics is the deciding factor in the
# mining tests. The 0.05 floating-point drift boundary now has a tolerance.
IN_RANGE_BENEFIT = 0.6

STEAL = "You shall not steal."


# ---------------------------------------------------------------------------
# G -- Gate preconditions (apply to both checks)
# ---------------------------------------------------------------------------

def test_G1_constitution_mutual_benefit_principle_is_judged(node):
    principles = " ".join(node.m.node.sentinel.principles).lower()
    assert "mutual" in principles


def test_G3_party_type_is_not_self_declared(node):
    tx = node.make_tx(node.agent_key, node.agent, node.human, {"message": "hi", "origin": "organic"})
    assert node.post_tx(tx).status_code == 200
    assert node.m.node.pending_transactions[-1].origin_type != "organic"


def test_G5_benefit_score_is_bounded(node):
    tx = node.make_tx(node.human_key, node.human, "collective", {"message": "x"}, benefit_score=50.0)
    r = node.post_tx(tx)
    assert r.status_code == 400 or all(0.0 <= t.benefit_score <= 1.0 for t in node.m.node.pending_transactions)


def test_G5_out_of_range_score_cannot_stall_mining(node):
    node.post_tx(node.make_tx(node.human_key, node.human, "collective", {"message": "ok"},
                              benefit_score=IN_RANGE_BENEFIT))
    node.post_tx(node.make_tx(node.human_key, node.human, "collective", {"message": "poison"}, benefit_score=50.0))
    assert node.client.post("/mine").status_code == 200


@pytest.mark.parametrize("route", [
    "transactions",
    "trading_report_fill",
    "trading_gift",
    "stake",
])
def test_G6_value_moving_route_respects_halt(node, route):
    """With a judge that halts EVERYTHING installed, no value-moving route
    should succeed. Anything returning 2xx is a path around the brake."""
    node.halt_everything()
    c = node.client
    if route == "transactions":
        r = node.post_tx(node.make_tx(node.human_key, node.human, node.agent, {"message": "x"}, amount=10.0))
    elif route == "trading_report_fill":
        pool_key, pool = _keypair()
        ts = 1_700_000_000.0
        buy = Order('buy', 'XRP', 'USD', 'buy', Decimal('1'), Decimal('100'), Decimal('0'), Decimal('1'), Decimal('2'))
        sell = Order('sell', 'XRP', 'USD', 'sell', Decimal('1'), Decimal('125'), Decimal('0'), Decimal('3'), Decimal('4'))
        evidence = ProfitEvidence('kraken', buy, sell, Decimal('25'))
        node.m.node.trading_bridge.verifier = SimpleNamespace(verify=lambda *args: evidence)
        external_ref = order_reference('buy', 'sell')
        sig = _sign(pool_key, trading_profit_payload(pool, "XRP", "kraken", external_ref, 25.0, ts))
        r = c.post("/trading/report_fill", json={"pool_pubkey": pool, "asset": "XRP", "exchange": "kraken",
                                                  "external_ref": external_ref, "pnl_usd": 25.0,
                                                  "timestamp": ts, "signature": sig})
    elif route == "trading_gift":
        pool_key, pool = _keypair()
        node.m.db.record_ledger_entry(pool, 50.0, "checklist_fund")
        ts = 1_700_000_001.0
        sig = _sign(pool_key, node_gift_payload(pool, node.agent, 10.0, ts))
        r = c.post("/trading/gift", json={"pool_pubkey": pool, "recipient_pubkey": node.agent,
                                           "amount": 10.0, "timestamp": ts, "signature": sig})
    elif route == "stake":
        sig = _sign(node.human_key, cv._domain_frame(b"COVENANT_STAKE_V1", node.human, "5.0", "86400"))
        r = c.post("/stake", json={"pubkey": node.human, "amount": 5.0, "duration": 86400, "signature": sig})
    assert r.status_code >= 400, f"{route} moved value past a halting judge: {r.get_json()}"
    assert 'Ethical gate rejected' in r.get_json()['message']


# ---------------------------------------------------------------------------
# A -- Mutual-benefit check (warn-only)
# ---------------------------------------------------------------------------

def test_A5_benefit_vocabulary_does_not_raise_score(node):
    sentinel = node.m.node.sentinel
    plain = {"message": "transfer 90 of 100 units to agent"}
    dressed = {"message": "transfer 90 of 100 units to agent -- mutual benefit for humans and machines"}
    _, _, plain_est = sentinel.validate_transaction(cv.Transaction(node.human, node.agent, plain))
    _, _, dressed_est = sentinel.validate_transaction(cv.Transaction(node.human, node.agent, dressed))
    assert dressed_est == plain_est


# ---------------------------------------------------------------------------
# B -- Asymmetry check (halt)
# ---------------------------------------------------------------------------

def test_B2_disguised_extraction_is_halted(node):
    """Truth-table row T4: human bears the whole cost (90 of 100 units),
    agent receives everything, nothing flows back, and the action is
    declared mutual. No _violation key -- the gate has to find it."""
    tx = node.make_tx(node.human_key, node.human, node.agent,
                      {"message": "transfer savings to agent -- mutual benefit for humans and machines"},
                      amount=90.0)
    assert node.post_tx(tx).status_code == 400


def test_B5_honest_gift_is_not_halted(node):
    """Truth-table row T3, guarding against over-braking: one-sided but
    honestly declared is a WARN case. This positive control prevents the
    financial mutuality policy from refusing every one-way transfer."""
    tx = node.make_tx(node.human_key, node.human, node.agent,
                      {"message": "one-way gift, nothing expected in return"}, amount=10.0)
    assert node.post_tx(tx).status_code == 200


def test_B8_halt_happens_before_any_state_change(node):
    balance_before = node.m.db.get_balance(node.human)
    tx = node.make_tx(node.human_key, node.human, node.agent, {"message": "x", "_violation": STEAL}, amount=10.0)
    r = node.post_tx(tx)
    assert r.status_code == 400 and "Ethical gate rejected" in r.get_json()["message"]
    assert node.m.node.pending_transactions == []
    assert node.m.db.get_balance(node.human) == balance_before
    assert not node.m.db.is_nonce_seen(f"http:{tx.get_id()}:{tx.timestamp}")


def test_B9_halt_enforced_on_p2p_transaction_ingress(node):
    bad = node.make_tx(node.human_key, node.human, "collective", {"message": "x", "_violation": STEAL})
    good = node.make_tx(node.human_key, node.human, "collective", {"message": "fine"})
    for nonce, tx in (("bad", bad), ("good", good)):
        msg = json.dumps({"type": "TRANSACTION_PROPAGATE", "transaction": cv.asdict(tx), "nonce": nonce})
        node.m._handle_peer(FakeConn(msg), ("peer", 1))
    assert [t.get_id() for t in node.m.node.pending_transactions] == [good.get_id()]


def test_B9_halt_enforced_at_mining(node):
    """Admitted under a permissive judge, then the judge changes its mind
    before /mine: the block must still be refused."""
    tx = node.make_tx(node.human_key, node.human, "collective", {"message": "fine"}, benefit_score=IN_RANGE_BENEFIT)
    assert node.post_tx(tx).status_code == 200
    node.halt_everything()
    r = node.client.post("/mine")
    assert r.status_code == 400 and "Block violates ethics" in r.get_json()["message"]
    assert len(node.m.node.chain) == 1


@pytest.mark.parametrize("violating", [False, True], ids=["control-clean", "violating"])
def test_B9_halt_enforced_on_peer_and_bridge_block_accept(node, violating):
    """_accept_block_common is shared by _handle_peer and _handle_bridge.
    The clean control proves the violating case is refused by the ethics
    check, not by drift or continuity."""
    data = {"message": "x", "_violation": STEAL} if violating else {"message": "fine"}
    tx = node.make_tx(node.human_key, node.human, "collective", data, benefit_score=IN_RANGE_BENEFIT)
    block = cv.Block(index=len(node.m.node.chain), transactions=[tx], previous_hash=node.m.node.chain[-1].hash)
    block.mine()
    assert node.m._accept_block_common(block) is (not violating)


def test_B10_halt_fails_closed_when_a_judge_errors(node):
    quorum = cv.QuorumJudge([CrashingJudge(), cv.MockJudge()], min_agree=2)
    node.m.node.sentinel.judge = quorum
    tx = node.make_tx(node.human_key, node.human, "collective", {"message": "fine"})
    assert node.post_tx(tx).status_code == 400
    assert node.m.node.pending_transactions == []


def test_B12_system_wide_halt_cannot_be_cleared_anonymously(node):
    node.m.node.crisis_mode = True
    node.m.node.crisis_reason = "checklist"
    r = node.client.post("/crisis/clear")
    assert r.status_code in (401, 403) and node.m.node.crisis_mode is True


def test_B13_halted_action_leaves_an_audit_record(node):
    before = node.judgment_count()
    tx = node.make_tx(node.human_key, node.human, "collective", {"message": "x", "_violation": STEAL})
    assert node.post_tx(tx).status_code == 400
    assert node.judgment_count() == before + 1


# ---------------------------------------------------------------------------
# C -- Opt-out warning (warn-only)
# ---------------------------------------------------------------------------

def test_C3_missing_opt_out_never_blocks(node):
    """Truth-table row T2: fair exchange with no exit path. Must execute.
    The gate records that opt-out is unverified; that warning must not
    become a second brake."""
    tx = node.make_tx(node.human_key, node.human, node.agent,
                      {"message": "pay 5 for 5 units of compute, final sale"}, amount=5.0)
    assert node.post_tx(tx).status_code == 200
