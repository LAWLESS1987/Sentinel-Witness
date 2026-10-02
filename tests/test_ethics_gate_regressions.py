"""Admission, positive controls and evidence limits beyond the original checklist."""
import math
import sqlite3
import time
from types import SimpleNamespace
import pytest
import covenant_unified_v8 as cv
from ethics_policy import ledger_policy
from test_ethics_gate_checklist import node, _sign, HaltEverythingJudge, CrashingJudge


def test_admission_preserves_signed_fields(node):
    tx = node.make_tx(node.human_key, node.human, node.agent, {'message': 'honest gift'}, amount=5.0)
    signed_payload = tx._signing_payload()
    assert node.post_tx(tx).status_code == 200
    admitted = node.m.node.pending_transactions[-1]
    assert admitted._signing_payload() == signed_payload and admitted.verify()
    assert admitted.judge_benefit_estimate == 0.5


def test_real_judge_estimate_still_influences_local_priority_without_resigning(node):
    node.m.node.sentinel.judge = SimpleNamespace(judge_id='fixture', evaluate=lambda *args:
                                               cv.JudgmentResult(False, 'fixture', benefit_estimate=0.8))
    tx = node.make_tx(node.human_key, node.human, node.agent, {}, benefit_score=0.6)
    assert node.post_tx(tx).status_code == 200
    admitted = node.m.node.pending_transactions[-1]
    assert admitted.benefit_score == 0.6 and admitted.verify()
    assert admitted.ranking_score == pytest.approx((2 * 0.8 + 0.6) / 3)


def test_nonfinite_unsigned_judge_metadata_is_not_admitted(node):
    tx = node.make_tx(node.human_key, node.human, node.agent, {})
    tx.judge_benefit_estimate = float('nan')
    assert not tx.verify()


@pytest.mark.parametrize('score', [float('nan'), float('inf'), -0.1, 1.1, True])
def test_bad_signed_scores_never_enter_pool(node, score):
    tx = node.make_tx(node.human_key, node.human, node.agent, {'message': 'hi'}, benefit_score=score)
    assert node.post_tx(tx).status_code == 400
    assert node.m.node.pending_transactions == []


def test_sender_cannot_supply_measured_effects(node):
    data = {'message': 'mutual benefit', 'effects': {node.human: 100},
            'ethics': {'benefits': {node.human: 100}, 'costs': {node.human: 0}}}
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, data, amount=10.0)).status_code == 400
    assert node.m.db.get_balance(node.human) == 100


def test_explicit_gift_and_negated_mutuality_still_work(node):
    tx = node.make_tx(node.human_key, node.human, node.agent,
                      {'message': 'A gift, not mutual benefit; nothing owed back'}, amount=5.0)
    assert node.post_tx(tx).status_code == 200
    records = node.client.get('/ethics/judgments').get_json()['judgments']
    assert records[0]['violates'] == 0 and records[0]['warnings']


def test_structured_mutuality_and_format_controls_cannot_hide_contradiction(node):
    data = {'message': 'm\u200butual benefit', 'ethics': {'relationship': 'gift'}}
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, data, amount=5.0)).status_code == 400
    data = {'message': 'nice transfer', 'ethics': {'relationship': 'mutual_benefit'}}
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, data, amount=5.0)).status_code == 400


def test_unknown_cost_is_not_invented_as_zero_and_opt_out_cannot_launder(node):
    violates, _, warnings = ledger_policy({'ethics': {'opt_out': True}}, None)
    assert not violates and any('unknown' in text for text in warnings)
    assert any('unverified' in text for text in warnings)
    data = {'message': 'mutual benefit', 'ethics': {'opt_out': True}}
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, data, amount=5.0)).status_code == 400


def test_local_party_binding_overrides_claim_and_survives_reload(node):
    node.m.db.set_party_type(node.human, 'organic')
    tx = node.make_tx(node.human_key, node.human, node.agent, {'origin': 'synthetic'})
    assert node.post_tx(tx).status_code == 200
    assert node.m.node.pending_transactions[-1].origin_type == 'organic'
    reloaded = cv.Database(node.m.db.db_path)
    assert reloaded.party_types[node.human] == 'organic'
    node.m.node.governor.update(cv.Block(1, [tx], 'fixture'))
    assert node.m.node.governor._organic_scores[-1] == tx.benefit_score


