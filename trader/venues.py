#!/usr/bin/env python3
r"""
venues.py -- Kraken and Coinbase order adapters for covenant_trader.py.

THE ONE RULE IN THIS FILE
  Every call that can reach a matching engine takes `live` and it defaults to
  False. With live=False the order goes to the venue's own VALIDATE endpoint
  (Kraken `validate=true`, Coinbase `/orders/preview`) -- the venue parses it,
  checks your balance, checks the minimums, and returns what it WOULD do,
  without booking anything. Nothing here places an order unless a caller passes
  live=True explicitly, and covenant_trader.py only does that when its config
  is armed.

WHY THE SIGNING IS REIMPLEMENTED HERE
  kraken_balance.py already signs Kraken requests, but it calls sys.exit() on
  every error -- correct for a script a human runs and watches, fatal for a
  loop that is supposed to survive a bad night and report it. These raise
  VenueError instead. The signing itself is the same construction.

CREDENTIALS
  Read from OUTSIDE this folder, exactly as the balance readers do:
      %USERPROFILE%\.kraken\credentials
      %USERPROFILE%\.coinbase\credentials  (or cdp_api_key.json)
  A READ-ONLY key is enough for everything except live=True. Placing orders
  needs a trade-scoped key, which is yours to create and install -- see
  EXCHANGE_SETUP.md. Nothing in this file will ever ask you for one.
"""
from __future__ import annotations
import os, json, time, base64, hashlib, hmac, urllib.request, urllib.parse, urllib.error, secrets

import key_scope          # H9: what a key may do, and who else can read it. No network code.

HOME = os.path.expanduser("~")


class VenueError(Exception):
    """Anything the venue refused or could not answer. Always carries the why."""


# H9 (2026-11-12). Two things every adapter below now does, both judged in
# key_scope.py (which makes no venue call) and both said here in VenueError's
# words so every existing caller handles them as it handles any venue failure:
#
#   _own_credential(path)   before a credential file is read: refuse one that
#                           another account on this machine can read. Windows
#                           is asked through the file's ACL, not its mode bits.
#   key_permissions(fetch)  what the installed key may do, from the venue's own
#                           endpoint where it has one. `fetch` is the function
#                           that returns the venue's raw answer; the default
#                           makes the signed call, a test hands in a fake.
#                           covenant_trader asks key_scope.key_reasons(venue)
#                           before any live order.
# ============================================================================
# H10 (2026-11-12): ORDER MECHANICS
#
# (a) A DEFINITE REFUSAL IS NOT "MAYBE BOOKED". covenant_trader.execute frees
#     the day's row only for an answer that begins "HTTP 4". Kraken answers a
#     rejected order with HTTP 200 and an `error` list, so every Kraken
#     rejection -- insufficient funds, below the minimum, a bad argument --
#     was booked as "unknown, may be booked" and used up one of the day's two
#     orders. So were this file's own pre-flight refusals (a size that rounds
#     to zero, a pair the venue does not list), which are raised before
#     anything is sent. VenueRefused says: nothing was booked. It is a
#     VenueError, so every existing handler still catches it.
#
# (b) A CLIENT REFERENCE ON EVERY LIVE ORDER, derived from (day, symbol, side,
#     size, sequence) so the same order has the same reference however often
#     it is retried, and can be found again afterwards. What it is worth
#     differs by venue and is said where each is used.
#
# (c) NO ORDER WITHOUT A PRICE BAND. A market order takes whatever the book
#     gives. With a reference price (the planner's own, cross-venue checked)
#     an order is a LIMIT no worse than PRICE_BAND from it: a sale never
#     below reference x (1 - band), a purchase never above reference x
#     (1 + band).
# ============================================================================
class VenueRefused(VenueError):
    """A definite refusal: the venue answered and booked nothing, or this
    adapter stopped before anything was sent."""


PRICE_BAND = 0.01          # 1%: the furthest a fill may be from the reference price


def client_ref(day, symbol, side, size, seq):
    """32 hex characters, the same for the same (day, symbol, side, size, seq)."""
    return hashlib.sha256(("sentinel:%s:%s:%s:%s:%s" % (
        day, str(symbol).upper(), str(side).lower(), size, seq)).encode()).hexdigest()[:32]


def band_edge(side, ref_px, band=PRICE_BAND):
    """The worst price this order may trade at, as a float."""
    ref = float(ref_px)
    if not (ref > 0 and ref == ref and ref != float("inf")):
        raise VenueRefused("the reference price %r is not a finite number above zero" % (ref_px,))
    return ref * (1.0 - band) if str(side).lower() == "sell" else ref * (1.0 + band)


def within_band(side, price, ref_px, band=PRICE_BAND):
    """True when `price` is no worse for this side than the band allows."""
    edge = band_edge(side, ref_px, band)
    return float(price) >= edge - 1e-12 if str(side).lower() == "sell" else float(price) <= edge + 1e-12


def _grid(price, decimals, side):
    """`price` on a grid of `decimals` places, as text: a sale's floor rounds
    UP and a purchase's ceiling rounds DOWN, so rounding never widens the band."""
    from decimal import Decimal, ROUND_DOWN, ROUND_UP
    q = Decimal(1).scaleb(-int(decimals))
    d = Decimal(str(price)).quantize(q, rounding=ROUND_UP if str(side).lower() == "sell" else ROUND_DOWN)
    return format(d, "f")


