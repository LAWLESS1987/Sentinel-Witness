"""Read-only exchange evidence for closed USD spot round trips.

Credentials and pool authorization come from node-local configuration, never
from a profit-report request. No order placement or withdrawal API exists here.
See docs/PROFIT_VERIFICATION.md for configuration and accounting scope.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


class EvidenceError(ValueError):
    """No ledger credit may be made from this request/evidence."""


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise EvidenceError(f'{label} must be a finite decimal')
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        raise EvidenceError(f'{label} must be a finite decimal') from None
    if not out.is_finite() or out.copy_abs() > Decimal('1e15') or (positive and out <= 0):
        raise EvidenceError(f'{label} is outside the supported range')
    if out and out.as_tuple().exponent < -18:
        raise EvidenceError(f'{label} has unsupported precision')
    return out


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise EvidenceError('Use exchange-assigned order IDs')
    return value


def order_reference(buy_order_id, sell_order_id):
    """Value to put in external_ref BEFORE signing the existing report payload."""
    return json.dumps([identifier(buy_order_id), identifier(sell_order_id)], separators=(',', ':'))


def parse_reference(value):
    try:
        if not isinstance(value, str) or len(value) > 300:
            raise ValueError
        ids = json.loads(value)
        if not isinstance(ids, list) or len(ids) != 2 or ids[0] == ids[1]:
            raise ValueError
        return identifier(ids[0]), identifier(ids[1])
    except (ValueError, TypeError):
        raise EvidenceError('external_ref must be a JSON array [buy_order_id, sell_order_id]; sign that exact string') from None


def pool_fingerprint(pem):
    try:
        key = serialization.load_pem_public_key(pem.encode())
        der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return hashlib.sha256(der).hexdigest()
    except (ValueError, TypeError, AttributeError):
        raise EvidenceError('Invalid pool public key') from None


def timestamp(value):
    if isinstance(value, str) and 'T' in value:
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError
            return Decimal(str(dt.timestamp()))
        except ValueError:
            raise EvidenceError('Exchange timestamp is invalid') from None
    return number(value, 'exchange timestamp', positive=True)


@dataclass(frozen=True)
class Order:
    order_id: str
    asset: str
    quote: str
    side: str
    quantity: Decimal
    gross: Decimal
    fee: Decimal
    opened: Decimal
    closed: Decimal


@dataclass(frozen=True)
class ProfitEvidence:
    exchange: str
    buy: Order
    sell: Order
    pnl: Decimal

    def record(self):
        def order(o):
            return {k: str(v) if isinstance(v, Decimal) else v for k, v in vars(o).items()}
        return {'exchange': self.exchange, 'buy': order(self.buy), 'sell': order(self.sell), 'pnl_usd': str(self.pnl)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EvidenceError('Exchange redirect refused')


def read_json(method, url, headers, body=None):
    """Bounded HTTPS reads; exceptions never include credentials or response bodies."""
    try:
        req = Request(url, data=body, headers=headers, method=method)
        with build_opener(NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise EvidenceError('Exchange response too large')
        result = json.loads(raw, parse_float=Decimal)
        if not isinstance(result, dict):
            raise EvidenceError('Invalid exchange response')
        return result
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError('Exchange evidence could not be read; no credit was made') from None


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def coinbase_token(api_key, secret, path):
    try:
        key = serialization.load_pem_private_key(secret.replace('\\n', '\n').encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError
        now = int(time.time())
        header = {'alg': 'ES256', 'typ': 'JWT', 'kid': api_key, 'nonce': secrets.token_hex(16)}
        payload = {'sub': api_key, 'iss': 'cdp', 'nbf': now, 'exp': now + 120,
                   'uri': 'GET api.coinbase.com' + path}
        message = '.'.join(_b64(json.dumps(x, separators=(',', ':')).encode()) for x in (header, payload))
        r, s = decode_dss_signature(key.sign(message.encode(), ec.ECDSA(hashes.SHA256())))
        return message + '.' + _b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    except Exception:
        raise EvidenceError('Coinbase requires a configured ECDSA P-256 API key') from None


class ExchangeReader:
    """One server-selected account, with fixed exchange endpoints."""
    def __init__(self, exchange, api_key, secret, transport=read_json):
        self.exchange, self.api_key, self.secret = exchange, api_key, secret
        self.transport = transport
        self._nonce = 0
        self._lock = threading.Lock()

    def get_order(self, order_id):
        identifier(order_id)
        # Serialize private calls so a later Kraken nonce cannot arrive first.
        with self._lock:
            self._nonce = max(time.time_ns() // 1_000_000, self._nonce + 1)
            try:
                if self.exchange == 'kraken':
                    return self._kraken(order_id)
                if self.exchange == 'coinbase':
                    return self._coinbase(order_id)
                if self.exchange == 'cryptocom':
                    return self._cryptocom(order_id)
                raise EvidenceError('No read-only verifier for this exchange')
            except EvidenceError:
                raise
            except Exception:
                raise EvidenceError('Exchange evidence is incomplete or malformed; no credit was made') from None

    def _kraken(self, oid):
        path = '/0/private/QueryOrders'
        nonce = str(self._nonce)
        body = urlencode({'nonce': nonce, 'txid': oid}).encode()
        digest = hashlib.sha256(nonce.encode() + body).digest()
        signature = base64.b64encode(hmac.new(base64.b64decode(self.secret, validate=True),
                                              path.encode() + digest, hashlib.sha512).digest()).decode()
        result = self.transport('POST', 'https://api.kraken.com' + path,
                                {'API-Key': self.api_key, 'API-Sign': signature,
                                 'Content-Type': 'application/x-www-form-urlencoded'}, body)
        if result.get('error') != []:
            raise EvidenceError('Kraken rejected the order lookup')
        order = result['result'][oid]
        descr = order['descr']
        if order['status'] not in ('closed', 'canceled', 'expired') or descr.get('leverage') != 'none':
            raise EvidenceError('Kraken order must be a terminal, unleveraged spot order')
        flags = set(order['oflags'].split(','))
        if 'fciq' not in flags or 'fcib' in flags:
            raise EvidenceError('Kraken fee currency must be quote currency for this accounting path')
        pair = descr['pair']
        metadata = self.transport('GET', 'https://api.kraken.com/0/public/AssetPairs?' + urlencode({'pair': pair}), {}, None)
        if metadata.get('error') != [] or len(metadata['result']) != 1:
            raise EvidenceError('Kraken product could not be verified')
        product = next(iter(metadata['result'].values()))
        base, quote = product['wsname'].split('/')
        base = {'XBT': 'BTC', 'XDG': 'DOGE'}.get(base, base)
        return self._order(oid, base, quote, descr['type'], order['vol_exec'], order['cost'], order['fee'],
                           order['opentm'], order['closetm'])

    def _coinbase(self, oid):
        path = '/api/v3/brokerage/orders/historical/' + oid
        result = self.transport('GET', 'https://api.coinbase.com' + path,
                                {'Authorization': 'Bearer ' + coinbase_token(self.api_key, self.secret, path)}, None)
        order = result['order']
        if order['order_id'] != oid or order['status'] not in ('FILLED', 'CANCELLED', 'EXPIRED') or order.get('settled') is not True:
            raise EvidenceError('Coinbase order must be terminal and settled')
        if order.get('product_type') != 'SPOT' or order.get('leverage') not in (None, '', '0', '1'):
            raise EvidenceError('Coinbase order must be unleveraged spot')
        base, quote = order['product_id'].split('-')
        native = order.get('total_fees_native')
        if native is not None and native.get('currency') != quote:
            raise EvidenceError('Coinbase fees are not denominated in the quote currency')
        return self._order(oid, base, quote, order['side'], order['filled_size'], order['filled_value'],
                           order['total_fees'], timestamp(order['created_time']), timestamp(order['last_fill_time']))

    def _cryptocom(self, oid):
        method = 'private/get-order-detail'
        nonce = self._nonce
        params = {'order_id': oid}
        message = method + str(nonce) + self.api_key + 'order_id' + oid + str(nonce)
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        body = json.dumps({'id': nonce, 'method': method, 'api_key': self.api_key, 'params': params,
                           'nonce': nonce, 'sig': signature}, separators=(',', ':')).encode()
        result = self.transport('POST', 'https://api.crypto.com/exchange/v1/' + method,
                                {'Content-Type': 'application/json'}, body)
        if result.get('code') != 0:
            raise EvidenceError('Crypto.com rejected the order lookup')
        order = result['result']
        if str(order['order_id']) != oid or order['status'] not in ('FILLED', 'CANCELED', 'EXPIRED'):
            raise EvidenceError('Crypto.com order must be terminal')
        if order.get('leverage') not in (None, '', '0', '1') or order.get('isolation_type') not in (None, '', 'NONE'):
            raise EvidenceError('Crypto.com margin orders are outside this spot accounting path')
        instructions = order['exec_inst']
        if not isinstance(instructions, list) or not all(isinstance(x, str) for x in instructions) or set(instructions) - {'POST_ONLY', 'SMART_POST_ONLY'}:
            raise EvidenceError('Crypto.com order is not confirmed as an ordinary spot order')
        base, quote = order['instrument_name'].split('_')
        if order['fee_instrument_name'] != quote:
            raise EvidenceError('Crypto.com fees are not denominated in the quote currency')
        return self._order(oid, base, quote, order['side'], order['cumulative_quantity'], order['cumulative_value'],
                           order['cumulative_fee'], number(order['create_time'], 'created') / 1000,
                           number(order['update_time'], 'closed') / 1000)

    @staticmethod
    def _order(oid, asset, quote, side, quantity, gross, fee, opened, closed):
        qty, value, fees = number(quantity, 'filled quantity', positive=True), number(gross, 'filled value', positive=True), number(fee, 'fees')
        start, end = timestamp(opened), timestamp(closed)
        if abs(fees) >= value or end < start or side.upper() not in ('BUY', 'SELL'):
            raise EvidenceError('Inconsistent exchange order values')
        return Order(oid, asset, quote, side.upper(), qty, value, fees, start, end)


class ConfiguredVerifier:
    """Configuration is loaded once at node startup; restart to change bindings."""
    def __init__(self, accounts=None):
        self.accounts = accounts or {}

    @classmethod
    def from_environment(cls):
        path = os.environ.get('COVENANT_TRADING_ACCOUNTS')
        if not path:
            return cls()
        try:
            config = json.loads(Path(path).read_text(encoding='utf-8'))
            accounts = {}
            used_keys = set()
            for item in config['accounts']:
                exchange, fingerprint = item['exchange'], item['pool_sha256']
                if exchange not in ('kraken', 'coinbase', 'cryptocom') or not re.fullmatch('[0-9a-f]{64}', fingerprint):
                    raise ValueError
                api_key, secret = os.environ[item['api_key_env']], os.environ[item['api_secret_env']]
                if not api_key or not secret or (fingerprint, exchange) in accounts or (exchange, api_key) in used_keys:
                    raise ValueError
                accounts[fingerprint, exchange] = ExchangeReader(exchange, api_key, secret)
                used_keys.add((exchange, api_key))
            return cls(accounts)
        except Exception:
            raise EvidenceError('Trading verification configuration is invalid; check local account bindings and credential environment variables') from None

    def verify(self, pool_pem, asset, exchange, external_ref):
        fingerprint = pool_fingerprint(pool_pem)
        reader = self.accounts.get((fingerprint, exchange))
        if reader is None:
            raise EvidenceError('No exchange account is bound to this pool; configure COVENANT_TRADING_ACCOUNTS on the node')
        buy_id, sell_id = parse_reference(external_ref)
        buy, sell = reader.get_order(buy_id), reader.get_order(sell_id)
        if buy.order_id != buy_id or sell.order_id != sell_id:
            raise EvidenceError('Exchange order identity mismatch')
        if buy.side != 'BUY' or sell.side != 'SELL' or buy.asset != asset or sell.asset != asset:
            raise EvidenceError('Order asset or direction does not match the report')
        if buy.quote != 'USD' or sell.quote != 'USD':
            raise EvidenceError('This pnl_usd path requires USD-quoted orders; stablecoins are not treated as USD')
        if buy.quantity != sell.quantity or buy.closed > sell.opened:
            raise EvidenceError('Provide a closed buy/sell round trip with equal filled quantities and the buy completed before the sell opened')
        with localcontext() as ctx:
            ctx.prec = 50
            pnl = sell.gross - sell.fee - buy.gross - buy.fee
        number(pnl, 'verified profit', positive=True)
        return ProfitEvidence(exchange, buy, sell, pnl)