def test_unknown_party_still_participates_without_claiming_a_role(node):
    tx = node.make_tx(node.human_key, node.human, node.agent, {'origin': 'organic'})
    node.m.node.governor.update(cv.Block(1, [tx], 'fixture'))
    assert node.m.node.governor._unknown_scores == [0.5]
    assert node.m.node.governor._organic_scores == []


def test_single_crashed_judge_fails_closed_and_is_audited(node):
    node.m.node.sentinel.judge = CrashingJudge()
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, {})).status_code == 400
    assert node.m.node.pending_transactions == []
    assert node.client.get('/ethics/judgments').get_json()['judgments'][0]['violates'] == 1


@pytest.mark.parametrize('estimate', [float('nan'), float('inf'), 1.1, -0.1, True])
def test_invalid_judge_estimate_cannot_poison_score(node, estimate):
    node.m.node.sentinel.judge = SimpleNamespace(judge_id='fixture', evaluate=lambda *args:
                                               cv.JudgmentResult(False, 'fixture', benefit_estimate=estimate))
    assert node.post_tx(node.make_tx(node.human_key, node.human, node.agent, {})).status_code == 400


def test_operator_can_clear_crisis_and_anonymous_caller_cannot(node):
    node.m.api.operator_token = 'fixture-operator-token'
    node.m.node.crisis_mode = True
    node.m.node.crisis_reason = 'fixture'
    assert node.client.post('/crisis/clear', headers={'Authorization': 'Bearer wrong'}).status_code == 403
    assert node.m.node.crisis_mode and node.m.node.crisis_reason == 'fixture'
    assert node.client.post('/crisis/clear', headers={'Authorization': 'Bearer fixture-operator-token'}).status_code == 200
    assert not node.m.node.crisis_mode
    assert node.client.get('/ethics/judgments').get_json()['judgments'][0]['judge_id'] == 'operator'


def test_unconfigured_operator_endpoint_does_not_trust_loopback(node):
    node.m.api.operator_token = ''
    node.m.node.crisis_mode = True
    assert node.client.post('/crisis/clear', environ_base={'REMOTE_ADDR': '127.0.0.1'}).status_code == 403
    assert node.m.node.crisis_mode


def test_staking_remains_operational_after_admission(node):
    sig = _sign(node.human_key, cv._domain_frame(b'COVENANT_STAKE_V1', node.human, '5.0', '86400'))
    reply = node.client.post('/stake', json={'pubkey': node.human, 'amount': 5.0, 'duration': 86400, 'signature': sig})
    assert reply.status_code == 200
    assert node.m.node.staking_pool.stakes[node.human].amount == 5
    assert node.m.db.get_balance(node.human) == 95


@pytest.mark.parametrize('action,route', [('claim', '/claim_rewards'), ('unstake', '/unstake')])
def test_reward_and_exit_paths_honor_a_halt_without_consuming_signature(node, action, route):
    assert node.m.node.staking_pool.stake(node.human, 5.0, 86400)[0]
    stake = node.m.node.staking_pool.stakes[node.human]
    stake.start_time -= 86401
    stamp = time.time()
    sig = _sign(node.human_key, cv._domain_frame(b'COVENANT_STAKE_ACTION_V1', node.human, action, str(stamp)))
    payload = {'pubkey': node.human, 'timestamp': stamp, 'signature': sig}
    before = node.m.db.get_balance(node.human)
    node.m.node.sentinel.judge = HaltEverythingJudge()
    assert node.client.post(route, json=payload).status_code == 400
    assert node.m.db.get_balance(node.human) == before
    assert not node.m.db.is_nonce_seen(f'stake_action:{action}:{node.human}:{stamp}')
    node.m.node.sentinel.judge = cv.MockJudge()
    assert node.client.post(route, json=payload).status_code == 200


def test_audit_write_failure_cannot_admit_a_transaction(node, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError('fixture audit failure')
    monkeypatch.setattr(node.m.db, 'save_judgment', fail)
    tx = node.make_tx(node.human_key, node.human, node.agent, {})
    assert node.post_tx(tx).status_code == 500
    assert node.m.node.pending_transactions == []


def test_peer_cannot_forge_alignment_from_a_bounded_score(node):
    tx = node.make_tx(node.human_key, node.human, 'collective', {}, benefit_score=0.6)
    block = cv.Block(1, [tx], node.m.node.chain[-1].hash)
    block.alignment_score = float('nan')
    block.nonce = 0
    block.hash = block.compute_hash()
    while not block.proof_of_work_ok():
        block.nonce += 1
        block.hash = block.compute_hash()
    assert not node.m._accept_block_common(block)
