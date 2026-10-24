# Sentinel Witness component preview

This checkout contains two React components, tier/limit helpers, and a local
preview with synthetic balances and price. It is runnable as a component demo.
It does not implement a trading application, credential storage, exchange
adapters, or Ledger signing. Tier changes in the preview only change in-memory
presentation state.

## Run

With Node.js 22.12+ and pnpm:

```
pnpm install --frozen-lockfile
pnpm test
pnpm dev
pnpm build
```

The development server binds to 127.0.0.1. Production assets go to dist/.
The preview does not call the seal service or any exchange.

## Included and tested

- Dashboard.jsx and AutomatedSetupModal.jsx now import files present in this checkout.
- Node tests exercise tier transitions, invalid limits, exposure calculation,
  and seal refusal/allow responses with injected in-memory fetch functions.
- The build resolves and bundles the React components.
- tradeGate.js is copied unchanged from LAWLESS1987/covenant at
  d668a771dd5f772ed6db5fd30531e1ce7dd0beac, sentinel_witness/tradeGate.js.
  Its executor helper is exported for integration but is not wired into the
  preview. A successful seal is not evidence of a wallet signature or an order.

Browser/device behavior and live services require separate integration testing.
The Python core snapshots in this repository are not launched by this preview.

## Correction to the earlier README (2026-10-24)

The earlier README described files and test coverage absent from this checkout.
It is retained verbatim in README-as-written-20261024.md as historical evidence;
its claims about secureStorage, exchange adapters, App.jsx, PWA installation,
and hardware behavior do not describe this distribution. No live credentials,
wallet integration, strategy validation, or device testing was performed here.
