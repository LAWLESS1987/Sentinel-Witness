"""Fixtures test request authentication and strict response accounting offline."""
import base64
import hashlib
import hmac
import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from exchange_evidence import ExchangeReader, EvidenceError, read_json, NoRedirect

class ReaderTests(unittest.TestCase):
    def test_coinbase_signed_lookup_and_fee_parsing(self):
        key=ec.generate_private_key(ec.SECP256R1())
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        def transport(method,url,headers,body):
            self.assertEqual(method,'GET')
            self.assertEqual(url,'https://api.coinbase.com/api/v3/brokerage/orders/historical/order-1')
            parts=headers['Authorization'].split()[1].split('.')
            decode=lambda s:base64.urlsafe_b64decode(s+'='*(-len(s)%4))
            claims=json.loads(decode(parts[1]))
            self.assertEqual(claims['uri'],'GET api.coinbase.com/api/v3/brokerage/orders/historical/order-1')
            sig=decode(parts[2])
            key.public_key().verify(encode_dss_signature(int.from_bytes(sig[:32],'big'),int.from_bytes(sig[32:],'big')),'.'.join(parts[:2]).encode(),ec.ECDSA(hashes.SHA256()))
            return {'order':{'order_id':'order-1','status':'FILLED','settled':True,'product_type':'SPOT','product_id':'BTC-USD','side':'BUY','filled_size':'2','filled_value':'100','total_fees':'1','created_time':'2026-01-01T00:00:00Z','last_fill_time':'2026-01-01T00:00:01Z'}}
        order=ExchangeReader('coinbase','fixture',pem,transport).get_order('order-1')
        self.assertEqual((order.quantity,order.gross,order.fee),(Decimal(2),Decimal(100),Decimal(1)))

    def test_cryptocom_signed_lookup_and_fee_parsing(self):
        def transport(method,url,headers,body):
            self.assertEqual(url,'https://api.crypto.com/exchange/v1/private/get-order-detail')
            p=json.loads(body)
            message=p['method']+str(p['id'])+p['api_key']+'order_id123'+str(p['nonce'])
            self.assertEqual(p['sig'],hmac.new(b'fixture',message.encode(),hashlib.sha256).hexdigest())
            return {'code':0,'result':{'order_id':'123','status':'FILLED','exec_inst':[],'instrument_name':'BTC_USD','fee_instrument_name':'USD','side':'SELL','cumulative_quantity':'2','cumulative_value':'112','cumulative_fee':'1','create_time':1000,'update_time':2000}}
        order=ExchangeReader('cryptocom','fixture','fixture',transport).get_order('123')
        self.assertEqual(order.closed,Decimal(2))
        self.assertEqual(order.fee,Decimal(1))

    def test_kraken_authentication(self):
        def transport(method,url,headers,body):
            from urllib.parse import parse_qs
            nonce=parse_qs(body.decode())['nonce'][0]
            expected=base64.b64encode(hmac.new(b'fixture',b'/0/private/QueryOrders'+hashlib.sha256(nonce.encode()+body).digest(),hashlib.sha512).digest()).decode()
            self.assertEqual(headers['API-Sign'],expected)
            return {'error':['fixture unavailable']}
        reader=ExchangeReader('kraken','fixture',base64.b64encode(b'fixture').decode(),transport)
        with self.assertRaises(EvidenceError):reader.get_order('order-1')

    def test_transport_failure_does_not_disclose_secret(self):
        with patch('exchange_evidence.build_opener',side_effect=RuntimeError('sensitive fixture')):
            with self.assertRaises(EvidenceError) as caught:read_json('GET','https://api.kraken.com',{})
        self.assertNotIn('sensitive',str(caught.exception))

    def test_redirect_refused(self):
        with self.assertRaises(EvidenceError):NoRedirect().redirect_request(None,None,302,'',{},'https://example.com')

if __name__ == '__main__':unittest.main()
