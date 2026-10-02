# Profit verification

The node now verifies exchange order evidence before creating a local profit
credit. The caller's signature authorizes a report; it cannot supply its own
exchange credentials, server URL, execution quantities, or fees.

## Configure a pool

Set `COVENANT_TRADING_ACCOUNTS` to a local JSON configuration file. Keep this
file and credentials outside the repository. Each account binds one exchange
credential to the SHA-256 fingerprint of a pool's public key in DER
SubjectPublicKeyInfo format (`exchange_evidence.pool_fingerprint(pem)`).

```json
{"accounts": [{
  "exchange": "kraken",
  "pool_sha256": "REPLACE_WITH_PUBLIC_KEY_FINGERPRINT",
  "api_key_env": "POOL_EXCHANGE_API_KEY",
  "api_secret_env": "POOL_EXCHANGE_API_SECRET"
}]}
```

The two named environment variables hold the exchange credential. Use a
dedicated read-only credential with order-history access and no trading or
withdrawal permissions. The node does not inspect or change key permissions.
Configuration is loaded at startup; restart after changing it. No configuration
means profit verification returns an explicit error; gifts and other node
features remain available. Invalid configuration does not bypass verification.

Reader identifiers are `kraken`, `coinbase`, and `cryptocom`. Coinbase uses an
ECDSA P-256 API private key; Kraken uses its base64 API secret. Crypto.com uses
the Exchange API secret. Do not reuse a Kraken credential across node processes:
nonce ordering is serialized within one reader process only.

## Submit a report

Keep the existing `/trading/report_fill` request fields. Set `external_ref` to
the exact compact JSON string produced by
`order_reference(buy_order_id, sell_order_id)` and sign it using
`trading_profit_payload`. The pool key signs all the existing fields, including
the expected profit and timestamp. The node authenticates before making an
exchange call. An old free-form reference must be replaced and re-signed.

Supported accounting is a fully matched USD spot round trip: terminal buy and
sell orders, equal executed quantities, buy completed before sell opened, and
fees in the quote currency. Partially filled canceled orders can qualify when
their final executed quantities match. Net trading profit is sell proceeds
minus sell fees, buy cost, and buy fees. Stablecoins are not converted to USD.
Kraken fee flags must establish quote-currency fees; Coinbase orders must be
settled; Crypto.com margin execution flags are rejected.

This does not calculate portfolio-wide profit, FIFO across multiple lots,
taxes, withdrawal fees, financing costs, or currency conversion. It does not
place trades. Exchange-reported order evidence is trusted through HTTPS and
authenticated account access; it is not an independent audit of the exchange.

## Atomicity and replay

Both order IDs, normalized evidence, the judgment audit entry, and the ledger
credit commit together. A database error rolls everything back. A permanent
unique constraint on exchange plus order ID prevents either leg being reused,
including across timestamps and restarts. This conservative constraint can
reject colliding order IDs from separate accounts; it cannot cause double credit.
Gifts have permanent signed-payload receipts and atomic balance transfers.
Other ledger spending paths have not been changed by this patch.

Existing ledger entries are preserved. A matching legacy report reference or
order ID blocks another credit, but arbitrary historical free-form references
cannot be reconciled automatically. Review historical credits separately;
this change does not retroactively authenticate them. Keep the database and its
new receipt tables together in backups; restoring older state restores its
older accounting history too.

The existing ledger stores floating-point balances. The bridge rejects amounts
that lose their decimal value when converted to its float representation, but
this patch is not a migration of the entire ledger to decimal storage.

## Verification

Run `python -m unittest -v test_trading_bridge test_exchange_evidence` after
installing `requirements.txt`. Tests use disposable databases and generated
keys. Exchange responses are fixtures; no production credentials are needed.
Live API compatibility and real account permissions still require a separately
configured integration check before deployment.

Protocol references checked during implementation:

- [Kraken order lookup](https://docs.kraken.com/api-reference/account-data/query-orders-info)
- [Kraken authentication](https://docs.kraken.com/exchange/guides/rest/authentication)
- [Coinbase order lookup](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/get-order)
- [Coinbase authentication](https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication)
- [Crypto.com Exchange protocol](https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket-single-page/)
