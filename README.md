# Sentinel-Witness

This repository holds:

- **`trader/`**: the trading program that places orders. It was copied here from covenant on
  2026-10-05 ([`trader/README.md`](trader/README.md)).
- A Python Covenant ledger node, with a verified profit and gift bridge.
- React dashboard components and the trade gate they import.

It does not contain a complete buildable PWA. The original inventory had eight tracked files
at commit `3f9464a890ea6ea097e933c42b48a2f058925100` on 2026-10-01.

## Trading: Sentinel-Witness executes, covenant witnesses

`trader/` reads balances, plans under the rules in `trader/MY_STRATEGY.md`, and places orders
when armed. Three things stay in covenant, the operator's node, and are reached through
`trader/witness.py`:

- **the seal**: each decision is admitted or refused by covenant's node and mined;
- **the day's approval**: no order goes live without today's plan approved in covenant;
- **the reserve floors**: the frozen hold-only floors on XRP, LINK and HBAR, read in place.

Without covenant configured, the trader refuses to run a cycle. Every live order must clear
all of these:

- `armed`;
- no `TRADER_HALT` file, in either folder;
- **$25 per order, $50 per day, 2 orders per day**;
- the guard stack on buys, including the **10% cash floor**;
- today's approval;
- the seal.

**Since 2026-10-06, every coin except XRP, HBAR and LINK is open.** It is the operator's
recorded scope. The three keep their frozen floors. On the open coins:

- the 50% reserve is lifted;
- a coin below its 200-day line is sold toward its floor;
- cash under 10% is rebuilt from the largest open coin above its line;
- each sale is one capped order a day.

**Rule 5 was waived on 2026-10-05** by the operator's choice ("Trade now within caps"). On that
day its record was 7/30 signals settled, 0/7 wins, mean −6.13% after costs. The waiver is a
record in the local config, printed and sealed every cycle. It deletes no check. That record,
and every account fact in these documents, rests on files that are never published, so a
reader cannot check it. Details, commands and setup are in [`trader/README.md`](trader/README.md).

`trader/venues.py` holds three order adapters: Coinbase, Kraken and Robinhood. Where the venue
offers a server-side dry run, a disarmed order goes there: Coinbase `/orders/preview`, Kraken
`validate=true`. Robinhood publishes no preview endpoint, so its dry run is local only.
Exchange credentials are read from the operator's home folder and are never in this
repository, which is public.

## What is present

| Path | Role |
|---|---|
| `trader/` | The trading program, its guards, its tests, the strategy research, price series and trading records. Every copied file is in `trader/COPIED_FROM_COVENANT.tsv` |
| `trader/witness.py` | The boundary to covenant: seal, daily approval, reserve floors, covenant's halt file |
| `tradeGate.js` | The trade gate `tierNavigation.js` imports. Tiers, `enableAutomated()` with no unlimited option, and `gateTrade()`, which seals every proposal through covenant's seal service on `127.0.0.1:8433` and allows only an answer of exactly `{"ok": true, "admission": "admitted"}`. Copied from covenant's `sentinel_witness/tradeGate.js` |
| `covenant_unified_v8.py` | An older copy of covenant's ledger node: peer networking, HTTP API, staking and succession. The bridge below uses it. **The trader does not**: its seals run in covenant, against covenant's current node |
| `covenant_trading_bridge.py` | Verified profit credits, atomic signed gifts and succession registration |
| `exchange_evidence.py` | Read-only order lookups and closed-lot profit calculation |
| `test_trading_bridge.py`, `test_exchange_evidence.py` | Offline regression tests for the bridge |
| `ethics_policy.py`, `tests/` | Ledger-effect admission policy and ethics regressions |
| `Dashboard.jsx` | Dashboard component consuming a gate supplied by its caller |
| `AutomatedSetupModal.jsx` | Form for per-trade and daily-count limits |
| `automatedLimits.js`, `tierNavigation.js` | Frontend validation and tier-navigation helpers |
| `requirements.txt`, `requirements-dev.txt` | Python dependencies (`cryptography` is the trader's only third-party one) |
| `docs/` | Profit verification, the ethics checklists, and the historical README |
| `COWORK_TOMBSTONE.md` | The record of the `cowork/` folder taken private on 2026-10-03, with the hashes that prove it is unchanged when it returns |

## What the bridge establishes

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

The gate enforces refusals on the tested value paths, records warnings and
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
simulation mode is not implemented. Starting the node does not verify exchange
access; a profit report performs authenticated order lookups.

## Frontend completeness

The checkout has no `package.json`, application entry point, Vite configuration,
frontend exchange execution adapters or Ledger adapter. `tierNavigation.js`'s import of
`./tradeGate.js` now resolves. `Dashboard.jsx` and `AutomatedSetupModal.jsx` import from a
`../lib/` layout that is absent here. So `npm install`, `npm test` and `npm run build` cannot
build or test this checkout, and no JavaScript in it has been run. Orders are placed by
`trader/`, in Python, not by the dashboard.

## Verification and limits

The bridge:

```sh
python -m unittest -v test_trading_bridge test_exchange_evidence
```

Its tests cover authenticated evidence, fee accounting, invalid input, replay after
restart, concurrent reports and gifts, and rollback after a failed write.
Exchange readers have fixture tests for their authentication formats.

The trader's suites run from `trader/` and are listed in [`trader/README.md`](trader/README.md).
CI runs both on every push (`.github/workflows/verified-bridge.yml`). The trader's checks that
need covenant's ledger or seal service report **NOT RUN** on a machine without covenant.

The trader's covenant link (seal admitted and mined, floors read, daily gate asked) was
checked live on the operator's machine on 2026-10-05 in plan-only mode. No live order has been
placed from this repository. No hardware wallet, browser UI or complete peer/succession
lifecycle was tested. Fixture tests do not establish live API compatibility.

## Earlier documentation

The original README described a larger Ledger trading dashboard and tests
whose files are absent from this checkout. It is preserved, marked as
historical, in [docs/README_HISTORICAL.md](docs/README_HISTORICAL.md). Its test
claims are not reproduced by this repository.
