# Sentinel-Witness

This repository contains a Python Covenant ledger node, a trading-report bridge,
and React dashboard components. It does not currently contain a complete
buildable PWA. The original inventory had eight tracked files at
commit `3f9464a890ea6ea097e933c42b48a2f058925100` on 2026-10-01.

## What is present

| File | Role in this checkout |
|---|---|
| `covenant_unified_v8.py` | Ledger, peer networking, HTTP API, staking, and succession implementation |
| `covenant_trading_bridge.py` | Verified profit credits, atomic signed gifts, and succession registration |
| `exchange_evidence.py` | Read-only order lookups and closed-lot profit calculation |
| `test_trading_bridge.py`, `test_exchange_evidence.py` | Offline regression tests |
| `Dashboard.jsx` | Dashboard component consuming a gate supplied by its caller |
| `AutomatedSetupModal.jsx` | Form for per-trade and daily-count limits |
| `automatedLimits.js`, `tierNavigation.js` | Frontend validation and tier-navigation helpers |
| `requirements.txt` | Python dependencies |
| `ethics_policy.py`, `tests/` | Ledger-effect admission policy and ethics regressions |
| `requirements-dev.txt` | Development/test dependencies |

## What the trading bridge establishes

The profit-report path authenticates the pool signature and retrieves both
orders from a node-configured exchange account. It calculates proceeds minus
cost and trading fees, requires the reported amount to match, and commits the
credit with permanent order receipts in one database transaction. Reusing an
order with a new timestamp does not create another credit.

The gift path authenticates the pool signature and transfers balances in one
transaction, with permanent replay protection and a serialized balance check.
It does not automatically stake for the recipient.

See [profit verification setup and limits](docs/PROFIT_VERIFICATION.md) for the
required account binding and signed buy/sell order reference. These credits
are local accounting entries, not exchange deposits or proof of reserves.

Succession concerns authorization within this ledger. It does not transfer
exchange accounts or a hardware wallet. The default node constructs mock
judges; their results are not evidence of semantic safety.

## Ethics admission and recovery

The gate now enforces refusals on the tested value paths, records warnings and
halts, and requires a configured operator token to clear crisis mode. Honest
gifts and permitted staking/reward/exit actions remain available. Signed scores
are preserved; judge estimates affect local queue priority without invalidating
signatures. See the [current ethics verification checklist](docs/ethics-gate-verification-checklist.md)
for operator configuration, 64 offline cases, and the remaining evidence limits.
The original Claude checklist remains available as a dated historical record.

## Local inspection

Use an isolated Python environment, install the declared dependencies, then
inspect the node's command-line help:

```sh
python -m venv .venv
# Activate .venv for your shell before the following commands.
python -m pip install -r requirements.txt
python covenant_unified_v8.py --help
```

The node entry point offers `--port`, `--peers`, and `--node-id`. Starting the
node creates local state and starts services. `--sim` explicitly reports that
simulation mode is not implemented. Starting the node does not verify exchange access; a profit report performs authenticated order lookups.

## Frontend completeness

The checkout has no `package.json`, application entry point, Vite configuration,
frontend exchange execution adapters, Ledger adapter, or `tradeGate.js`. The JSX
imports use a larger project's component/library layout that is absent here.
Consequently the previously documented `npm install`, `npm test`, and
`npm run build` instructions cannot be used to build or test this checkout.
The dashboard's labels describe intended capabilities; they do not establish
that order execution or Ledger confirmation is implemented in this repository.

## Verification and limits

The original audit reproduced the defect: a signed synthetic report credited
the ledger without exchange evidence. The replacement includes offline
regression tests using generated keys, a temporary database, and exchange
response fixtures:

```sh
python -m unittest -v test_trading_bridge test_exchange_evidence
```

Tests cover authenticated evidence, fee accounting, invalid input, replay after
restart, concurrent reports and gifts, and rollback after a failed write.
Exchange readers have fixture tests for their authentication formats. No live
exchange, hardware wallet, browser UI, or complete peer/succession lifecycle
was tested; fixture tests do not establish live API compatibility.

## Earlier documentation

The original README described a larger Ledger trading dashboard and tests
whose files are absent from this checkout. It is preserved, marked as
historical, in [docs/README_HISTORICAL.md](docs/README_HISTORICAL.md). Its test
claims are not reproduced by this repository.
