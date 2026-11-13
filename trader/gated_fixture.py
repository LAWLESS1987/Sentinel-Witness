#!/usr/bin/env python3
"""gated_fixture.py -- a complete, armed, gated setup in a temp folder, for a
suite that needs an order to go LIVE at a stand-in venue.

WHY THIS EXISTS (R1, 2026-11-12). Until review round one a suite could make an
order "live" by handing covenant_trader.execute() a dict: armed, with every
other gate switched off, and sealed_ok=True. That was the defect, and it is
gone -- guards.live_gate judges from DISK and from the witness, so an order
goes live in a test exactly the way it would for real: every gate satisfied.
This module builds that state once, in one place, out of temp files and a
stand-in covenant, so that each suite then breaks the ONE thing it is about.

WHAT IT SETS UP, and restores on the way out:

  a config ON DISK   armed for one hour, with the shipped gates: seal required,
                     Rule 5 at 30 signals with significance, the day's plan
                     required, the caps at their ceilings, no waiver, no scope.
  a state ON DISK    today, no orders yet, a Rule 5 record that clears.
  a stand-in covenant folder, PINNED (witness.PINNED_COVENANT_HOME), with a
                     floor file; witness._run is replaced by a runner that
                     answers as covenant would: the seal is admitted, today's
                     plan is approved, and the approval record proposes exactly
                     the orders the suite names.
  no halt file.

It places nothing and opens no credential: the venue is whatever stand-in the
suite hands to execute(). It is not a test (its name does not begin test_).
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import witness as W
import guards as G
import covenant_trader as T

PLAN_HASH = "ab12" * 16


def until(seconds=3600):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


class Gated:
    """with Gated(approved=[order, ...]) as fx: fx.seal(orders); fx.execute(orders, [venue])"""

    def __init__(self, approved=(), cfg=None, state=None, gate=None, approval=None, decisions=None,
                 hold_lock=True):
        # C1: a cycle holds the trader's cycle lock from start to finish, as run_once does.
        # hold_lock=False leaves it to execute() and lets a test put another holder in the way.
        self._hold_lock, self._locked = bool(hold_lock), False
        self._approved = [{"sym": o["sym"], "side": o["side"], "usd": o["usd"]} for o in approved]
        self._cfg_over, self._state_over = dict(cfg or {}), dict(state or {})
        self.gate = list(gate or [])            # covenant's yes/no for the day: [] is yes
        self._approval, self._decisions = approval, decisions
        self.asked = []                         # which of covenant's scripts were run, in order

    # -- what covenant answers -------------------------------------------------
    def approval(self):
        if self._approval is not None:
            return self._approval
        now = time.time()
        return {"ok": True, "date": time.strftime("%Y-%m-%d", time.localtime(now)),
                "plan_hash": PLAN_HASH,
                "decisions": (self._decisions if self._decisions is not None else
                              [{"decision": "approve", "at": now - 3600, "plan_hash": PLAN_HASH}]),
                "orders": list(self._approved)}

    def _runner(self, home, script, payload, timeout):
        if script is W._SEAL_SCRIPT:
            self.asked.append("seal")
            return True, {"ok": True, "status": 200, "admission": "admitted", "tx_id": "fixture",
                          "detail": "stand-in covenant", "mined": "", "terms": "covenant"}, ""
        if script is W._GATE_SCRIPT:
            self.asked.append("gate")
            return True, list(self.gate), ""
        if script is W._APPROVAL_SCRIPT:
            self.asked.append("approval")
            return True, self.approval(), ""
        return False, None, "the stand-in covenant does not know that script"

    # -- setup / teardown --------------------------------------------------------
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.home = os.path.join(self.dir, "covenant")
        os.makedirs(os.path.join(self.home, "private"))
        open(os.path.join(self.home, "covenant_unified_v8.py"), "w").close()
        self.reserve = os.path.join(self.home, "private", "RESERVE.json")
        with open(self.reserve, "w", encoding="utf-8") as fh:
            json.dump({"baseline": {}, "starting_total_usd": 1000.0, "pct_buyable": 0.5}, fh)
        self.config_path = os.path.join(self.dir, "trader_config.json")
        self.state_path = os.path.join(self.dir, "trader_state.json")
        cfg = {k: v for k, v in G.SHIPPED_DEFAULTS.items()}
        cfg.update({"armed": True, "armed_until": until(), "covenant_home": self.home,
                    "node_ports": []})
        cfg.update(self._cfg_over)
        cfg = {k: v for k, v in cfg.items() if v is not None}       # None removes a key
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        now = time.time()
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        st = {"day": today, "last_seen_day": today, "last_seen_at": now, "orders_today": [],
              "equity_peak": 1000.0, "equity_start_of_day": 1000.0, "closed_trades": [],
              "last_sold": {}, "sealed_signals": 30, "bought_total_usd": 0.0,
              "rule5": {"clears": True, "why": "fixture: a record that clears"}}
        st.update(self._state_over)
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        self._keep = {
            (G, "CONFIG_PATH"): G.CONFIG_PATH, (T, "CONFIG"): T.CONFIG,
            (G, "TRADER_STATE"): G.TRADER_STATE, (T, "STATE"): T.STATE,
            (G, "HALT"): G.HALT, (G, "RESERVE_PATH"): G.RESERVE_PATH,
            (T, "RESERVE_PATH"): T.RESERVE_PATH,
            (W, "PINNED_COVENANT_HOME"): W.PINNED_COVENANT_HOME, (W, "_run"): W._run,
            (T.V, "all_venues"): T.V.all_venues,
        }
        G.CONFIG_PATH = T.CONFIG = self.config_path
        G.TRADER_STATE = T.STATE = self.state_path
        G.HALT = os.path.join(self.dir, "TRADER_HALT")
        G.RESERVE_PATH = T.RESERVE_PATH = self.reserve
        W.PINNED_COVENANT_HOME = self.home
        W._run = self._runner
        self._sealed_keep = list(W._SEALED)
        del W._SEALED[:]
        self.cfg = G.load_config(self.config_path)
        self.st = T.load_state()
        if self._hold_lock:
            self._locked, _why = G.take_cycle_lock()
        return self

    def __exit__(self, *exc):
        if self._locked:
            G.release_cycle_lock()
            self._locked = False
        for (mod, name), val in self._keep.items():
            setattr(mod, name, val)
        W._SEALED[:] = self._sealed_keep
        return False

    # -- what a cycle does ---------------------------------------------------------
    def seal(self, orders):
        """Seal these orders through the witness, as run_once does. The answer
        comes from the stand-in covenant by the REAL runner's road (W._run), so
        the witness remembers it -- which is what the gate then looks for."""
        record = {"orders": [{k: o[k] for k in ("sym", "side", "qty", "usd")} for o in orders]}
        return W.seal_decision_result(self.cfg, record)

    def execute(self, orders, venues, seal=True, **kw):
        """covenant_trader.execute on the config and state this fixture holds,
        against the stand-in `venues`."""
        if seal:
            self.seal(orders)
        T.V.all_venues = lambda: list(venues)
        pf = kw.get("portfolio")
        if kw.pop("record_book", True) and isinstance(pf, G.State):
            # G2: as run_once does before it executes -- the measured book is in the state file
            self.st["book"] = G.book_snapshot(pf)
            self.st["equity_peak"], self.st["equity_start_of_day"] = pf.equity_peak, pf.equity_start_of_day
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(self.st, fh)
        res = T.execute(self.cfg, self.st, orders, True, [], **kw)
        return res

    def state_on_disk(self):
        with open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)


def order(sym="XLM", side="sell", usd=20.0, px=1.0, venue="fake"):
    return {"sym": sym, "side": side, "qty": usd / px, "usd": usd, "px": px,
            "rule": "test", "at": {venue: 1000.0}}


class Venue:
    """A stand-in venue: a trade-only key, nothing open, nothing filled, and a
    record of every place() call. `behaviour` is an exception to raise from
    place(), or None."""

    def __init__(self, name="fake", perms=None, behaviour=None):
        self.name, self.behaviour = name, behaviour
        self.perms = perms if perms is not None else {
            "can_trade": True, "can_transfer": False, "can_withdraw": False, "source": "stand-in"}
        self.calls, self.key_asked = [], 0

    def has_credentials(self):
        return True

    def key_permissions(self, fetch=None):
        self.key_asked += 1
        if isinstance(self.perms, Exception):
            raise self.perms
        return dict(self.perms)

    def open_orders_norm(self):
        return []

    def recent_fills(self, since=None):
        return []

    def place(self, sym, side, qty, live=False, ref_px=None, client_ref=None, permit=None,
              declared_usd=None):
        self.calls.append({"sym": sym, "side": side, "qty": qty, "live": live, "ref_px": ref_px,
                           "client_ref": client_ref, "permit": permit, "declared_usd": declared_usd})
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return {"descr": "stand-in", "txid": ("TX-%d" % len(self.calls)) if live else None}

    def live(self):
        return [c["live"] for c in self.calls]
