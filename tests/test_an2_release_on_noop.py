"""AN2 (A+, 2026-11-04): a NO-OP claim/unstake (nothing credited) releases the signed nonce instead of
burning it, so a premature claim can be retried once it would succeed. A successful claim keeps the nonce
reserved, so a replay is still rejected (AN1). Full reserve+credit crash-atomicity as one DB transaction is a
larger money-core refactor, deliberately not done; the gift path is already one atomic transaction.

Drives the real HTTP handler via the test client.
"""
import covenant_unified_v8 as cv
from test_ethics_gate_checklist import node, _sign   # noqa: F401  (node is a pytest fixture)


def _action_sig(node, action, ts):
    payload = cv._domain_frame(b"COVENANT_STAKE_ACTION_V1", node.human, action, str(ts))
    return _sign(node.human_key, payload)


def test_noop_claim_releases_the_nonce(node):
    ts = 1000.0
    sig = _action_sig(node, "claim", ts)
    key = f"stake_action:claim:{node.human}:{ts}"
    # node.human has no stake -> claim_rewards returns (0, ...) -> a no-op
    r = node.client.post("/claim_rewards", json={"pubkey": node.human, "timestamp": ts, "signature": sig})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["rewards"] == 0
    # the nonce was RELEASED, not burned
    assert node.m.db.is_nonce_seen(key) is False
    # so the SAME signed request is accepted again (not a Duplicate/replayed rejection)
    r2 = node.client.post("/claim_rewards", json={"pubkey": node.human, "timestamp": ts, "signature": sig})
    assert r2.status_code == 200
    assert "Duplicate" not in r2.get_json().get("message", "")


def test_noop_unstake_releases_the_nonce(node):
    ts = 2000.0
    sig = _action_sig(node, "unstake", ts)
    key = f"stake_action:unstake:{node.human}:{ts}"
    r = node.client.post("/unstake", json={"pubkey": node.human, "timestamp": ts, "signature": sig})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["payout"] == 0
    assert node.m.db.is_nonce_seen(key) is False


def test_bad_signature_is_rejected_and_reserves_nothing(node):
    ts = 3000.0
    key = f"stake_action:claim:{node.human}:{ts}"
    r = node.client.post("/claim_rewards", json={"pubkey": node.human, "timestamp": ts, "signature": "not-a-sig"})
    assert r.status_code == 400
    assert node.m.db.is_nonce_seen(key) is False     # a rejected-before-reserve request reserves nothing