def _iso_epoch(s):
    """Epoch seconds for an RFC 3339 time that carries its zone, or None."""
    import datetime, re
    try:
        t = str(s).strip()
        if t[-1:] in "Zz":
            t = t[:-1] + "+00:00"
        t = re.sub("[.]([0-9]{1,6})[0-9]*", lambda m: "." + m.group(1), t)   # at most microseconds
        d = datetime.datetime.fromisoformat(t)
        return d.timestamp() if d.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _own_credential(path):
    try:
        key_scope.refuse_shared_credential(path)
    except key_scope.KeyScopeError as e:
        raise VenueError(str(e)) from None


def _scope(fn, *a):
    try:
        return fn(*a)
    except key_scope.KeyScopeError as e:
        raise VenueError(str(e)) from None


# --------------------------------------------------------------------- shared
def _http(req, timeout=30):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise VenueError(f"HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise VenueError(f"unreachable: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        # A read timeout AFTER the request was sent is not a URLError (urllib
        # wraps only the connect phase), so it used to escape as a bare
        # TimeoutError, abort the trader's cycle before save_state, and leave
        # an accepted live order unrecorded (pre-push audit 2026-09-06). It is
        # a venue error like any other. The caller cannot know whether the
        # order was booked; covenant_trader.execute() records it as unknown.
        raise VenueError(f"no response: {type(e).__name__}: {e}") from None
    except json.JSONDecodeError:
        raise VenueError("response was not JSON") from None


def _round_down(x, decimals):
    """Truncate, never round up. Rounding a sell UP invents coins you do not
    have and the venue rejects the order; rounding a buy up spends more than
    the plan authorised. Down is the only safe direction for both sides."""
    f = 10 ** decimals
    return int(x * f) / f



# --------------------------------------------------------------- the dry run
# WHAT live=False ACTUALLY REACHES, DECLARED PER VENUE.
#
#   Robinhood landed on 2026-08-29 with no server-side preview, and said so in
#   its own docstring. Every document describing this repository's money
#   posture -- README, CONSTITUTION II.1, GOVERNANCE VIII -- and the checker
#   they all point to (money_posture.py, written the NEXT day) still described
#   two venues and one uniform guarantee. Nothing contradicted anything; the
#   guarantee was simply narrower than the sentence, and no check compared them.
#
#   So the guarantee is now a value a venue must state, not an adjective a
#   document may assume:
#     "venue"  the exchange's own endpoint priced and rejected the order
#     "local"  only the checks in venues.py ran; no matching engine saw it
#
#   A venue class with no DRY_RUN is UNKNOWN, and UNKNOWN is reported as a
#   failure rather than defaulted -- so a FOURTH adapter cannot inherit a
#   promise it does not keep just by being added. test_v2_venue_guarantee.py
#   pins that, and money_posture.py reads it instead of a hardcoded pair.

# --------------------------------------------------------------------- Kraken
class KrakenVenue:
    name = "kraken"
    DRY_RUN = "venue"
    DRY_RUN_ENDPOINT = "AddOrder validate=true"
    CRED = os.path.join(HOME, ".kraken", "credentials")

    def __init__(self):
        self._key = self._secret = None
        self._pairs = None

    # -- credentials --------------------------------------------------------
    def has_credentials(self):
        return os.path.exists(self.CRED)

    def _creds(self):
        if self._key:
            return self._key, self._secret
        if not os.path.exists(self.CRED):
            raise VenueError(f"no credential at {self.CRED} (see EXCHANGE_SETUP.md)")
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.realpath(self.CRED).startswith(os.path.realpath(here) + os.sep):
            raise VenueError("credential is inside the synced folder -- refusing")
        _own_credential(self.CRED)                               # H9
        k = s = ""
        for line in open(self.CRED, encoding="utf-8"):
            line = line.strip()
            if line.startswith("key="):
                k = line[4:].strip()
            elif line.startswith("secret="):
                s = line[7:].strip()
        if not k or not s:
            raise VenueError(f"{self.CRED} needs key= and secret= lines")
        self._key, self._secret = k, s
        return k, s

    # -- signed calls -------------------------------------------------------
    def _private(self, endpoint, data=None):
        key, secret = self._creds()
        path = f"/0/private/{endpoint}"
        data = dict(data or {})
        data["nonce"] = str(int(time.time() * 1000))
        post = urllib.parse.urlencode(data)
        sha = hashlib.sha256((data["nonce"] + post).encode()).digest()
        try:
            sig = hmac.new(base64.b64decode(secret), path.encode() + sha,
                           hashlib.sha512).digest()
        except Exception:
            raise VenueError("secret is not valid base64 -- re-copy the private key") from None
        req = urllib.request.Request(
            "https://api.kraken.com" + path, data=post.encode(), method="POST",
            headers={"API-Key": key, "API-Sign": base64.b64encode(sig).decode(),
                     "User-Agent": "covenant-trader/1.0",
                     "Content-Type": "application/x-www-form-urlencoded"})
        body = _http(req)
        if body.get("error"):
            errs = "; ".join(str(e) for e in body["error"])
            definite = all(self.definite_refusal(e) for e in body["error"])
            if "Permission denied" in errs:
                errs += "  -> this key lacks the permission for that call"
            # H10: Kraken says no with HTTP 200 and this list. Where EVERY
            # entry is one of its documented rejections, nothing was booked.
            raise (VenueRefused if definite else VenueError)(f"kraken: {errs}")
        return body.get("result", {})

    # H10. Kraken's documented error strings that mean the request was
    # rejected: every EOrder: (order-level rejections -- insufficient funds,
    # order minimum not met, cannot open position ...), and these named ones.
    # EService:Unavailable, EService:Busy, EService:Deadline elapsed ("the
    # order may have been placed despite the timeout"), EGeneral:Internal
    # error and anything not listed stay AMBIGUOUS: the row stays in the day's
    # record as unknown.  docs.kraken.com/exchange/guides/general/errors
    DEFINITE_ERRORS = ("EGeneral:Invalid arguments", "EGeneral:Permission denied",
                       "EAPI:Invalid key", "EAPI:Invalid signature", "EAPI:Invalid nonce",
                       "EAPI:Rate limit exceeded")

    @classmethod
    def definite_refusal(cls, err):
        e = str(err).strip()
        return e.startswith("EOrder:") or any(e.startswith(d) for d in cls.DEFINITE_ERRORS)

    # -- market metadata ----------------------------------------------------
    def pairs(self):
        """altname/base -> constraints. Cached; the table changes rarely."""
        if self._pairs is not None:
            return self._pairs
        res = _http(urllib.request.Request(
            "https://api.kraken.com/0/public/AssetPairs",
            headers={"User-Agent": "covenant-trader/1.0"})).get("result", {})
        out = {}
        for name, v in res.items():
            if v.get("quote") not in ("ZUSD", "USD") or v.get("status") != "online":
                continue
            ws = (v.get("wsname") or "")
            sym = ws.split("/")[0] if "/" in ws else v.get("base", "")
            meta = {"pair": name, "symbol": sym, "altname": v.get("altname") or "",
                    "wsname": ws,
                    "lot_decimals": int(v.get("lot_decimals", 8)),
                    "pair_decimals": int(v.get("pair_decimals", 5)),
                    "ordermin": float(v.get("ordermin", 0) or 0),
                    "costmin": float(v.get("costmin", 0) or 0)}
            if sym and (sym not in out or len(name) < len(out[sym]["pair"])):
                out[sym] = meta
        self._pairs = out
        return out

    def meta(self, symbol):
        ALIAS = {"BTC": "XBT", "DOGE": "XDG"}
        p = self.pairs()
        m = p.get(symbol.upper()) or p.get(ALIAS.get(symbol.upper(), symbol.upper()))
        if not m:
            raise VenueError(f"kraken does not list a USD pair for {symbol}")
        return m

    # -- reads --------------------------------------------------------------
    def balances(self):
        raw = self._private("Balance")
        out = {}
        for code, amt in raw.items():
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            spot = code.split(".")[0]          # SOL.S / ADA.F -> same exposure
            out[spot] = out.get(spot, 0.0) + amt
        return out

    def open_orders(self):
        return self._private("OpenOrders").get("open", {})

    # -- what this key may do (H9) -------------------------------------------
    KEY_SCOPE_ENDPOINT = "POST /0/private/GetApiKeyInfo"

    def key_permissions(self, fetch=None):
        """`fetch` returns GetApiKeyInfo's `result` object."""
        return _scope(key_scope.from_kraken,
                      (fetch or (lambda: self._private("GetApiKeyInfo")))())

    # -- orders -------------------------------------------------------------
    LIVE_ORDER = "limit, immediate-or-cancel, no worse than PRICE_BAND from the reference price"

    def place(self, symbol, side, qty, live=False, ordertype="market", price=None,
              ref_px=None, client_ref=None):
        """side is 'buy'/'sell'. Returns a dict describing what happened.

        live=False sends Kraken's own `validate=true`: the order is fully
        parsed and checked against your balance and the pair minimums, and
        nothing is booked. That is a real check against the real venue, not a
        simulation written here -- which is the point.

        H10. With `ref_px` the order is never a market order: it is a LIMIT at
        the band's edge (band_edge), immediate-or-cancel -- it fills at once at
        the book's price when that is inside the band, and otherwise cancels
        instead of resting. `client_ref` travels as Kraken's cl_ord_id on a
        live order. WHAT THAT IS WORTH: Kraken checks cl_ord_id for uniqueness
        among OPEN orders only (docs.kraken.com/exchange/guides/general/
        clordid), so it is NOT an idempotency key for an order that has
        already closed. It is the handle OpenOrders and ClosedOrders can be
        asked by.
        """
        try:
            m = self.meta(symbol)
        except VenueError as e:
            # Whatever went wrong finding the pair, no order has been sent.
            raise VenueRefused(f"{e} (nothing was sent)") from None
        vol = _round_down(float(qty), m["lot_decimals"])
        if vol <= 0:
            raise VenueRefused(f"{symbol}: quantity rounds to zero at "
                               f"{m['lot_decimals']} decimals")
        if m["ordermin"] and vol < m["ordermin"]:
            raise VenueRefused(f"{symbol}: {vol} is below Kraken's minimum "
                               f"{m['ordermin']}")
        if ref_px is not None and ordertype == "market":
            ordertype, price = "limit", band_edge(side, ref_px)
            tif = "IOC"
        else:
            tif = None
        data = {"pair": m["pair"], "type": side, "ordertype": ordertype,
                "volume": f"{vol:.{m['lot_decimals']}f}".rstrip("0").rstrip(".")}
        if ordertype == "limit":
            if price is None:
                raise VenueRefused("limit order needs a price")
            if ref_px is not None:
                if not within_band(side, price, ref_px):
                    raise VenueRefused(f"{symbol}: limit {price} is outside the "
                                       f"{PRICE_BAND:.0%} band around {ref_px}")
                data["price"] = _grid(price, m["pair_decimals"], side)
            else:
                data["price"] = f"{_round_down(price, m['pair_decimals']):.{m['pair_decimals']}f}"
            if tif:
                data["timeinforce"] = tif
        if not live:
            data["validate"] = "true"
        elif client_ref:
            data["cl_ord_id"] = str(client_ref)
        res = self._private("AddOrder", data)
        txid = res.get("txid")
        if isinstance(txid, list):                  # Kraken returns a list of ids
            txid = txid[0] if len(txid) == 1 else (",".join(str(x) for x in txid) or None)
        return {"venue": self.name, "live": live, "symbol": symbol, "side": side,
                "qty": vol, "pair": m["pair"], "ordertype": ordertype,
                "price": data.get("price"), "client_ref": data.get("cl_ord_id"),
                "descr": (res.get("descr") or {}).get("order"),
                "txid": txid}

    # -- reading orders back (H10) ---------------------------------------------
    def _sym_of_pair(self, pair):
        """Kraken names a pair three ways (XXLMZUSD, XLMUSD, XLM/USD). The
        symbol it trades, or VenueError if it is none of ours."""
        want = str(pair or "").upper()
        for sym, m in self.pairs().items():
            names = {str(m.get("pair") or "").upper(), str(m.get("altname") or "").upper(),
                     str(m.get("wsname") or "").upper(), str(m.get("wsname") or "").upper().replace("/", "")}
            if want and want in names:
                return {"XBT": "BTC", "XDG": "DOGE"}.get(sym, sym)
        raise VenueError("kraken: an order names the pair %r, which is not a USD pair this "
                         "adapter knows" % (pair,))

    def open_orders_norm(self, fetch=None):
        """Open orders as [{"sym", "side", "id", "client_ref"}]. `fetch`
        returns OpenOrders' `result`."""
        res = (fetch or (lambda: self._private("OpenOrders")))()
        rows = res.get("open") if isinstance(res, dict) else None
        if not isinstance(rows, dict):
            raise VenueError("kraken: OpenOrders returned no `open` object")
        out = []
        for txid, o in rows.items():
            d = (o or {}).get("descr") or {}
            out.append({"sym": self._sym_of_pair(d.get("pair")), "side": str(d.get("type") or "").lower(),
                        "id": str(txid), "client_ref": (o or {}).get("cl_ord_id")})
        return out

    def recent_fills(self, since=None, fetch=None):
        """Trades since `since` (epoch seconds) as [{"order_id", "sym", "side",
        "usd", "qty", "at"}]. `fetch` returns TradesHistory's `result`."""
        data = {"start": str(int(since))} if since else {}
        res = (fetch or (lambda: self._private("TradesHistory", data)))()
        rows = res.get("trades") if isinstance(res, dict) else None
        if not isinstance(rows, dict):
            raise VenueError("kraken: TradesHistory returned no `trades` object")
        out = []
        for t in rows.values():
            try:
                out.append({"order_id": str(t["ordertxid"]), "sym": self._sym_of_pair(t.get("pair")),
                            "side": str(t.get("type") or "").lower(), "usd": float(t["cost"]),
                            "qty": float(t["vol"]), "at": float(t["time"])})
            except (KeyError, TypeError, ValueError):
                raise VenueError("kraken: a trade in TradesHistory cannot be read") from None
        return out


# ------------------------------------------------------------------- Coinbase

def _ipv4_only():
    """CDP keys carry an IPv4 allowlist; Windows prefers the IPv6 route, whose
    address rotates and is not on the list, so every call 401s. Filter DNS to
    A records for this process. Falls back untouched if a host has no A record."""
    import socket
    if getattr(socket, "_covenant_ipv4_only", False):
        return
    orig = socket.getaddrinfo

    def v4_first(host, *a, **k):
        res = orig(host, *a, **k)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res
    socket.getaddrinfo = v4_first
    socket._covenant_ipv4_only = True


class CoinbaseVenue:
    name = "coinbase"
    DRY_RUN = "venue"
    DRY_RUN_ENDPOINT = "/api/v3/brokerage/orders/preview"
    MAKER_BY_DEFAULT = True   # post-only limit at the touch; see place()
    CRED_DIR = os.environ.get("COINBASE_CRED_DIR", os.path.join(HOME, ".coinbase"))
    HOST = "api.coinbase.com"

    def __init__(self):
        self._cred = None
        self._products = None

    def has_credentials(self):
        return (os.path.exists(os.path.join(self.CRED_DIR, "credentials"))
                or os.path.exists(os.path.join(self.CRED_DIR, "cdp_api_key.json")))

    def _creds(self):
        if self._cred:
            return self._cred
        j = os.path.join(self.CRED_DIR, "cdp_api_key.json")
        if os.path.exists(j):
            _own_credential(j)                                   # H9
            d = json.load(open(j, encoding="utf-8"))
            name, pk = d.get("name") or d.get("id"), d.get("privateKey")
        else:
            p = os.path.join(self.CRED_DIR, "credentials")
            if not os.path.exists(p):
                raise VenueError(f"no CDP credential in {self.CRED_DIR} "
                                 f"(see EXCHANGE_SETUP.md)")
            _own_credential(p)                                   # H9
            name = pk = None
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith("name="):
                    name = line[5:].strip()
                elif line.startswith("privateKey="):
                    pk = line[11:].strip()
        if not name or not pk:
            raise VenueError("CDP credential needs name= and privateKey= "
                             "(an Exchange or retail v2 key will not place "
                             "Advanced Trade orders)")
        self._cred = (name, pk)
        return self._cred

    def _jwt(self, method, path):
        """CDP JWT. PEM privateKey -> ES256 (older ECDSA download); base64
        privateKey -> EdDSA (the portal's current Ed25519 default)."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils
        name, pk = self._creds()
        pk = pk.strip()

        def b64u(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        now = int(time.time())
        # The uri claim is method + host + PATH ONLY. A query string in it
        # makes Coinbase answer 401 with no further explanation.
        pay = {"sub": name, "iss": "cdp", "nbf": now, "exp": now + 120,
               "uri": f"{method} {self.HOST}{path.split('?', 1)[0]}"}
        if "BEGIN" in pk:
            try:
                key = serialization.load_pem_private_key(
                    pk.replace("\\n", "\n").encode(), password=None)
            except Exception as e:
                raise VenueError(f"privateKey did not load as a PEM EC key "
                                 f"({type(e).__name__})") from None
            hdr = {"alg": "ES256", "kid": name, "typ": "JWT", "nonce": secrets.token_hex(16)}
            si = f"{b64u(json.dumps(hdr).encode())}.{b64u(json.dumps(pay).encode())}"
            der = key.sign(si.encode(), ec.ECDSA(hashes.SHA256()))
            r, s = utils.decode_dss_signature(der)
            return f"{si}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        try:
            seed = base64.b64decode(pk)
        except Exception as e:
            raise VenueError(f"privateKey is neither PEM nor base64 ({type(e).__name__})") from None
        if len(seed) not in (32, 64):
            raise VenueError(f"privateKey decoded to {len(seed)} bytes; Ed25519 CDP keys are 64")
        key = ed25519.Ed25519PrivateKey.from_private_bytes(seed[:32])
        hdr = {"alg": "EdDSA", "kid": name, "typ": "JWT", "nonce": secrets.token_hex(16)}
        si = f"{b64u(json.dumps(hdr).encode())}.{b64u(json.dumps(pay).encode())}"
        return f"{si}.{b64u(key.sign(si.encode()))}"

    def _call(self, method, path, body=None):
        _ipv4_only()
        tok = self._jwt(method, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"https://{self.HOST}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "application/json"})
        return _http(req)

    # -- reads --------------------------------------------------------------
    def balances(self):
        js = self._call("GET", "/api/v3/brokerage/accounts")
        out = {}
        for a in js.get("accounts", []):
            bal = a.get("available_balance") or {}
            v = float(bal.get("value") or 0)
            h = float((a.get("hold") or {}).get("value") or 0)
            if v + h > 0:
                out[bal.get("currency") or a.get("currency", "?")] = v + h
        return out

    def products(self):
        if self._products is not None:
            return self._products
        js = self._call("GET", "/api/v3/brokerage/products?product_type=SPOT")
        out = {}
        for p in js.get("products", []):
            if p.get("quote_currency_id") != "USD" or p.get("trading_disabled"):
                continue
            out[p.get("base_currency_id", "")] = {
                "product_id": p.get("product_id"),
                "base_increment": p.get("base_increment") or "0.00000001",
                "quote_increment": p.get("quote_increment") or "0.01",
                "base_min_size": float(p.get("base_min_size") or 0)}
        self._products = out
        return out

    def meta(self, symbol):
        m = self.products().get(symbol.upper())
        if not m:
            raise VenueError(f"coinbase does not list a USD spot product for {symbol}")
        return m

    def open_orders(self):
        js = self._call("GET", "/api/v3/brokerage/orders/historical/batch?order_status=OPEN")
        return js.get("orders", [])

    # -- reading orders back (H10) ---------------------------------------------
    def open_orders_norm(self, fetch=None):
        """Open orders as [{"sym", "side", "id", "client_ref"}]. `fetch`
        returns the list of order objects."""
        rows = (fetch or self.open_orders)()
        if not isinstance(rows, list):
            raise VenueError("coinbase: the open-orders answer was not a list")
        out = []
        for o in rows:
            pid = str((o or {}).get("product_id") or "")
            if "-" not in pid:
                raise VenueError("coinbase: an open order names no product")
            out.append({"sym": pid.split("-")[0].upper(), "side": str(o.get("side") or "").lower(),
                        "id": str(o.get("order_id") or ""), "client_ref": o.get("client_order_id")})
        return out

    def recent_fills(self, since=None, fetch=None):
        """Fills since `since` (epoch seconds) as [{"order_id", "sym", "side",
        "usd", "qty", "at"}]. `fetch` returns the list of fill objects."""
        def _get():
            q = ""
            if since:
                q = "?start_sequence_timestamp=" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
            return self._call("GET", "/api/v3/brokerage/orders/historical/fills" + q).get("fills", [])
        rows = (fetch or _get)()
        if not isinstance(rows, list):
            raise VenueError("coinbase: the fills answer was not a list")
        out = []
        for f in rows:
            try:
                if str(f.get("trade_type") or "FILL").upper() != "FILL":
                    continue                      # reversals and corrections are not fills
                px, size = float(f["price"]), float(f["size"])
                usd = size if f.get("size_in_quote") else px * size
                qty = (size / px) if f.get("size_in_quote") else size
                out.append({"order_id": str(f["order_id"]), "sym": str(f["product_id"]).split("-")[0].upper(),
                            "side": str(f.get("side") or "").lower(), "usd": usd, "qty": qty,
                            "at": _iso_epoch(f.get("trade_time"))})
            except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError):
                raise VenueError("coinbase: a fill cannot be read") from None
        return out

    # -- what this key may do (H9) -------------------------------------------
    KEY_SCOPE_ENDPOINT = "GET /api/v3/brokerage/key_permissions"

    def key_permissions(self, fetch=None):
        """`fetch` returns the key_permissions JSON object."""
        return _scope(key_scope.from_coinbase,
                      (fetch or (lambda: self._call("GET", "/api/v3/brokerage/key_permissions")))())

    def fees(self):
        """Maker/taker rates for the account's current tier (Advanced Trade
        pricing; the consumer app charges its own spread on top and is never
        used here)."""
        js = self._call("GET", "/api/v3/brokerage/transaction_summary")
        ft = js.get("fee_tier") or {}
        return {"maker": float(ft.get("maker_fee_rate") or 0),
                "taker": float(ft.get("taker_fee_rate") or 0),
                "tier": ft.get("pricing_tier"),
                "volume_30d": float(js.get("advanced_trade_only_volume") or 0)}

    def best_bid_ask(self, product_id):
        js = self._call("GET", f"/api/v3/brokerage/best_bid_ask?product_ids={product_id}")
        for pb in js.get("pricebooks", []):
            bids, asks = pb.get("bids") or [], pb.get("asks") or []
            if bids and asks:
                return float(bids[0]["price"]), float(asks[0]["price"])
        raise VenueError(f"{product_id}: no bid/ask in the pricebook")

    # -- orders -------------------------------------------------------------
    # Maker orders rest with a deadline, not for ever. A GTC post-only limit
    # that never fills would sit on the book unreconciled: nothing here cancels
    # it, and balances() counts held coins as position, so the next cycle
    # would plan the same sale again on top of it. GTD with this time-to-live
    # means an unfilled order cancels itself before the next cycle can read
    # its hold as position -- so it must be SHORTER than any loop interval
    # (the default loop_seconds is 3600), not equal to it.
    MAKER_TTL_S = 1800

    @staticmethod
    def _snap(price, increment, side):
        """Put a price on the product's quote grid EXACTLY. float truncation
        (int(x * 10**d) / 10**d) knocks one tick off prices that are already
        on the grid -- 1.15 -> 1.14 at 2 dp -- which for a post-only sell at
        the ask means a price below the ask, i.e. a taker, i.e. rejected. A buy
        rounds down, a sell rounds up, and an on-grid price is unchanged."""
        from decimal import Decimal, ROUND_DOWN, ROUND_UP
        inc = Decimal(str(increment))
        px = Decimal(str(price))
        q = (px / inc).to_integral_value(rounding=ROUND_UP if side.upper() == "SELL" else ROUND_DOWN)
        out = q * inc
        # normalize() drops trailing zeros ("0.50000000" -> "0.5"); format "f"
        # keeps it out of exponent notation ("1E+2" -> "100").
        return format(out.normalize(), "f")

    LIVE_ORDER = "limit, post-only at the touch, good for MAKER_TTL_S, no worse than PRICE_BAND from the reference price"

    def place(self, symbol, side, qty, live=False, ordertype=None, price=None, day=None,
              ref_px=None, client_ref=None):
        """ordertype: None -> the venue default (post-only maker at the touch when
        MAKER_BY_DEFAULT, else market); "market" -> take, deliberately;
        "maker" -> post-only at the touch; "limit" -> post-only GTD at `price`.

        H10. With `ref_px` the order must be a limit and its price must be
        inside PRICE_BAND of the reference -- a touch that has moved outside
        the band since the plan was priced is refused here, before anything is
        sent. `client_ref`, when given, is the client_order_id: Coinbase
        returns the EXISTING order for a client_order_id it has already seen
        rather than creating another, so here it is a real idempotency key."""
        try:
            m = self.meta(symbol)
        except VenueError as e:
            # Whatever went wrong finding the product, no order has been sent.
            raise VenueRefused(f"{e} (nothing was sent)") from None
        # Size on the base grid the same exact way as price: Decimal, rounded
        # down, formatted without an exponent. str(float) wrote 4.5e-05 for
        # small sizes and _round_down's float truncation could take one
        # increment off a size that was already on the grid.
        size_s = self._snap(qty, m["base_increment"], "BUY")
        size = float(size_s)
        if size <= 0:
            raise VenueRefused(f"{symbol}: quantity rounds to zero at the "
                               f"product's base increment {m['base_increment']}")
        if m["base_min_size"] and size < m["base_min_size"]:
            raise VenueRefused(f"{symbol}: {size} is below Coinbase's minimum "
                               f"{m['base_min_size']}")
        # FEES. Advanced Trade charges the taker rate on anything that crosses
        # the book (every market order) and the lower maker rate on anything
        # that rests. The default is therefore "maker": a post-only limit at
        # the touch -- the bid for a buy, the ask for a sell. post_only makes
        # Coinbase reject the order rather than fill it as a taker if the
        # price moves through it, so the maker rate is guaranteed. The cost is
        # that a resting order may not fill within MAKER_TTL_S; it then cancels
        # itself and the next cycle re-plans. Right for a daily rebalancer,
        # wrong for anything that must fill now: pass ordertype="market".
        if ordertype is None:
            ordertype = "maker" if self.MAKER_BY_DEFAULT else "market"
        if ordertype == "maker":
            try:
                bid, ask = self.best_bid_ask(m["product_id"])
            except VenueError as e:
                raise VenueRefused(f"{e} (nothing was sent)") from None
            price = bid if side.upper() == "BUY" else ask
        if ordertype == "market":
            if ref_px is not None:
                raise VenueRefused(f"{symbol}: a market order has no price band; with a "
                                   f"reference price the order must be a limit")
            cfg = {"market_market_ioc": {"base_size": size_s}}
        else:
            if price is None:
                raise VenueRefused(f"{symbol}: a limit order needs a price")
            price = self._snap(price, m["quote_increment"], side)
            if ref_px is not None and not within_band(side, float(price), ref_px):
                raise VenueRefused(f"{symbol}: the price {price} is outside the "
                                   f"{PRICE_BAND:.0%} band around the reference {ref_px} -- "
                                   f"nothing was sent")
            end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + self.MAKER_TTL_S))
            cfg = {"limit_limit_gtd": {"base_size": size_s, "limit_price": price,
                                       "end_time": end, "post_only": True}}
        body = {"product_id": m["product_id"], "side": side.upper(),
                "order_configuration": cfg}
        # /preview parses and prices the order without booking it -- Coinbase's
        # equivalent of Kraken's validate=true, and the default here for the
        # same reason. It takes the same body MINUS client_order_id, which it
        # rejects as an unknown field (HTTP 400) -- found 2026-09-06, the first
        # time a real key reached this line.
        path = "/api/v3/brokerage/orders" if live else "/api/v3/brokerage/orders/preview"
        if live:
            # Deterministic per (day, product, side, size): a retry after a
            # timeout on an order Coinbase in fact accepted is rejected as a
            # duplicate instead of booked twice. A fresh random id per attempt
            # made that protection inert. The cost, accepted: a SECOND order of
            # the same product, side and size on the same local day is refused
            # too. The caps allow two orders a day and the planner never emits
            # two identical trims, so that case does not arise from this code.
            day = day or time.strftime("%Y-%m-%d")
            body["client_order_id"] = str(client_ref) if client_ref else hashlib.sha256(
                f"covenant:{day}:{m['product_id']}:{side.upper()}:{size_s}".encode()).hexdigest()[:32]
        res = self._call("POST", path, body)
        if live and not res.get("success", True):
            # H10: Coinbase answered, and the answer is success=false with an
            # error_response. Nothing was booked.
            raise VenueRefused(f"coinbase refused: {json.dumps(res)[:300]}")
        if not live and res.get("errs"):
            raise VenueError(f"coinbase preview rejected: {json.dumps(res['errs'])[:300]}")
        return {"venue": self.name, "live": live, "symbol": symbol, "side": side,
                "qty": size, "product": m["product_id"],
                "descr": json.dumps(res.get("order_configuration") or cfg)[:120],
                "txid": ((res.get("success_response") or {}).get("order_id")
                         if live else None)}


class RobinhoodVenue:
    """Robinhood Crypto, to the same contract as the two above -- with ONE
    difference that is stated rather than smoothed over.

    KRAKEN AND COINBASE HAVE A SERVER-SIDE DRY RUN. THIS DOES NOT.
    Kraken takes validate=true and Coinbase has /orders/preview: with
    live=False the VENUE parses the order, checks the balance and the
    minimums, and answers what it would do. Robinhood publishes no such
    endpoint. So live=False here can only mean "the checks THIS FILE can do
    locally passed" -- it has not been near a matching engine, the balance
    has not been verified against it, and it is not a promise that a live
    order would succeed.

    Returning the same shape as the other two while meaning something weaker
    is exactly the quiet unequal-guarantee this project keeps catching in
    itself, so every dry run from here carries venue_validated=False and
    says why in `descr`. A caller that treats a dry run as proof must read
    that field; the other two venues set it True.

    AUTH. Ed25519 over `api_key + timestamp + path + method + body`, the
    scheme Robinhood documents. Credentials live OUTSIDE this folder, like
    the others:
        %USERPROFILE%\\.robinhood\\credentials
    as JSON: {"api_key": "...", "private_key_b64": "..."} where the private
    key is the base64 seed issued when the key was created. Nothing here
    will ever ask you for one, and a read-only key covers everything except
    live=True.
    """
    name = "robinhood"
    DRY_RUN = "local"          # no preview endpoint exists to call
    DRY_RUN_ENDPOINT = None
    CRED = os.path.join(HOME, ".robinhood", "credentials")
    HOST = "https://trading.robinhood.com"

    def __init__(self):
        self._k = None

    def has_credentials(self):
        return os.path.exists(self.CRED)

    def _creds(self):
        if self._k:
            return self._k
        if not self.has_credentials():
            raise VenueError(
                f"no credential at {self.CRED} (see EXCHANGE_SETUP.md). "
                f"Robinhood requires auth even for market data, so nothing "
                f"on this venue answers read-only without a key.")
        _own_credential(self.CRED)                               # H9
        try:
            with open(self.CRED, encoding="utf-8") as fh:
                raw = json.load(fh)
            key = raw["api_key"]
            seed = base64.b64decode(raw["private_key_b64"])
        except (ValueError, KeyError, OSError) as e:
            raise VenueError(f"robinhood credential unreadable: {e}")
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except ImportError:
            raise VenueError(
                "robinhood signing needs `cryptography` (ed25519) -- it is in "
                "requirements.txt but is not importable here")
        # 32 bytes is the seed; a 64-byte blob is seed+public, so take 32.
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed[:32])
        self._k = (key, priv)
        return self._k

    def _call(self, method, path, body=None):
        key, priv = self._creds()
        payload = json.dumps(body) if body else ""
        ts = str(int(time.time()))
        msg = f"{key}{ts}{path}{method}{payload}".encode()
        sig = base64.b64encode(priv.sign(msg)).decode()
        req = urllib.request.Request(
            self.HOST + path,
            data=payload.encode() if payload else None,
            method=method,
            headers={"x-api-key": key, "x-signature": sig,
                     "x-timestamp": ts, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:                                   # noqa: BLE001
                pass
            raise VenueError(f"robinhood {method} {path}: HTTP {e.code} "
                             f"{detail}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise VenueError(f"robinhood {method} {path}: {e}")

    # -- what this key may do (H9) -------------------------------------------
    # This venue publishes no endpoint that reports a key's permissions, so it
    # cannot be asked. The answer is the operator's own hand-written record
    # (key_scope.KEY_SCOPE_FILE); with none, no live order goes to this venue.
    KEY_SCOPE_ENDPOINT = None

    def key_permissions(self, fetch=None):
        """`fetch` returns the record's three answers; the default reads the
        operator's file."""
        if fetch is not None:
            return fetch()
        return _scope(key_scope.recorded, self.name)

    def meta(self, symbol):
        sym = f"{symbol.upper()}-USD"
        res = self._call(
            "GET", f"/api/v1/crypto/trading/trading_pairs/?symbol={sym}")
        rows = res.get("results") or []
        if not rows:
            raise VenueError(f"robinhood does not list a USD pair for {symbol}")
        r = rows[0]
        return {"pair": sym, "symbol": symbol.upper(),
                "ordermin": float(r.get("min_order_size") or 0),
                "increment": r.get("quote_increment"),
                "status": r.get("status")}

    def balances(self):
        res = self._call("GET", "/api/v1/crypto/trading/holdings/")
        out = {}
        for h in res.get("results") or []:
            code = str(h.get("asset_code") or "").upper()
            try:
                qty = float(h.get("total_quantity") or 0)
            except (TypeError, ValueError):
                continue
            if code and qty:
                out[code] = out.get(code, 0.0) + qty
        return out

    def open_orders(self):
        res = self._call("GET", "/api/v1/crypto/trading/orders/?state=open")
        return res.get("results") or []

    # -- reading orders back (H10) ---------------------------------------------
    def open_orders_norm(self, fetch=None):
        """Open orders as [{"sym", "side", "id", "client_ref"}]. `fetch`
        returns the list of order objects."""
        rows = (fetch or self.open_orders)()
        if not isinstance(rows, list):
            raise VenueError("robinhood: the open-orders answer was not a list")
        out = []
        for o in rows:
            pair = str((o or {}).get("symbol") or "")
            if "-" not in pair:
                raise VenueError("robinhood: an open order names no pair")
            out.append({"sym": pair.split("-")[0].upper(), "side": str(o.get("side") or "").lower(),
                        "id": str(o.get("id") or ""), "client_ref": o.get("client_order_id")})
        return out

    def recent_fills(self, since=None, fetch=None):
        # NOT IMPLEMENTED, and said so rather than guessed: the fields a
        # filled order carries were not confirmed from this venue's
        # documentation, so no fill here is ever "confirmed" and no sale at
        # this venue counts as proceeds.
        raise VenueError("robinhood: fills are not read by this adapter (the response "
                         "fields are UNDETERMINED from its documentation)")

    LIVE_ORDER = "limit at the band's edge, no worse than PRICE_BAND from the reference price"

    def place(self, symbol, side, qty, live=False, ordertype="market",
              price=None, ref_px=None, client_ref=None):
        if side.lower() not in ("buy", "sell"):
            raise VenueRefused(f"robinhood: bad side {side!r}")
        try:
            m = self.meta(symbol)
        except VenueError as e:
            # Whatever went wrong finding the pair, no order has been sent.
            raise VenueRefused(f"{e} (nothing was sent)") from None
        size = float(qty)
        if m["ordermin"] and size < m["ordermin"]:
            raise VenueRefused(f"robinhood: {size} {symbol} is under the "
                               f"minimum order size {m['ordermin']}")
        if not live:
            # LOCAL ONLY -- see the class docstring. There is no venue-side
            # preview to call, so this reports what was checked HERE and
            # refuses to imply anything more.
            return {"venue": self.name, "live": False,
                    "venue_validated": False,
                    "symbol": symbol.upper(), "side": side.lower(),
                    "qty": size, "product": m["pair"],
                    "descr": f"LOCAL dry run only: size >= min "
                             f"({m['ordermin']}), pair status "
                             f"{m.get('status')}. Robinhood has no preview "
                             f"endpoint, so balance and buying power were "
                             f"NOT checked by the venue.",
                    "txid": None}
        # H10. The client_order_id was secrets.token_hex(16): new on every
        # attempt, so a retry of an order this venue had in fact accepted was a
        # second order. It is now the caller's reference, written as the UUID
        # the venue asks for ("for idempotency validation"). And with a
        # reference price the order is a LIMIT at the band's edge
        # (limit_order_config: asset_quantity, limit_price), not a market
        # order. How long such an order rests if it does not fill at once is
        # UNDETERMINED from the documentation read; covenant_trader plans
        # nothing for a symbol that has an order open.
        import uuid
        ref = str(client_ref) if client_ref else secrets.token_hex(16)
        try:
            coid = str(uuid.UUID(hex=ref[:32]))
        except ValueError:
            coid = str(uuid.uuid5(uuid.NAMESPACE_OID, ref))
        config = {"asset_quantity": str(size)}
        if ref_px is not None:
            ordertype = "limit"
            inc = str(m.get("increment") or "0.01")
            decimals = max(0, len(inc.split(".")[1].rstrip("0")) if "." in inc else 0)
            config["limit_price"] = _grid(band_edge(side, ref_px), decimals, side)
        body = {"symbol": m["pair"], "client_order_id": coid,
                "side": side.lower(), "type": ordertype,
                f"{ordertype}_order_config": config}
        res = self._call("POST", "/api/v1/crypto/trading/orders/", body)
        return {"venue": self.name, "live": True, "venue_validated": True,
                "symbol": symbol.upper(), "side": side.lower(), "qty": size,
                "product": m["pair"], "descr": json.dumps(res)[:120],
                "txid": res.get("id")}


def all_venues():
    """Every adapter. Callers should check has_credentials() first -- a venue
    with no key is not an error, it is a venue this machine cannot reach."""
    return [KrakenVenue(), CoinbaseVenue(), RobinhoodVenue()]
