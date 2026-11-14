#!/usr/bin/env python3
"""K7 (review of 2026-11-14): the two unauthenticated write routes of the old core copy, and where its API listens.

  python -B tests/test_core_loopback.py

covenant_unified_v8.py in this branch is an old copy of another project's core. Before this change anyone who
could reach its API port could register a peer (POST /peers) and clear the crisis flag (POST /crisis/clear), and
the API listened on every interface unless told otherwise. Now both routes answer 403 to a caller that is not on
loopback, and the API listens on loopback unless a host is given.

The core is imported whole, so this needs what it imports at the top: flask, cryptography and waitress (the
ranges are in requirements.txt). Nothing is served and no port is opened: the routes are called through Flask's
test client, which sets the caller's address through environ_base, and waitress's serve() is replaced by a
recorder for the one test that asks where the API would listen. The addresses are documentation addresses
(RFC 5737, RFC 3849), not real hosts.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import covenant_unified_v8 as core  # noqa: E402

OUTSIDE = ("203.0.113.7", "198.51.100.20", "2001:db8::7", "::ffff:203.0.113.7", "", "not-an-address")
LOOPBACK = ("127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1")
PEER = {"peer_id": "peer-one", "host": "peer.example", "port": 6001}


class Core(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core_loopback_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = core.Database(os.path.join(self.tmp, "node.db"))
        self.node = core.P2PNode("test-node", "127.0.0.1", 0, None, None, self.db)
        self.api = core.CovenantAPI(self.node, self.db)
        self.client = self.api.app.test_client()

    def post(self, path, addr, body=None):
        return self.client.post(path, json=body or {}, environ_base={"REMOTE_ADDR": addr})


class Peers(Core):
    def test_a_caller_that_is_not_on_loopback_cannot_register_a_peer(self):
        for addr in OUTSIDE:
            with self.subTest(addr=addr):
                got = self.post("/peers", addr, PEER)
                self.assertEqual(got.status_code, 403, got.get_data(as_text=True))
                self.assertIn("loopback", got.get_json()["message"])
        self.assertEqual(self.node.peers, {})                                   # nothing was added
        self.assertEqual(self.db.load_peer_registrations(), [])                 # and nothing recorded as accepted

    def test_a_loopback_caller_registers_a_peer_as_before(self):
        for n, addr in enumerate(LOOPBACK):
            with self.subTest(addr=addr):
                got = self.post("/peers", addr, dict(PEER, peer_id="peer-%d" % n))
                self.assertEqual(got.status_code, 200, got.get_data(as_text=True))
                self.assertEqual(got.get_json(), {"status": "success"})
                self.assertEqual(self.node.peers["peer-%d" % n], ("peer.example", 6001))
        rows = self.db.load_peer_registrations()
        self.assertEqual([r["source_addr"] for r in rows], list(LOOPBACK))      # still audited, as before
        self.assertEqual(self.post("/peers", "127.0.0.1", {"peer_id": "x"}).status_code, 400)   # and still validated

    def test_reading_the_peers_is_not_restricted_by_this_change(self):
        got = self.client.get("/peers", environ_base={"REMOTE_ADDR": OUTSIDE[0]})
        self.assertEqual(got.status_code, 200)


class Crisis(Core):
    def setUp(self):
        super().setUp()
        self.node.crisis_mode, self.node.crisis_reason = True, "set by the test"

    def test_a_caller_that_is_not_on_loopback_cannot_clear_the_crisis_flag(self):
        for addr in OUTSIDE:
            with self.subTest(addr=addr):
                got = self.post("/crisis/clear", addr)
                self.assertEqual(got.status_code, 403, got.get_data(as_text=True))
                self.assertIn("loopback", got.get_json()["message"])
                self.assertTrue(self.node.crisis_mode)                          # the flag is still up
                self.assertEqual(self.node.crisis_reason, "set by the test")

    def test_a_loopback_caller_clears_the_crisis_flag_as_before(self):
        for addr in LOOPBACK:
            with self.subTest(addr=addr):
                self.node.crisis_mode, self.node.crisis_reason = True, "set by the test"
                got = self.post("/crisis/clear", addr)
                self.assertEqual(got.status_code, 200, got.get_data(as_text=True))
                self.assertEqual(got.get_json(), {"status": "success", "message": "crisis_mode cleared"})
                self.assertFalse(self.node.crisis_mode)
                self.assertEqual(self.node.crisis_reason, "")

    def test_reading_the_crisis_flag_is_not_restricted_by_this_change(self):
        got = self.client.get("/crisis", environ_base={"REMOTE_ADDR": OUTSIDE[0]})
        self.assertEqual(got.status_code, 200)
        self.assertTrue(got.get_json()["crisis_mode"])


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core_bind_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.served = []
        real = core.serve
        core.serve = lambda app, **kw: self.served.append(kw)                   # record where it would listen; open nothing
        self.addCleanup(setattr, core, "serve", real)

    def master(self, name, **kw):
        return core.CovenantUnifiedMaster(name, db_path=os.path.join(self.tmp, name + ".db"), **kw)

    def test_the_api_listens_on_loopback_when_no_host_is_given(self):
        m = self.master("default", port=5999)
        m.api.run()
        self.assertEqual(self.served[-1]["host"], "127.0.0.1")
        self.assertEqual(self.served[-1]["port"], 5999)
        db = core.Database(os.path.join(self.tmp, "api.db"))
        self.assertEqual(core.CovenantAPI(core.P2PNode("n", "127.0.0.1", 0, None, None, db), db).host, "127.0.0.1")

    def test_a_host_the_operator_gives_is_still_used(self):
        for host in ("0.0.0.0", "192.0.2.10"):
            with self.subTest(host=host):
                m = self.master("explicit-" + host.replace(".", "-"), host=host, port=5999)
                m.api.run()
                self.assertEqual(self.served[-1]["host"], host)
                self.assertEqual(m.node.host, host)                             # the peer sockets follow it, as before


if __name__ == "__main__":
    unittest.main(verbosity=1)
