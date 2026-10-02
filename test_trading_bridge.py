"""Offline regression tests: generated keys, temporary ledger, fixture exchange."""
import base64
import gc
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
import covenant_unified_v8 as core
import covenant_trading_bridge as bridge
from exchange_evidence import ConfiguredVerifier, ExchangeReader, order_reference, pool_fingerprint


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Live network prohibited'))
        self.network.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = core.Database(str(Path(self.tmp.name) / 'test.db'))
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pool = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.orders = {
            'buy': {'status':'closed','descr':{'pair':'XBTUSD','type':'buy','leverage':'none'},'oflags':'fciq','vol_exec':'2','cost':'100','fee':'1','opentm':'1','closetm':'2'},
            'sell': {'status':'closed','descr':{'pair':'XBTUSD','type':'sell','leverage':'none'},'oflags':'fciq','vol_exec':'2','cost':'112','fee':'1','opentm':'3','closetm':'4'},
        }
        self.calls = []
        def transport(method, url, headers, body):
            self.calls.append(url)
            if method == 'GET':
                return {'error':[], 'result':{'XXBTZUSD':{'wsname':'XBT/USD'}}}
            from urllib.parse import parse_qs
            oid = parse_qs(body.decode())['txid'][0]
            self.assertIn('API-Sign', headers)
            return {'error':[], 'result':{oid:self.orders[oid]}}
        reader = ExchangeReader('kraken','fixture',base64.b64encode(b'fixture').decode(),transport)
        self.verifier = ConfiguredVerifier({(pool_fingerprint(self.pool),'kraken'):reader})
        self.api = bridge.TradingBridge(self.db, core.ReasoningSentinel(core.MockJudge()),None,None,self.verifier)

    def tearDown(self):
        self.network.stop()
        gc.collect()
        self.tmp.cleanup()

    def sign(self, payload):
        return base64.b64encode(self.key.sign(payload,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=padding.PSS.MAX_LENGTH),hashes.SHA256())).decode()

    def report(self, amount=10.0, timestamp=5.0):
        args=(self.pool,'BTC','kraken',order_reference('buy','sell'),amount,timestamp)
        return self.api.report_realized_profit(*args,self.sign(bridge.trading_profit_payload(*args)))

    def count(self, table):
        with sqlite3.connect(self.db.db_path) as conn:
            return conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_verified_profit_and_gift(self):
        self.assertEqual(self.report()['credited'],10)
        other=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        args=(self.pool,other,3.0,7.0)
        sig=self.sign(bridge.node_gift_payload(*args))
        self.api.gift_stake_to_new_node(*args,sig)
        self.assertEqual(self.db.get_balance(self.pool),7)
        self.assertEqual(self.db.get_balance(other),3)
        self.assertFalse(self.db.load_stakes())
        with self.assertRaises(core.TradingBridgeError): self.api.gift_stake_to_new_node(*args,sig)

    def test_claim_must_match_exchange_fees(self):
        with self.assertRaises(core.TradingBridgeError): self.report(12.0)
        self.assertEqual(self.count('ledger_entries'),0)
        self.assertEqual(self.count('trading_receipts'),0)

    def test_unconfigured_pool_rejected(self):
        self.api.verifier=ConfiguredVerifier()
        with self.assertRaises(core.TradingBridgeError): self.report()
        self.assertEqual(self.calls,[])

    def test_invalid_signature_never_queries_exchange(self):
        with self.assertRaises(core.TradingBridgeError):
            self.api.report_realized_profit(self.pool,'BTC','kraken',order_reference('buy','sell'),10,5,'bad')
        self.assertEqual(self.calls,[])

    def test_replay_different_timestamp_and_restart(self):
        self.report()
        self.api=bridge.TradingBridge(self.db,self.api.sentinel,None,None,self.verifier)
        with self.assertRaises(core.TradingBridgeError): self.report(timestamp=123)
        self.assertEqual(self.db.get_balance(self.pool),10)

    def test_concurrent_reports_credit_once(self):
        def submit(t):
            try: self.report(timestamp=t); return True
            except core.TradingBridgeError: return False
        with ThreadPoolExecutor(max_workers=6) as executor:
            self.assertEqual(sum(executor.map(submit,range(10,16))),1)
        self.assertEqual(self.db.get_balance(self.pool),10)

    def test_failed_write_rolls_back_order_consumption(self):
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("CREATE TRIGGER fail_credit BEFORE INSERT ON ledger_entries BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(core.TradingBridgeError): self.report()
        for table in ('trading_used_orders','trading_receipts','judgments','ledger_entries'):
            self.assertEqual(self.count(table),0)
        with sqlite3.connect(self.db.db_path) as conn: conn.execute('DROP TRIGGER fail_credit')
        self.assertEqual(self.report()['credited'],10)

    def test_malformed_and_incomplete_exchange_data(self):
        for field,value in [('status','open'),('vol_exec','1'),('fee','NaN'),('cost','Infinity'),('oflags','fcib'),('opentm','1')]:
            with self.subTest(field=field):
                old=self.orders['sell'][field];self.orders['sell'][field]=value
                with self.assertRaises(core.TradingBridgeError): self.report()
                self.orders['sell'][field]=old
                self.assertEqual(self.count('ledger_entries'),0)

    def test_nonfinite_report_rejected(self):
        for value in (float('nan'),float('inf'),-1,0,True,{},'not money'):
            with self.subTest(value=value), self.assertRaises(core.TradingBridgeError): self.report(value)
        self.assertEqual(self.calls,[])

    def test_gifts_cannot_overdraw_concurrently(self):
        self.report()
        recipient=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        def gift(t):
            args=(self.pool,recipient,7.0,t)
            try: self.api.gift_stake_to_new_node(*args,self.sign(bridge.node_gift_payload(*args)));return True
            except core.TradingBridgeError:return False
        with ThreadPoolExecutor(max_workers=2) as executor: self.assertEqual(sum(executor.map(gift,(10,11))),1)
        self.assertEqual(self.db.get_balance(self.pool),3)
        self.assertEqual(self.db.get_balance(recipient),7)

    def test_http_validation_and_valid_report(self):
        node=SimpleNamespace(trading_bridge=self.api,rate_limiter=SimpleNamespace(allow=lambda *args:True))
        client=core.CovenantAPI(node,self.db).app.test_client()
        for body in ([],None,{'pnl_usd':{}},{'pnl_usd':True}):
            self.assertEqual(client.post('/trading/report_fill',json=body).status_code,400)
        args=(self.pool,'BTC','kraken',order_reference('buy','sell'),10.0,5.0)
        data=dict(zip(('pool_pubkey','asset','exchange','external_ref','pnl_usd','timestamp'),args))
        data['signature']=self.sign(bridge.trading_profit_payload(*args))
        self.assertEqual(client.post('/trading/report_fill',json=data).status_code,200)
        self.assertEqual(client.post('/trading/report_fill',json=data).status_code,400)

    def test_legacy_credit_not_credited_again(self):
        self.db.record_ledger_entry(self.pool,10,'trading_profit',ref_id='trading_profit:kraken:sell')
        with self.assertRaises(core.TradingBridgeError): self.report()
        self.assertEqual(self.db.get_balance(self.pool),10)


if __name__ == '__main__': unittest.main()
