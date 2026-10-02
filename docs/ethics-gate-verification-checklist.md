# Ethics gate: current verification and limits

Verified 2026-10-02 on the repair branch containing the original Claude
checklist commits and the exchange-evidence fixes. Three independent local
runs each passed **64 tests and 13 subtests**, with no skips or expected
failures. The 64 cases comprise 20 original checklist cases, 27 additional
regressions, and 17 exchange/profit/gift cases. The original baseline reproduced
**9 passed, 11 xfailed** before repairs.

The baseline checklist and its decisions are preserved byte for byte in
[the historical checklist](ethics-gate-verification-checklist-baseline.md).
Its PASS/FAIL tables, line references, and expected results describe commit
`3f9464a`; they are not the status of this branch. Its original executable
fixtures remain available on the immutable
[handed-off commit](https://github.com/LAWLESS1987/Sentinel-Witness/blob/bed9b3ca684c22106f38303dfc55d12eda6f0971/tests/test_ethics_gate_checklist.py).

## What the gate now checks

The configured judge evaluates every supported value action. A halting,
crashing, or malformed judge decision refuses admission. Independently,
`ethics_policy.py` compares an explicit declaration with projected ledger
deltas supplied by the executing node, never caller-written benefit/cost
numbers. For transfers it derives those deltas from the signed amount.
For profit credits it uses the amount established by exchange evidence.

A declaration of mutual ledger benefit contradicted by a negative measured
ledger net halts. An honestly declared one-way gift or payment can proceed
with warnings. Unknown nonfinancial benefit and unobserved costs are reported
as unknown, not zero. Opt-out is unverified and does not itself block or
override a halt. The existing judge's explicit violation decisions still
apply; a warning never overrides a refusal.

The signed structured declaration is:

```json
{"ethics": {"relationship": "mutual_benefit"}}
```

The explicit legacy phrase `mutual benefit` is also recognized after Unicode
normalization and removal of format characters. Common negated mentions are
excluded. This compatibility rule is not general natural-language reasoning.
Paraphrases, services, reciprocal arrangements outside this ledger, and
nonfinancial compensation cannot be reliably measured here. Avoid presenting
an unknown external benefit as a verified financial reciprocal credit.

## Results by original requirement

| Items | Current evidence and scope |
|---|---|
| G1 | Mutual benefit is included in the judge's principle list; original principles remain. |
| G3 | Caller-written `origin` cannot classify a key. Persisted local bindings control the split; unknown keys still participate. This is not real-world identity attestation. |
| G4 | Partial: vocabulary no longer raises the mock estimate. Signed bounded scores remain scheduling inputs, not independently verified benefit. |
| G5, G5b | Invalid/nonfinite/out-of-range/bool scores are rejected; valid transactions still mine. |
| G6, B11 | Transactions, profit credits, gifts, staking, rewards and unstaking honor an ethical refusal before their value mutation. |
| A5 | The supported benefit phrase no longer boosts the estimate or changes signed fields. A configured judge's valid estimate still affects local queue priority. |
| B2, B7 | The original disguised transfer and supported structured/legacy financial declarations are rejected without requiring `_violation`. General semantic deception remains unverified. |
| B5, C3 | Honest gifts/payments and missing opt-out remain admissible when the configured judge permits them. |
| B6, C4 | Caller-provided opt-out cannot launder a measured contradiction. |
| B8, B9 | Refusals preserve tested balances, transaction pools and replay nonces; HTTP, P2P, mining and shared peer/bridge block acceptance are covered. Audit writes are intentional state changes. |
| B10 | Crashed or invalid judge decisions fail closed. An audit-write failure also prevents the tested admission. |
| B12 | Anonymous/wrong-token crisis clearance is refused. Correctly authenticated clearance remains usable and is audited. |
| B13, C1 | Refusals and warnings persist and are readable through `GET /ethics/judgments` (latest 100). Profit/gift admission records commit atomically with their receipts and ledger entries. |
| A6, C2 | Partial: projected ledger net is checked; unseen costs and actual opt-out are explicitly unverified. |
| G2, A1, B1, B4 | No complete independently measured per-party welfare model or separate comprehensive A/B verdicts exist. |
| G7 | No pre-trade exchange execution path exists here; Python gates ledger credits after trades close. |

Warnings are separate from the halt flag. Even an apparently balanced ledger
flow retains warnings about unmeasured nonfinancial effects; the original
truth table's fully verified, warning-free mutual-benefit case cannot be
established by these interfaces.

The financial contradiction rule is direction-independent and also rejects a
supported mutual-benefit declaration when supplied measured effects are
negative. Openly accepted costs without a false mutuality claim only warn.
This does not establish real-world symmetric-harm detection: ordinary transfers
do not provide independently measured welfare for both parties.

## Operator recovery and local party bindings

Set `COVENANT_OPERATOR_TOKEN` through the operator's normal private service
configuration before starting the node, or pass `operator_token` to
`CovenantUnifiedMaster`. There is no default token. `POST /crisis/clear`
requires `Authorization: Bearer <configured token>` and records the recovery
before clearing the flag. Empty configuration, loopback origin, and wrong
tokens do not bypass authentication. If the audit write fails, the flag stays
set. Use a protected transport; token authentication does not provide TLS.
No live service configuration was changed during verification.

`CovenantUnifiedMaster(verified_party_types={public_pem: "organic"})` accepts
trusted local public-key bindings; `"synthetic"` is the other supported label.
`Database.set_party_type(public_pem, kind)` persists an operator-local binding.
The node's own runtime key is classified synthetic. No HTTP or peer message
can call the binding setter. This is local configuration, not biological
identity proof; operators must establish any external evidence themselves.

Signed transaction bytes remain unchanged during admission. Judge estimates
are locally recomputed priority metadata; peer block alignment must match the
mean of the signed bounded scores. Existing balances and history are retained;
the database migration adds warning storage and the local binding table.

## Reproduce the checks

Use an isolated development environment:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest -v test_trading_bridge test_exchange_evidence
python -m pytest tests -v
```

The unittest command covers 17 existing cases; pytest covers 47 ethics cases.
To run all 64 together, including the same unittest cases:

```sh
python -m pytest tests test_trading_bridge.py test_exchange_evidence.py -q
```

The existing GitHub workflow retains the exchange/bridge unittest step and
adds required ethics regressions on Python 3.11 and 3.12. No failures are
masked by xfail markers or removed assertions. The profit gate fixture now
supplies valid signed matched-order evidence so the halt cannot be attributed
to an invalid report. Local verification used Python 3.12.14, generated RSA
keys, temporary SQLite databases, Flask test clients and offline exchange
fixtures. No live keys, accounts, deposits, exchange orders or network services
were used. Live API compatibility remains unverified.

Five separate negative controls deliberately bypassed financial contradiction,
operator authentication, the verified-profit gate, refusal auditing, and signed
payload preservation. Each produced an assertion failure in its corresponding
regression. Those mutations occurred only in temporary test fixtures.

## Remaining limits

Mock judges remain placeholders, and two mock labels are not independent
reasoning. The gate cannot prove all harms, truthful descriptions, voluntary
consent, actual opt-out, biological identity, reciprocal off-ledger services,
or total net benefit for all intelligence. It does not block a real exchange
trade or complete the absent frontend application.

Staking remains local to a node rather than a propagated consensus operation.
This change gates the tested HTTP staking actions; it does not make the
existing staking subsystem transactionally atomic or resolve its historical
cached-total drift and multi-node accounting limits. The complete succession,
peer-registration, malformed-input and live network lifecycle has not been
verified. Public read routes and existing peer networking retain their prior
scope; this is not universal API authentication.

Inspection of the repair introduced no additional outbound destinations or
startup tasks. It added a read-only audit route and authenticated an existing
recovery route. This review and its passing tests do not prove that no backdoors
exist anywhere in the repository or its dependencies. These changes are for
review; no merge, deployment or live-state change was performed.
