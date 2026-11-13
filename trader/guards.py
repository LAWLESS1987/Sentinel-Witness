#!/usr/bin/env python3
"""
guards.py -- circuit breakers. The part we had none of.

WHAT THIS IS AND WHY IT IS THE HIGHEST-VALUE ADDITION
  The rules so far answer "what should I hold?" Nothing answers "should I be
  trading at all right now?" Freqtrade ships a whole subsystem for that
  (protections: MaxDrawdown, StoplossGuard, CooldownPeriod, LowProfitPairs) and
  it is the piece most retail setups skip, because it never makes money -- it
  only stops you losing it faster.

  It needs no forecast. Every guard here is arithmetic on your own history.
  That matters because the one thing established across this whole project is
  that prediction did not survive out-of-sample while risk control replicated.
  This is more risk control.

DESIGN RULES
  * FAIL CLOSED. A guard that cannot evaluate BLOCKS. Same posture as the
    ethics gate: silence is not consent.
  * NO GUARD EVER FORCES A SALE. A circuit breaker that liquidates you at the
    bottom is worse than the loss it was stopping. This is the rule; the one
    below used to be stated in its place and is not the same claim.
  * Most guards block BUYS ONLY, and callers apply them that way -- see
    covenant_trader.preconditions(). Two do not, and both were added
    2026-09-04, so the older wording ("guards only ever block buys") is no
    longer true and has been corrected rather than left as a comment that
    quietly disagrees with the code:
      - ReserveFloor blocks a SELL that would cross the reserved half. Refusing
        to sell is not forcing a sale, so it keeps the rule above.
      - PerTradeCap and PerDayCap apply to EVERY order. They are throughput
        limits, and a runaway loop selling costs the same 100 bps a round trip
        as one buying, so restricting them to buys would leave the hole open on
        the side the planner actually emits.
  * Every block states the number that triggered it, so you can argue with the
    threshold instead of guessing at the reason.
"""
from __future__ import annotations
import datetime, json, math, os, time
from dataclasses import dataclass, field
from typing import List, Optional

import witness   # Sentinel-Witness: covenant holds the floors, a halt, the day's approval
import key_scope  # H9/R1: what a venue's key may do -- judged there, no network code


# ---- H1 (2026-11-12): WHAT COUNTS AS AN AMOUNT -------------------------------
# NaN fails every comparison. `nan < min` is False and `nan > room` is False, so
# an order of NaN dollars was neither under the minimum nor over the cap and
# went through both; a NaN row in the day's record made the day's total NaN,
# and `nan >= cap` is False, so the day was never full. A negative row
# subtracted from the day. This is the one reading of "a real amount", and every
# sum and comparison of dollars below asks it: a number (never a bool, never a
# string), finite, and above zero. Anything else is not skipped and not counted
# as zero -- the caller refuses.
def finite_number(x):
    """x as a float when it is an int or float (not a bool) and finite; else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def finite_positive(x):
    """x as a float when it is a finite number above zero; else None."""
    v = finite_number(x)
    return v if v is not None and v > 0 else None


def finite_nonnegative(x):
    """x as a float when it is a finite number at or above zero; else None."""
    v = finite_number(x)
    return v if v is not None and v >= 0 else None


BAD_AMOUNT = "order amount is not a finite number above zero"
BAD_SIDE = "order side is not exactly 'buy' or 'sell'"


# ---- H11 (2026-11-12): "NOT THERE" IS ONLY EVER FileNotFoundError --------------
# os.path.exists() answers False for a path it could not examine at all -- a
# permission error, a drive that has gone away, a name the OS will not parse.
# Two kinds of switch in this folder were built on it, and for both a False
# was the PERMISSIVE answer:
#
#   * the halt:  `if os.path.exists(HALT)` -- a TRADER_HALT that could not be
#     looked at read as "no halt", and orders went on;
#   * "never run": `if not os.path.exists(state): return []` -- a state file
#     that could not be looked at read as a trader that had never placed an
#     order, so the day's count and the lifetime buy total were a known zero.
#
# path_state() is the one reading. "absent" is said only when the OS itself
# says the name is not there; anything else that is not "present" is
# "unknown: <why>", and every caller treats unknown as the refusing answer:
# halted, or a state that exists and cannot be read.
_lstat = os.lstat          # a test replaces this to make a path un-examinable


def path_state(p):
    """'present', 'absent', or 'unknown: <why>' for the path `p`. lstat, so a
    directory entry of any kind named TRADER_HALT is a halt."""
    try:
        _lstat(p)
        return "present"
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError, TypeError) as e:
        return "unknown: %s" % type(e).__name__


def _absent(p):
    """True only when the OS says `p` is not there. Unknown is not absent."""
    return path_state(p) == "absent"


# ---- H4 (2026-11-12): THE CLOCK ONLY GOES FORWARD ----------------------------
# The day rolled over whenever the stored date merely DIFFERED from today's.
# Set the clock back a day, or cross a time zone westward near midnight, and
# the stored day "differs": the day's order count was cleared, the daily caps
# started again, and yesterday's approved plan was today's once more. So the
# state keeps the latest local day and the latest moment this trader has seen
# (last_seen_day, last_seen_at), only ever raised, and a clock that reads
# earlier than either is a refusal, not a new day.
CLOCK_BACK = "clock: the date or time reads EARLIER than this trader has already seen"
CLOCK_SLACK_S = 300.0     # an ordinary time-sync correction is not a clock set back


def local_day(now=None):
    """The local calendar day, YYYY-MM-DD -- the one reading roll_day,
    orders_today_from and covenant's daily plan (time.localtime) all use."""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def is_day(s):
    """True for a real calendar date written YYYY-MM-DD, and nothing else."""
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        return time.strftime("%Y-%m-%d", time.strptime(s, "%Y-%m-%d")) == s
    except ValueError:
        return False


def clock_back(trader_state, now=None):
    """Why the clock reads earlier than the trader's own record, or None.

    Reads `last_seen_day` and `day` (dates sort as text) and `last_seen_at`
    (epoch seconds, with CLOCK_SLACK_S of tolerance). A `last_seen_day` or
    `last_seen_at` that is present and is not a date or a finite number is a
    reason too: the mark that says how far the clock has been cannot be read,
    so it cannot be shown not to have gone back."""
    if not isinstance(trader_state, dict):
        return None
    t = time.time() if now is None else now
    if finite_number(t) is None:
        return CLOCK_BACK + " (the time now is not a finite number)"
    today = local_day(t)
    lsd = trader_state.get("last_seen_day")
    if lsd is not None and not is_day(lsd):
        return CLOCK_BACK + " (last_seen_day %r in the trader's state is not a date)" % (lsd,)
    seen = [d for d in (lsd, trader_state.get("day")) if is_day(d)]
    if seen and today < max(seen):
        return (CLOCK_BACK + ": today reads %s and the last day on record is %s. Nothing "
                "runs until the date is at or after it; correct the clock" % (today, max(seen)))
    lsa = trader_state.get("last_seen_at")
    if lsa is not None:
        at = finite_number(lsa)
        if at is None:
            return CLOCK_BACK + " (last_seen_at %r in the trader's state is not a finite number)" % (lsa,)
        if t < at - CLOCK_SLACK_S:
            return (CLOCK_BACK + ": the time now is %d s before the last cycle on record "
                    "(more than the %d s allowed for a time-sync correction)"
                    % (int(at - t), int(CLOCK_SLACK_S)))
    return None


@dataclass
class Verdict:
    allowed: bool
    guard: str
    reason: str


@dataclass
class State:
    """Everything the guards reason over. All of it is your own history."""
    equity_now: float
    equity_peak: float
    equity_start_of_day: float
    closed_trades: list          # [{sym, pnl, closed_at}] newest last
    last_sold: dict              # {sym: unix_ts}
    positions: dict              # {sym: value}
    cash: float
    quantities: dict = field(default_factory=dict)   # {sym: units held now}
    reserve_baseline: dict = field(default_factory=dict)  # {sym: units at the day the floor was set}
    # Orders already placed TODAY: [{usd: float}, ...]. None is not the same as
    # []: an empty list means "none placed, and I know that", None means "I
    # could not find out", and the cap guards block on the second. The
    # distinction is the whole reason this is not a plain default_factory.
    orders_today: Optional[list] = None
    # The buy budget's two numbers, both Optional for the same reason
    # orders_today is: None means "could not be established", and the guard
    # blocks on that rather than assuming a convenient zero.
    starting_total_usd: Optional[float] = None   # the book when the floor was set
    bought_total_usd: Optional[float] = None     # cumulative buy notional since
    # Fiat-funded buys in the trailing seven days: [{at: unix_ts, usd: float}].
    # None means "could not be established" and WeeklyBudget blocks on it.
    fiat_buys_week: Optional[list] = None
    now: float = field(default_factory=time.time)

    @property
    def total(self) -> float:
        return sum(self.positions.values()) + self.cash


class Guard:
    name = "guard"
    # WHICH ORDERS THIS GUARD CONSTRAINS: "buy", "sell", or "both".
    #
    # This used to be prose in the header and nothing in the code, and the cost
    # of that showed up on 2026-09-04. ReserveFloor is a SELL-side rule -- half
    # of every coin may not be sold, and XRP may not be sold at all -- but it
    # sits in DEFAULTS, and may_buy() asked every guard in DEFAULTS. Before
    # hold-only existed it happened to answer "no baseline recorded, no claim"
    # on the buy path and the muddle was invisible. The moment XRP became
    # hold-only it started refusing to BUY XRP, which is not the rule and is
    # not what anybody asked for: "may not be sold" is not "may not be bought".
    #
    # So the side is declared, may_buy() honours it, and evaluate() still
    # reports every guard -- the operator should see the reserve line whether
    # or not it bears on the question being asked.
    side = "buy"

    def check(self, st: State, sym: Optional[str] = None) -> Verdict:
        raise NotImplementedError


class MaxDrawdown(Guard):
    """Stop buying once the account is this far below its own high-water mark.

    Not a stop-loss -- it does not sell. It stops you adding while things are
    going against you, which is exactly when the urge to average down peaks."""
    name = "max_drawdown"
    def __init__(self, pct: float = 0.25):
        self.pct = pct
    def check(self, st, sym=None):
        if finite_number(st.equity_peak) is None or finite_number(st.equity_now) is None:
            return Verdict(False, self.name, "equity is not a finite number -- cannot evaluate, blocking")
        if st.equity_peak <= 0:
            return Verdict(False, self.name, "no peak recorded -- cannot evaluate, blocking")
        dd = (st.equity_peak - st.equity_now) / st.equity_peak
        if dd >= self.pct:
            return Verdict(False, self.name,
                           f"drawdown {dd:.1%} at/over limit {self.pct:.0%} "
                           f"(peak ${st.equity_peak:,.0f} -> now ${st.equity_now:,.0f})")
        return Verdict(True, self.name, f"drawdown {dd:.1%} under {self.pct:.0%}")


class DailyLossLimit(Guard):
    """One bad day should not become one bad day plus a revenge trade."""
    name = "daily_loss"
    def __init__(self, pct: float = 0.08):
        self.pct = pct
    def check(self, st, sym=None):
        if (finite_number(st.equity_start_of_day) is None
                or finite_number(st.equity_now) is None):
            return Verdict(False, self.name, "equity is not a finite number -- cannot evaluate, blocking")
        if st.equity_start_of_day <= 0:
            return Verdict(False, self.name, "no start-of-day equity -- blocking")
        chg = (st.equity_now - st.equity_start_of_day) / st.equity_start_of_day
        if chg <= -self.pct:
            return Verdict(False, self.name,
                           f"down {abs(chg):.1%} today, limit {self.pct:.0%}")
        return Verdict(True, self.name, f"today {chg:+.1%}")


class CooldownPeriod(Guard):
    """After selling something, do not buy it back for N days.

    Sell-then-immediately-rebuy is the most common way a rules system turns
    into churn: the rule fires, the price ticks the other way, and you undo it
    an hour later having paid two fees to end up where you started."""
    name = "cooldown"
    def __init__(self, days: float = 7):
        self.secs = days * 86400
        self.days = days
    def check(self, st, sym=None):
        if sym is None:
            return Verdict(True, self.name, "not asset-specific")
        t = st.last_sold.get(sym)
        if t is None:
            return Verdict(True, self.name, f"{sym} not sold recently")
        if finite_number(t) is None or finite_number(st.now) is None:
            return Verdict(False, self.name,
                           f"{sym}: the time it was last sold is not a finite number "
                           f"-- cannot evaluate, blocking")
        age = st.now - t
        if age < self.secs:
            return Verdict(False, self.name,
                           f"{sym} sold {age/86400:.1f}d ago, cooldown {self.days:.0f}d")
        return Verdict(True, self.name, f"{sym} last sold {age/86400:.1f}d ago")


class LossStreak(Guard):
    """N losing closed trades in a window means the current approach is not
    working NOW. Pause rather than push."""
    name = "loss_streak"
    def __init__(self, n: int = 4, window_days: float = 30):
        self.n, self.window = n, window_days * 86400
    def check(self, st, sym=None):
        for t in st.closed_trades:
            if (not isinstance(t, dict) or finite_number(t.get("closed_at", 0)) is None
                    or finite_number(t.get("pnl", 0)) is None):
                return Verdict(False, self.name,
                               "a closed trade on record has a time or a result that is "
                               "not a finite number -- cannot evaluate, blocking")
        recent = [t for t in st.closed_trades if st.now - t.get("closed_at", 0) <= self.window]
        losses = [t for t in recent if t.get("pnl", 0) < 0]
        if len(losses) >= self.n:
            return Verdict(False, self.name,
                           f"{len(losses)} losing trades in {self.window/86400:.0f}d "
                           f"(limit {self.n})")
        return Verdict(True, self.name, f"{len(losses)}/{self.n} recent losses")


class ConcentrationCap(Guard):
    """No single position past this share. Blocks buys that would breach it."""
    name = "concentration"
    def __init__(self, pct: float = 0.20):
        self.pct = pct
    def check(self, st, sym=None):
        if sym is None:
            return Verdict(True, self.name, "not asset-specific")
        tot = st.total
        if finite_number(tot) is None or finite_number(st.positions.get(sym, 0.0)) is None:
            return Verdict(False, self.name, "portfolio value is not a finite number -- blocking")
        if tot <= 0:
            return Verdict(False, self.name, "no portfolio value -- blocking")
        w = st.positions.get(sym, 0.0) / tot
        if w >= self.pct:
            return Verdict(False, self.name,
                           f"{sym} already {w:.1%}, cap {self.pct:.0%}")
        return Verdict(True, self.name, f"{sym} at {w:.1%}")


class CashFloor(Guard):
    """Keep a buffer. Buying the floor away is how you end up forced to sell."""
    name = "cash_floor"
    def __init__(self, pct: float = 0.10):
        self.pct = pct
    def check(self, st, sym=None):
        tot = st.total
        if finite_number(tot) is None or finite_number(st.cash) is None:
            return Verdict(False, self.name, "portfolio value is not a finite number -- blocking")
        if tot <= 0:
            return Verdict(False, self.name, "no portfolio value -- blocking")
        c = st.cash / tot
        if c < self.pct:
            return Verdict(False, self.name,
                           f"cash {c:.1%} below floor {self.pct:.0%}")
        return Verdict(True, self.name, f"cash {c:.1%}")


# HOLD-ONLY ASSETS. Asked 2026-09-04: "50 percent of current holdings
# excluding xrp can be sold and rebought". XRP is not half-reserved, it is
# wholly reserved -- the standing instruction on it has been hold-only since
# this account was first described, and the sentence above says "excluding"
# rather than "including at fifty percent". Nothing here may sell any part of
# it, and no rule in this program can add to this tuple: removing a symbol from
# it is an operator's edit, in the open, like every other loosening.
#
# REDEFINED 2026-09-07. Asked, in two messages: "everything except hbar
# chainlink and xrp can be considered available balance for trading", then
# "hold the current amount able to add and use for trading", confirmed as:
# today's quantity is a FLOOR, more may be bought, and everything above the
# floor is tradeable.
#
# So these three are no longer "never sellable". They are FULLY RESERVED AT
# THE BASELINE: reserved_pct is 1.0 of the recorded baseline where every other
# asset reserves 0.50 of it. Held == baseline (the case today) still means zero
# sellable, which is why this reads the same from outside until something is
# added. The difference appears the moment more is bought.
#
# AND THE FLOOR DOES NOT FOLLOW A PURCHASE UP. For a 50%-reserved asset,
# buying more raises the baseline -- half of a bigger holding is a bigger
# floor, which is the rule that was asked for in September. Applying that here
# would make "able to add and use for trading" impossible: every coin added
# would raise the floor above itself and be locked on arrival. So
# covenant_trader.reserve_baseline() freezes the baseline for these symbols.
# That is the whole content of "hold the CURRENT amount".
#
# WHAT THIS DOES NOT BIND. The operator, who said "unless i choose to sell
# myself". Every floor in this file constrains the PROGRAM's rules. Nothing
# here reaches his own hands at the exchange, and nothing should.
HOLD_ONLY = ("XRP", "HBAR", "LINK")

# The fraction of the BASELINE reserved for a HOLD_ONLY symbol. One, not a
# half: the whole of what he holds today stays put.
FULL_RESERVE_PCT = 1.0


TRADING_SCOPE_FIELDS = ("by", "at", "words")


def trading_scope(cfg):
    """The operator's trading scope from cfg["trading_scope"], or None.

    Added 2026-10-06 in Sentinel-Witness on his words: "We already have a rule
    about those three. Everything else is good to sell in trade." The three are
    HOLD_ONLY (XRP, HBAR, LINK) and THIS RECORD DOES NOT TOUCH THEM: they keep
    the 100% reserve of a frozen baseline (reserved_pct ignores `pct` for
    them), anything held above it stays tradeable, and adding to them stays
    allowed. What the record sets is the reserve on every OTHER coin --
    `open_reserve_pct`, the share of its baseline no rule may sell. 0 is fully
    open; the rule before it was 0.50 ("50% of every current coin should be off
    limits", 2026-09-04).

    A SCOPE IS A RECORD, like the Rule 5 waiver: `open_reserve_pct` a number in
    [0, 1] and who, when and in what words, each a non-empty string. Anything
    malformed is None, and None means the rule before it: half of every other
    coin reserved."""
    s = cfg.get("trading_scope") if isinstance(cfg, dict) else None
    if not isinstance(s, dict):
        return None
    pct = s.get("open_reserve_pct")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0.0 <= float(pct) <= 1.0:
        return None
    for k in TRADING_SCOPE_FIELDS:
        v = s.get(k)
        if not isinstance(v, str) or not v.strip():
            return None
    return {"open_reserve_pct": float(pct), "by": s["by"], "at": s["at"], "words": s["words"]}


# ---- H7 (2026-11-12): AN ARMING EXPIRES ---------------------------------------
# `armed: true` was one click in SENTINEL_ARM_AND_RUN.bat and stayed true until
# somebody remembered to edit it back: a trader armed for one cycle in October
# was still armed in November. And `if not cfg.get("armed")` took any truthy
# value -- the text "false" armed it.
#
# An arming is now the boolean true AND `armed_until`: an ISO-8601 time WITH
# its zone (2026-11-12T21:00:00Z), in the future, and at most ARMED_MAX_S
# ahead. Missing, malformed, zoneless, too far ahead or passed all mean
# DISARMED, with the reason. arming() is the one reading; preconditions asks
# it for every order, and covenant_trader.run_once runs the whole cycle as a
# rehearsal when it says no.
ARMED_MAX_S = 24 * 3600.0


def parse_utc(s):
    """Epoch seconds for an ISO-8601 time that carries its zone (a trailing Z
    or +hh:mm), or None. A time with no zone is refused rather than guessed:
    local and UTC differ by hours, and hours are what an expiry is made of."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t[-1] in "Zz":
        t = t[:-1] + "+00:00"
    try:
        d = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    try:
        return d.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def arming(cfg, now=None):
    """(armed, why). armed is True only for a complete, unexpired arming."""
    if not isinstance(cfg, dict) or not cfg.get("armed"):
        return False, "armed=false in trader_config.json"
    if cfg.get("armed") is not True:
        return False, ("DISARMED: armed is %r in trader_config.json, not the boolean true"
                       % (cfg.get("armed"),))
    t = time.time() if now is None else now
    if finite_number(t) is None:
        return False, "DISARMED: the time now is not a finite number, so no expiry can be checked"
    until = cfg.get("armed_until")
    if until is None:
        return False, ("DISARMED: armed=true but armed_until is missing -- an arming carries "
                       "its own expiry (an ISO time with its zone, at most %d hours ahead)"
                       % (ARMED_MAX_S // 3600))
    at = parse_utc(until)
    if at is None:
        return False, ("DISARMED: armed_until %r is not an ISO-8601 time with its zone "
                       "(for example 2026-11-12T21:00:00Z)" % (until,))
    if at <= t:
        return False, ("DISARMED: the arming expired at %s (%d minutes ago) -- arm again to "
                       "place live orders" % (until, int((t - at) // 60)))
    if at - t > ARMED_MAX_S:
        return False, ("DISARMED: armed_until %s is %.1f hours ahead; an arming lasts at most "
                       "%d hours" % (until, (at - t) / 3600.0, ARMED_MAX_S // 3600))
    return True, "armed until %s" % until


# ---- H6 (2026-11-12): WHAT AN ARMED CONFIG MAY NOT SWITCH OFF -----------------
# Three records and three switches in this trader's own gitignored config each
# removed a gate: rule5_waiver removed Rule 5; trading_scope.open_reserve_pct
# removed the 50% reserve on every coin but the hold-only three;
# daily_plan_required=false removed the day's approval; min_sealed_signals
# below the shipped 30 and rule5_require_significance=false removed Rule 5
# without even a waiver record. (seal_required is SW3's, in witness.py.)
#
# A DISARMED run may keep all of them -- it is a rehearsal, it places nothing,
# and it says REHEARSAL ONLY when it uses one. An ARMED config that carries
# any of them is refused, with the reason. The waiver and the scope are not
# deleted from the code or from anyone's config: they are records of what the
# operator chose on 2026-10-05 and 2026-10-06, and they stop being honoured
# with real orders until this section is changed, in the open, in this file.
SHIPPED_RESERVE_PCT = 0.50
SHIPPED_MIN_SEALED_SIGNALS = 30
WAIVER_NOT_HONOURED = ("armed with a Rule 5 waiver -- a waiver is honoured in a disarmed "
                       "rehearsal only; Rule 5 applies to every live order")


def live_terms_reasons(cfg, hypothetical=False):
    """Why an ARMED config is refused for what it switches off; [] when it is
    not, and always [] for a disarmed config -- unless `hypothetical`, which
    asks "what WOULD refuse this config if it were armed" for the rehearsal
    notice. (A flag rather than a copy of the config with the arming switch
    set: nothing on the trading path writes that switch, and test_f5 A2 scans
    for the text.)"""
    if not isinstance(cfg, dict) or not (cfg.get("armed") or hypothetical):
        return []
    out = []
    if rule5_waiver(cfg) is not None:
        out.append(WAIVER_NOT_HONOURED)
    s = trading_scope(cfg)
    if s is not None and s["open_reserve_pct"] < SHIPPED_RESERVE_PCT:
        out.append("armed with trading_scope.open_reserve_pct %g, below the shipped reserve "
                   "of %g -- refused; a reserve under the shipped floor is rehearsal-only"
                   % (s["open_reserve_pct"], SHIPPED_RESERVE_PCT))
    if cfg.get("daily_plan_required", True) is not True:
        out.append("armed with daily_plan_required not true -- refused; no live order "
                   "without the day's approval")
    m = cfg.get("min_sealed_signals", SHIPPED_MIN_SEALED_SIGNALS)
    if isinstance(m, bool) or not isinstance(m, int) or m < SHIPPED_MIN_SEALED_SIGNALS:
        out.append("armed with min_sealed_signals %r, below the shipped Rule 5 minimum of %d "
                   "-- refused" % (m, SHIPPED_MIN_SEALED_SIGNALS))
    if cfg.get("rule5_require_significance", True) is not True:
        out.append("armed with rule5_require_significance not true -- refused; the count "
                   "alone is not Rule 5")
    return out


def open_reserve_pct(cfg, default=0.50):
    """The reserve on every coin that is not HOLD_ONLY: the scope's, or the
    rule before it. The ONE reading -- plan() and the status line both ask."""
    s = trading_scope(cfg)
    return s["open_reserve_pct"] if s is not None else float(default)


def reserved_pct(sym, pct=0.50, hold_only=None):
    """The fraction of `sym`'s baseline that no rule may sell into."""
    return (FULL_RESERVE_PCT
            if sym in (HOLD_ONLY if hold_only is None else hold_only)
            else float(pct))


def sellable_units(held, base, sym, pct=0.50, hold_only=None):
    """Units of `sym` that may still be sold. The ONE implementation.

    covenant_trader's planner used to compute this inline with its own copy of
    the arithmetic, which is how the per-trade and per-day caps came to be
    enforced in one place and invisible in the other. Both call this now."""
    full = sym in (HOLD_ONLY if hold_only is None else hold_only)
    if base is None or held is None:
        # A fully-reserved asset with no recorded baseline FAILS CLOSED: with
        # no floor there is no "above the floor", so nothing of it is sellable.
        # Zero here is KNOWN and is not None -- None means "no claim either
        # way" and would let a caller fall through to its own default.
        return 0.0 if full else None
    return max(0.0, held - base * reserved_pct(sym, pct, hold_only))


class ReserveFloor(Guard):
    """Half of every coin is not for sale, and XRP is not for sale at all.

    Asked 2026-09-04: "50% of every current coin should be off limits", and
    then "50 percent of current holdings excluding xrp can be sold and
    rebought".

    THE RATCHET, which is why the baseline is FIXED AND STORED. Read the rule
    as "half of what I hold right now", evaluated per order, and it permits
    selling everything: sell half of 100 and 50 remain, then half of 50, then
    half of 25. Eight orders and the position is 0.4 units, and no single
    order ever broke the rule. A floor that moves down with the balance it is
    protecting is not a floor.

    So the baseline is written once to ops/RESERVE.json and the floor is half
    of THAT, not half of whatever is left. Selling can take a holding from 100
    to 50 and no further, whatever route it takes and however many orders it
    uses.

    RAISING the baseline is allowed -- buying more raises what half means, and
    a floor that ignored new coin would be a different rule than the one
    asked for. LOWERING it is not, because that is the ratchet wearing a
    different hat, and this class refuses to do it. Moving a floor down is an
    operator's decision, made by editing ops/RESERVE.json, where it leaves a
    mark.
    """
    name = "reserve_floor"
    side = "sell"          # it refuses SALES; it has never had a view on buying

    def __init__(self, pct: float = 0.50, hold_only=None):
        self.pct = float(pct)
        self.hold_only = tuple(HOLD_ONLY if hold_only is None else hold_only)

    def check(self, st, sym=None):
        if sym is None:
            return Verdict(True, self.name, "no symbol named; nothing to reserve")
        full = sym in self.hold_only
        base = (st.reserve_baseline or {}).get(sym)
        held = (st.quantities or {}).get(sym)
        if base is None or held is None:
            if full:
                return Verdict(False, self.name,
                               "%s is hold-only and has no recorded baseline, so "
                               "there is no floor to be above -- refusing" % sym)
            return Verdict(True, self.name,
                           f"{sym}: no baseline recorded, so this guard makes no claim")
        pct = reserved_pct(sym, self.pct, self.hold_only)
        floor = base * pct
        label = "hold-only: 100%% of the %.8g baseline" % base if full else                 "%.0f%% of the %.8g baseline" % (pct * 100, base)
        if held <= floor + 1e-12:
            return Verdict(False, self.name,
                           f"{sym}: {held:.8g} held is at or below the reserved "
                           f"{floor:.8g} ({label}) -- no sell may cross it")
        return Verdict(True, self.name,
                       f"{sym}: {held:.8g} held, {floor:.8g} reserved ({label}), "
                       f"{held - floor:.8g} sellable")

    @staticmethod
    def sellable(st, sym, pct=0.50, hold_only=None):
        """Units that may still be sold without touching the reserve. This is
        what a sizing rule must clamp to -- the check above refuses an order
        that starts below the floor, and this stops one from ending below it.
        A hold-only symbol reserves 100% of its baseline rather than 50%, so
        this returns whatever is held ABOVE that baseline -- zero while the
        holding is exactly the amount that was frozen, which is not the same as
        None: it is known, and it is none."""
        return sellable_units((st.quantities or {}).get(sym),
                              (st.reserve_baseline or {}).get(sym),
                              sym, pct, hold_only)


# ------------------------------------------------------------------ the caps
# WHY THESE ARE GUARDS AND NOT JUST CONFIG, added 2026-09-04 on request.
#
# The three numbers already existed -- max_order_usd, max_daily_notional_usd
# and max_orders_per_day are in trader_config.json and covenant_trader.py has
# enforced them since it was written. The problem was WHERE. They lived in one
# function, `preconditions()`, in the file that places orders. daily.py -- the
# report an operator actually reads every morning, which runs this guard stack
# and prints "may add" -- knew nothing about them. Two enforcement points, one
# of them blind, and the blind one is the one a person looks at.
#
# Moving them here gives them the two properties the rest of this file has:
# they FAIL CLOSED when they cannot evaluate, and they state the number that
# triggered them. covenant_trader.preconditions() now asks these classes
# instead of doing the arithmetic again, so there is one implementation.
#
# WHAT THE DAILY CAP IS FOR, which is not what DailyLossLimit is for.
# DailyLossLimit watches EQUITY, so it sees a bad day. It cannot see a runaway
# loop: a scheduler that fires every hour, or a planner that re-proposes the
# same order, can place many small orders that each pass every other guard and
# move equity very little per trade -- while the round trip costs 100 bps.
# Four orders a day at the shipped $300 daily cap is $1,200 of notional a
# month, which on a book this size is roughly 18% a year paid to the exchange
# before any market move at all. The cap is a cost control, and a bug control.
#
# H5 (2026-11-12): THE SHIPPED DEFAULTS WERE $100 / $300 / 4 (the paragraph
# above is written against them and is kept as the record of that). Every
# document in this folder states $25 an order, $50 a day, 2 orders a day --
# README "What has to say yes before a live order", MY_STRATEGY.md Rule 6
# ("$25 per order"), rule5_waiver's own docstring, trader_config.example.json
# -- so a config that left a cap out fell back to four times what was written
# down. The defaults are now those documented numbers, and the same numbers
# are HARD_CEILINGS: a config may LOWER a cap and may not raise one. A value
# above a ceiling is refused with the reason (config_reasons); it is never
# clamped, because a silently clamped number is a config that says one thing
# while the program does another. Raising a ceiling is an edit to this file,
# in the open, like HOLD_ONLY.
HARD_CEILINGS = {
    "max_order_usd": 25.0,
    "max_daily_notional_usd": 50.0,
    "max_orders_per_day": 2,
}
CAP_DEFAULTS = {
    "max_order_usd": 25.0,
    "max_daily_notional_usd": 50.0,
    "max_orders_per_day": 2,
    "min_order_usd": 5.0,
    # R6 contribution (2026-09-06): dollars the trader may put to work in any
    # trailing seven days. 0 is the shipped default and means NO contribution
    # path -- the operator sets the number in trader_config.json.
    "weekly_fiat_budget_usd": 0.0,
}
# Not a number, so it is not in caps() -- but it is read the same way and from
# the same file, and it defaults to the refusing answer.
ALLOW_FIAT_KEY = "allow_fiat_buys"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "trader_config.json")

# H5: THE WHOLE SHIPPED CONFIG, here because guards cannot import
# covenant_trader (see TRADER_STATE below for the loop). It must equal
# covenant_trader.DEFAULT_CONFIG; test_h05_config.py pins the two together.
SHIPPED_DEFAULTS = {
    "armed": False,
    "max_order_usd": 25.0,
    "max_daily_notional_usd": 50.0,
    "max_orders_per_day": 2,
    "min_order_usd": 5.0,
    "max_position_pct": 0.20,
    "min_cash_pct": 0.10,
    "seal_required": True,
    "min_sealed_signals": 30,
    "rule5_require_significance": True,
    "allow_fiat_buys": False,
    "weekly_fiat_budget_usd": 0.0,
    "xrpl_record": False,
    "xrpl_network": "testnet",
    "xrpl_allow_mainnet": False,
    "contribution_min_cash_pct": 0.10,
    "contribution_symbols": [],
    "node_ports": [5000],
    "node_key": "covenant_A.db.key",
    "loop_seconds": 3600,
    "symbols": [],
}


def with_defaults(cfg, skip=()):
    """`cfg` over the shipped defaults: a key the config leaves out takes the
    shipped value, never "off". Keys in `skip` are left as the config has them.
    Something that is not a dict is the shipped defaults alone (armed false)."""
    out = {k: (list(v) if isinstance(v, list) else v)
           for k, v in SHIPPED_DEFAULTS.items() if k not in skip}
    if isinstance(cfg, dict):
        out.update(cfg)
    return out


def config_reasons(cfg):
    """Why this config is refused, as a list; [] when it is not. H5.

    Each cap that is PRESENT must be a finite number above zero (a whole
    number for the order count), and at or under its ceiling in
    HARD_CEILINGS. A cap that is absent is not a reason: it takes the shipped
    default, which is the ceiling. Nothing here changes a value."""
    if not isinstance(cfg, dict):
        return ["config: it is not an object"]
    out = []
    for key, ceiling in HARD_CEILINGS.items():
        if key not in cfg:
            continue
        v = finite_positive(cfg[key])
        if v is None:
            out.append("config: %s is %r, not a finite number above zero -- refused"
                       % (key, cfg[key]))
        elif key == "max_orders_per_day" and v != int(v):
            out.append("config: %s is %r, not a whole number -- refused" % (key, cfg[key]))
        elif v > ceiling + 1e-9:
            out.append("config: %s is %g, above the hard ceiling of %g set in code "
                       "(guards.HARD_CEILINGS) -- refused, not clamped; a config may "
                       "only lower it" % (key, v, ceiling))
    if "min_order_usd" in cfg and finite_nonnegative(cfg["min_order_usd"]) is None:
        out.append("config: min_order_usd is %r, not a finite number at or above zero "
                   "-- refused" % (cfg["min_order_usd"],))
    # R4 (2026-11-12): the other numbers that shape an order, each held on the
    # side its own document states and no further (CONFIG_BOUNDS).
    for key, (lo, lo_open, hi, hi_open, said) in CONFIG_BOUNDS.items():
        if key not in cfg:
            continue
        v = finite_number(cfg[key])
        if v is None:
            out.append("config: %s is %r, not a finite number -- refused" % (key, cfg[key]))
            continue
        low_ok = v > lo if lo_open else v >= lo
        high_ok = v < hi if hi_open else v <= hi
        if not (low_ok and high_ok):
            out.append("config: %s is %g, outside %s%g, %g%s -- the bound is set in code "
                       "(guards.CONFIG_BOUNDS, from %s); refused, not clamped"
                       % (key, v, "(" if lo_open else "[", lo, hi, ")" if hi_open else "]", said))
    if ALLOW_FIAT_KEY in cfg and not isinstance(cfg[ALLOW_FIAT_KEY], bool):
        out.append("config: %s is %r, not true or false -- refused; a permission is not "
                   "granted by a value that merely looks like yes" % (ALLOW_FIAT_KEY, cfg[ALLOW_FIAT_KEY]))
    for key in ("symbols", "contribution_symbols"):
        if key in cfg and (not isinstance(cfg[key], list) or any(
                not isinstance(x, str) or not x.strip() for x in cfg[key])):
            out.append("config: %s is %r, not a list of symbols -- refused" % (key, cfg[key]))
    return out


# R4 (2026-11-12). Until now three caps had ceilings and every other number in
# the config was the config's to set in either direction: max_position_pct 0.9
# switched the concentration rule off, min_cash_pct 0 switched the cash sleeve
# off, weekly_fiat_budget_usd 100000 was a budget. Each of these is held, in
# code, on the side its own document states. (lowest, open?, highest, open?,
# where the number is written.) A config may move a value INSIDE its range;
# outside it the config is refused -- every cycle, armed or not -- not clamped.
CONFIG_BOUNDS = {
    # MY_STRATEGY.md Rule 1: "No single coin above 20% of your crypto total."
    "max_position_pct": (0.0, True, 0.20, False, "MY_STRATEGY.md Rule 1: no single coin above 20%"),
    # README, "What has to say yes": "the 10% cash floor"; MY_STRATEGY.md Rule 6.
    "min_cash_pct": (0.10, False, 1.0, True, "README: the 10% cash floor"),
    "contribution_min_cash_pct": (0.10, False, 1.0, True,
                                  "MY_STRATEGY.md Rule 6: nothing is put to work under 10% cash"),
    # MY_STRATEGY.md Rule 6: "a budget of 100 a week".
    "weekly_fiat_budget_usd": (0.0, False, 100.0, False, "MY_STRATEGY.md Rule 6: a budget of 100 a week"),
}

# EVERY GATE-RELEVANT KEY (GATE_KEYS, below), and what holds it. Prose that a
# test reads: test_r04_config_bounds.py fails if a key is added to GATE_KEYS
# and not accounted for here.
CONFIG_KEY_NOTES = {
    "armed": "no bound: a boolean; counts only as the boolean true with an unexpired armed_until (H7)",
    "armed_until": "bound: in the future and at most 24 hours ahead (guards.ARMED_MAX_S, H7)",
    "max_order_usd": "bound: at most $25 (HARD_CEILINGS, H5); a config may lower it",
    "max_daily_notional_usd": "bound: at most $50 (HARD_CEILINGS, H5); a config may lower it",
    "max_orders_per_day": "bound: at most 2, a whole number (HARD_CEILINGS, H5); a config may lower it",
    "min_order_usd": "no bound beyond finite and not negative: raising it only refuses more orders, "
                     "lowering it only allows smaller ones inside the same caps",
    "max_position_pct": "bound: above 0 and at most 0.20 (CONFIG_BOUNDS, R4)",
    "min_cash_pct": "bound: at least 0.10 and below 1 (CONFIG_BOUNDS, R4)",
    "seal_required": "bound when armed: must be true (witness.terms_reasons, SW3)",
    "min_sealed_signals": "bound when armed: a whole number, at least 30 (live_terms_reasons, H6)",
    "rule5_require_significance": "bound when armed: must be true (live_terms_reasons, H6)",
    "daily_plan_required": "bound when armed: must be true (live_terms_reasons, H6)",
    "rule5_waiver": "bound when armed: refused (live_terms_reasons, H6); honoured disarmed only",
    "trading_scope": "bound when armed: open_reserve_pct at least 0.50 (live_terms_reasons, H6)",
    "allow_fiat_buys": "no bound: a permission that is the operator's to give; must be exactly "
                       "true or false (R4), and absent means no",
    "weekly_fiat_budget_usd": "bound: 0 to $100 (CONFIG_BOUNDS, R4)",
    "contribution_min_cash_pct": "bound: at least 0.10 and below 1 (CONFIG_BOUNDS, R4)",
    "contribution_symbols": "no bound: it can only NARROW what the weekly contribution buys; "
                            "must be a list of symbols (R4)",
    "symbols": "no bound: it can only NARROW what is traded, and what it leaves out is still "
               "counted in the book (R3); must be a list of symbols (R4)",
    "covenant_home": "bound when armed: must be the pinned witness folder (witness.home_reasons, H6)",
}


def caps(path: Optional[str] = None) -> dict:
    """The operator's caps, or the shipped defaults where the file is silent.

    trader_config.json is deliberately NOT shipped -- it carries `armed`, and
    it is per-operator runtime state -- so this must work without it, and a
    missing file means "use the defaults", never a crash. A present file with a
    missing key means the same for that key."""
    out = dict(CAP_DEFAULTS)
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict):
            for k in out:
                if isinstance(cfg.get(k), (int, float)):
                    out[k] = cfg[k]
    except (OSError, ValueError):
        pass
    return out


def fiat_buys_week_from(trader_state: Optional[dict], now: Optional[float] = None):
    """Fiat-funded buys in the trailing seven days out of covenant_trader's
    state file, for guards.WeeklyBudget. None if the state is unknown (a
    reader that cannot see the week must not assume it is empty); a state
    with no record yet is a KNOWN empty week."""
    if trader_state is None or not isinstance(trader_state, dict):
        return None
    t = now if now is not None else time.time()
    got = trader_state.get("fiat_buys", [])
    if not isinstance(got, list):
        return None
    out = []
    for r in got:
        # H1: a row is a dict with a finite time and a finite amount above zero.
        # Anything else makes the week UNKNOWN (None), and WeeklyBudget blocks
        # on that; it is never dropped, and never counted as nothing.
        if not isinstance(r, dict):
            return None
        at, usd = finite_nonnegative(r.get("at")), finite_positive(r.get("usd"))
        if at is None or usd is None:
            return None
        if t - at <= 7 * 86400:
            out.append({"at": at, "usd": usd})
    return out


def orders_today_from(trader_state: Optional[dict], now: Optional[float] = None):
    """The day's orders out of covenant_trader's state file, or None if unknown.

    APPLIES THE SAME ROLLOVER covenant_trader.roll_day() does. The trader clears
    orders_today when the stored day is not today, and a reader that skips that
    step counts yesterday's orders against this morning -- which blocks rather
    than admits, so it is safe, but it is still wrong, and it would block every
    morning until the trader next ran.

    A state file that does not exist is not unknown: the trader has never run,
    so no orders have been placed, and that is a KNOWN zero."""
    if trader_state is None:
        return None
    if not isinstance(trader_state, dict):
        return None
    # H4: a clock that reads earlier than the record is not a new day. The
    # day's orders are then UNKNOWN (None), and the cap guards block on that --
    # where this used to answer [] ("a stale day, nothing placed") and hand the
    # whole day's count back.
    if clock_back(trader_state, now) is not None:
        return None
    day = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    if trader_state.get("day") != day:
        return []
    got = trader_state.get("orders_today")
    return list(got) if isinstance(got, list) else None


# covenant_trader.py owns this path and this env var. It is repeated rather
# than imported because covenant_trader imports daily, daily imports guards,
# and guards importing covenant_trader would close the loop. A test pins the
# two against each other so they cannot drift apart quietly.
TRADER_STATE = os.environ.get("COVENANT_TRADER_STATE") or os.path.join(
    os.path.expanduser("~"), ".covenant", "trader_state.json")


def fiat_buys_week_now(path: Optional[str] = None, now: Optional[float] = None):
    """The trailing week's fiat-funded buys straight off disk, for WeeklyBudget.
    Same contract as orders_today_now: a missing file is a KNOWN empty week
    (the trader has never run), a file that will not parse is unknown."""
    p = path or TRADER_STATE
    if _absent(p):               # H11: only a path the OS says is not there
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            return fiat_buys_week_from(json.load(fh), now)
    except (OSError, ValueError):
        return None


def orders_today_now(path: Optional[str] = None, now: Optional[float] = None):
    """The day's orders straight off disk: a list, or None if it cannot be told.

    A MISSING FILE IS A KNOWN ZERO, not an unknown. The trader writes its state
    the first time it runs; if the file is not there it has never run, so it has
    placed no orders, and blocking on that would mean the caps refuse every
    order until the caps have already been used once. A file that exists but
    will not parse IS unknown, and that blocks."""
    p = path or TRADER_STATE
    if _absent(p):               # H11
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            return orders_today_from(json.load(fh), now)
    except (OSError, ValueError):
        return None


def allow_fiat_buys(path: Optional[str] = None) -> bool:
    """Has the operator permitted buying with dollars? Default NO.

    Asked 2026-09-04: "not from fiat without permission". Absent, unreadable
    and malformed all mean no, which is the same posture `armed` takes -- a
    permission that can be granted by a missing file is not a permission."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh).get(ALLOW_FIAT_KEY) is True
    except (OSError, ValueError, AttributeError):
        return False


def bought_total_now(path: Optional[str] = None) -> Optional[float]:
    """Lifetime buy notional out of the trader's state, or None if unknown.

    A MISSING FILE IS A KNOWN ZERO, for the same reason orders_today_now()
    treats it that way: the trader has never run, so it has bought nothing. A
    file that exists and does not carry the figure IS unknown -- it was written
    by a build that did not keep the total, and guessing zero for it would hand
    back a whole budget that may already have been spent."""
    p = path or TRADER_STATE
    if _absent(p):               # H11
        return 0.0
    try:
        with open(p, encoding="utf-8") as fh:
            v = json.load(fh).get("bought_total_usd")
        return finite_nonnegative(v)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _proceeds(orders):
    """(raised by selling today, spent buying today), or None if a side is
    unreadable. An order whose side is not recorded cannot be counted on
    either line, and guessing which would be the whole question."""
    sold = bought = 0.0
    for o in orders:
        if not isinstance(o, dict):
            return None
        side = str(o.get("side", "")).lower()
        usd = finite_positive(o.get("usd"))     # H1: NaN, inf, zero, negative -> unknown
        if usd is None:
            return None
        if side == "sell":
            # H10 (2026-11-12): A SALE IS PROCEEDS ONLY FOR WHAT A VENUE HAS
            # CONFIRMED FILLED. A row the trader writes now carries `fill`:
            # UNCONFIRMED when the order is placed, then PARTIAL or CONFIRMED
            # with `filled_usd` once the venue's own fills say so
            # (covenant_trader.apply_fills). A resting limit order that never
            # fills raised nothing, and used to count in full the moment it
            # was placed. A row with no `fill` key was written before fills
            # were tracked and counts as it always did; a `fill` this does not
            # know makes the day unknown.
            fill = o.get("fill")
            if fill is None:
                sold += usd
            elif fill in ("UNCONFIRMED", "PARTIAL", "CONFIRMED"):
                got = finite_nonnegative(o.get("filled_usd", 0.0))
                if got is None:
                    return None
                sold += got
            else:
                return None
        elif side == "buy":
            bought += usd
        else:
            return None
    return sold, bought


def _spent(orders):
    """Notional placed today, or None if any entry's size cannot be read.

    An order whose amount is unreadable is not worth zero. It is an order that
    happened for an unknown amount, and the file's first design rule says a
    guard that cannot evaluate blocks -- so the sum goes unknown and the caps
    refuse, rather than quietly counting a possibly-large order as nothing.
    Found by F7's C9, which had asserted the opposite."""
    tot = 0.0
    for o in orders:
        if not isinstance(o, dict) or "usd" not in o:
            return None
        # H1 (2026-11-12): finite and above zero, or the sum is unknown. NaN
        # made the total NaN, which is not >= any cap; a negative row gave the
        # day back room it had already used; a zero row is an order that was
        # recorded for nothing, which no order this program places can be.
        usd = finite_positive(o["usd"])
        if usd is None:
            return None
        tot += usd
    return tot


class PerDayCap(Guard):
    """How much may be traded in one day, by count and by notional.

    Blocks on history alone -- it needs no proposed order. Once the count is
    used up or the notional is spent, nothing more goes today, and the guard
    says which of the two stopped it."""
    name = "per_day_cap"
    side = "both"          # a throughput limit: a runaway loop selling costs
                           # the same 100 bps a round trip as one buying

    def __init__(self, max_orders: Optional[int] = None,
                 max_notional: Optional[float] = None,
                 config_path: Optional[str] = None):
        # WHAT WAS PASSED IS KEPT; WHAT WAS NOT IS READ AT EVERY CHECK.
        # DEFAULTS is built at import, so resolving the config here would
        # freeze the caps at start-up -- and covenant_trader --loop runs for
        # days. An operator who tightens a cap mid-run should have it apply on
        # the next cycle rather than the next restart, and tightening is
        # exactly the edit someone makes in a hurry. Explicit arguments still
        # win, so a test can pin fixed numbers.
        self._max_orders, self._max_notional = max_orders, max_notional
        self._config_path = config_path

    @property
    def max_orders(self):
        c = caps(self._config_path)
        return int(c["max_orders_per_day"] if self._max_orders is None
                   else self._max_orders)

    @property
    def max_notional(self):
        c = caps(self._config_path)
        return float(c["max_daily_notional_usd"] if self._max_notional is None
                     else self._max_notional)

    def check(self, st, sym=None):
        # H5: the numbers this guard was handed, against the ceilings in code.
        over = config_reasons({"max_orders_per_day": self.max_orders,
                               "max_daily_notional_usd": self.max_notional})
        if over:
            return Verdict(False, self.name, "; ".join(over))
        if st.orders_today is None:
            return Verdict(False, self.name,
                           "the day's orders are unknown (no trader state could be "
                           "read) -- cannot evaluate, blocking")
        n, spent = len(st.orders_today), _spent(st.orders_today)
        if n >= self.max_orders:
            return Verdict(False, self.name,
                           f"{n} order(s) already today, limit {self.max_orders}")
        if spent is None:
            return Verdict(False, self.name,
                           f"one of today's {n} order(s) records no readable amount "
                           f"(missing, not a number, not finite, or not above zero) "
                           f"-- the day's notional cannot be totalled, blocking")
        if spent >= self.max_notional:
            return Verdict(False, self.name,
                           f"${spent:,.2f} of notional already today, cap "
                           f"${self.max_notional:,.2f}")
        return Verdict(True, self.name,
                       f"{n}/{self.max_orders} orders, ${spent:,.2f}/"
                       f"${self.max_notional:,.2f} notional used today")


class PerTradeCap(Guard):
    """No single order over max_order_usd, and none under min_order_usd.

    THE SIZE OF A PROPOSED ORDER IS NOT IN State, deliberately -- State is the
    account's own history, and a guard that judged one order would have to be
    re-run per order with a different argument. So this follows ReserveFloor's
    shape: check() refuses the situation where no legal order EXISTS, and
    largest_allowed() is the number a sizing rule clamps to, which is what
    stops an order from ending over the line."""
    name = "per_trade_cap"
    side = "both"          # as above

    def __init__(self, max_usd: Optional[float] = None,
                 min_usd: Optional[float] = None,
                 max_orders: Optional[int] = None,
                 max_notional: Optional[float] = None,
                 config_path: Optional[str] = None):
        # Read at every check, not at construction -- see PerDayCap.__init__.
        self._max_usd, self._min_usd = max_usd, min_usd
        self._max_orders, self._max_notional = max_orders, max_notional
        self._config_path = config_path

    @property
    def max_usd(self):
        c = caps(self._config_path)
        return float(c["max_order_usd"] if self._max_usd is None else self._max_usd)

    @property
    def min_usd(self):
        c = caps(self._config_path)
        return float(c["min_order_usd"] if self._min_usd is None else self._min_usd)

    @property
    def max_orders(self):
        c = caps(self._config_path)
        return int(c["max_orders_per_day"] if self._max_orders is None
                   else self._max_orders)

    @property
    def max_notional(self):
        c = caps(self._config_path)
        return float(c["max_daily_notional_usd"] if self._max_notional is None
                     else self._max_notional)

    def largest_allowed(self, st) -> Optional[float]:
        """The biggest order that may be placed right now, or None if unknown.

        Both caps bind at once: the per-order ceiling, and whatever is left of
        today's notional. Returning the minimum is what makes many small orders
        unable to do what one large one may not -- the same ratchet ReserveFloor
        exists to stop, in the other direction."""
        if st.orders_today is None:
            return None
        if len(st.orders_today) >= self.max_orders:
            return 0.0
        spent = _spent(st.orders_today)
        if spent is None:
            return None
        return max(0.0, min(self.max_usd, self.max_notional - spent))

    def check(self, st, sym=None):
        # H5: as PerDayCap.check.
        over = config_reasons({"max_order_usd": self.max_usd, "min_order_usd": self.min_usd,
                               "max_orders_per_day": self.max_orders,
                               "max_daily_notional_usd": self.max_notional})
        if over:
            return Verdict(False, self.name, "; ".join(over))
        room = self.largest_allowed(st)
        if room is None:
            why = ("the day's orders are unknown (no trader state could be read)"
                   if st.orders_today is None else
                   "one of today's orders records no readable amount")
            return Verdict(False, self.name, why + " -- cannot evaluate, blocking")
        if room < self.min_usd:
            return Verdict(False, self.name,
                           f"${room:,.2f} of headroom left, under the "
                           f"${self.min_usd:,.2f} minimum order -- nothing legal "
                           f"can be placed today")
        return Verdict(True, self.name,
                       f"orders capped at ${self.max_usd:,.2f}; ${room:,.2f} "
                       f"placeable now")


# SENTINEL-WITNESS: the same file covenant_trader.RESERVE_PATH reads -- one
# set of floors and one starting book, in covenant, never a copy here.
RESERVE_PATH = witness.reserve_path()


def starting_total(path: Optional[str] = None) -> Optional[float]:
    """The book's value when the floor was set, or None if never recorded."""
    try:
        with open(path or RESERVE_PATH, encoding="utf-8") as fh:
            v = json.load(fh).get("starting_total_usd")
        return finite_positive(v)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def set_starting_total(total: float, path: Optional[str] = None) -> Optional[float]:
    """Record the starting book ONCE. Never overwrites; returns what stands.

    ONE WRITER: covenant_trader.py, from a real portfolio read. daily.py calls
    starting_total() and only reads.

    It was briefly both, and that was a defect worth recording rather than
    quietly correcting. daily.py is driven by test_d3_daily_guards.py with a
    fabricated $44 book, so a test run could have written the number that
    anchors this budget for ever. It did not, only because a real run happened
    to go first and this function never overwrites -- which is luck, not
    design. A figure that fixes how much of someone's money may be spent is not
    something a program should settle from whatever it happened to read on a
    Tuesday, and it is certainly not something a test should be able to set.

    It never raises the number either, which is the opposite of what
    reserve_baseline() does for per-asset quantities and is deliberate. That
    baseline rises when more coin is bought, because the rule is "half of every
    coin". This one is "half of the STARTING amount", and a starting amount
    that grows is not a starting amount. If money is deposited and the budget
    should grow with it, that is an operator's edit to private/RESERVE.json,
    where it leaves a mark."""
    have = starting_total(path)
    if have is not None:
        return have
    # "NEVER RECORDED" AND "CANNOT BE READ" BOTH ARRIVE HERE AS None. A78
    # (2026-09-10). starting_total() returns None for an absent file, for an
    # absent key, AND for a file that will not parse -- and only the first two
    # mean "not set yet". On the third, the write below rebuilds `data` from
    # {} and persists it, which deletes the reserve floors and re-anchors this
    # lifetime budget to today's book. Measured: one torn-file cycle turned
    # $100 of remaining buy budget into $2,100, and the new anchor was then
    # protected by the very "never overwrites" invariant that had just failed.
    #
    # Line 472 of this file already states the rule: a file that exists and
    # will not parse IS unknown, and that blocks. Returning None makes
    # BuyBudget.check refuse with its existing "no starting book value
    # recorded" verdict, which is the fail-closed direction.
    p = path or RESERVE_PATH
    if not _absent(p):           # H11: present, or could not be examined
        try:
            with open(p, encoding="utf-8") as fh:
                if not isinstance(json.load(fh), dict):
                    raise ValueError("RESERVE.json is not an object")
        except (OSError, ValueError):
            return None
    if finite_positive(total) is None:
        return None
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data["starting_total_usd"] = float(total)
        data.setdefault("_what", "")
        data["_what"] = (data["_what"] + " " if data["_what"] else "") + (
            "starting_total_usd is the book at the moment the floor was set; "
            "buying is capped at pct_buyable of it, cumulatively, for ever.")
        data.setdefault("pct_buyable", 0.50)
        # ATOMIC (A78). This is one of the two writers of RESERVE.json, and a
        # kill between open("w") and the last byte leaves a torn file that
        # both readers must now refuse. Do not manufacture the emergency you
        # fail closed on.
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except OSError:
        return None
    return float(total)


class BuyBudget(Guard):
    """Buying may spend half of what the book was worth at the start. Total.

    Asked 2026-09-04: "buys are allowed with 50% of starting amount", beside
    the earlier "50% of every current coin should be off limits". The two are
    the same shape from opposite ends -- half the book cannot be sold, and half
    of what it started at is what may be spent -- and they need the same
    defence, because "half of what I have now" ratchets in this direction too:
    spend half, and half of what remains is another spend, and the budget never
    runs out. So the baseline is the STARTING total, fixed and stored, and the
    spend is CUMULATIVE and never forgotten.

    WHAT COUNTS AGAINST IT IS NEW MONEY, NOT ROTATION. Asked and settled
    2026-09-04: "50 percent of current holdings excluding xrp can be sold and
    rebought to gain yield and purchase other assets". Selling XLM and buying
    SOL with those proceeds is the intended mechanism, so charging it to the
    budget would switch the mechanism off after about sixty-nine rotations at
    the $50 daily cap -- a cap that had never let a dollar in. So the figure
    accumulated is the FIAT portion of each buy: whatever part of it was not
    covered by that day's sale proceeds. A rotation adds nothing.

    That keeps it un-gameable in the way that matters. Each dollar that enters
    the book is counted once and never forgotten, and no sequence of sells and
    buys can spend a dollar twice. Reaching the end means half the STARTING
    book has been put in, which is the point at which someone should look up
    rather than continue. Raising it is an operator's edit to
    private/RESERVE.json.

    BUYS ONLY. It never touches a sale: refusing to let someone stop is not
    something this file does."""
    name = "buy_budget"

    def __init__(self, pct: float = 0.50, reserve_path: Optional[str] = None):
        self.pct = float(pct)
        self._reserve_path = reserve_path

    def remaining(self, st) -> Optional[float]:
        """Budget left to spend on buys, or None if it cannot be told. A sizing
        rule clamps to this -- check() refuses a purchase once the budget is
        gone, and this stops one from going past the end of it."""
        start = (st.starting_total_usd if st.starting_total_usd is not None
                 else starting_total(self._reserve_path))
        if (finite_positive(start) is None
                or finite_nonnegative(st.bought_total_usd) is None):
            return None
        return max(0.0, start * self.pct - float(st.bought_total_usd))

    def check(self, st, sym=None):
        start = (st.starting_total_usd if st.starting_total_usd is not None
                 else starting_total(self._reserve_path))
        if start is None:
            return Verdict(False, self.name,
                           "no starting book value recorded in private/RESERVE.json, "
                           "so the buy budget cannot be evaluated -- blocking. It is "
                           "written on the first run that can read the portfolio.")
        if st.bought_total_usd is None:
            return Verdict(False, self.name,
                           "the cumulative amount already spent on buys is unknown "
                           "(no trader state could be read) -- cannot evaluate, blocking")
        if finite_positive(start) is None or finite_nonnegative(st.bought_total_usd) is None:
            # H1: NaN spent is not >= any budget, so it used to read as room left.
            return Verdict(False, self.name,
                           "the starting book or the cumulative amount spent on buys is "
                           "not a finite number at or above zero -- cannot evaluate, blocking")
        budget = start * self.pct
        spent = float(st.bought_total_usd)
        if spent >= budget:
            return Verdict(False, self.name,
                           f"${spent:,.2f} spent on buys against a ${budget:,.2f} budget "
                           f"({self.pct:.0%} of the ${start:,.2f} starting book) -- "
                           f"the budget is used up")
        return Verdict(True, self.name,
                       f"${budget - spent:,.2f} of the ${budget:,.2f} buy budget left "
                       f"({self.pct:.0%} of the ${start:,.2f} starting book)")


class FiatBuyPermission(Guard):
    """Rotating crypto you already hold is not the same act as spending dollars.

    Asked 2026-09-04: "not from fiat without permission", alongside "buys are
    allowed with 50% of starting amount" and the older "can buy but can't
    withdraw from my bank".

    The distinction is real and the program can see it, but only if it wrote
    down which side each order was. Selling XLM and buying SOL with the
    proceeds moves value between assets already owned; buying SOL out of the
    dollar balance spends money. From a balance sheet alone the two look
    identical, which is why covenant_trader now records `side` on every booked
    order -- so "funded by today's sales" is a number rather than a guess.

    So: a buy is permitted up to what selling raised today and has not already
    been spent. Beyond that it is coming out of dollars, and it waits for
    permission -- `allow_fiat_buys: true` in trader_config.json, an operator's
    edit that leaves a mark, exactly like `armed`.

    THE HEADROOM IS TODAY'S, not all time. A rotation is a thing that happens
    within a session; treating a sale from three months ago as funding today's
    purchase would make "funded by sales" true of almost any buy, and the rule
    would mean nothing.

    BUYS ONLY, and it never forces a sale."""
    name = "fiat_permission"

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path

    def headroom(self, st) -> Optional[float]:
        """What may still be bought without touching dollars, or None if it
        cannot be told. Infinite (returned as None's opposite: a float) is not
        used -- when fiat is permitted the guard simply allows."""
        if st.orders_today is None:
            return None
        got = _proceeds(st.orders_today)
        if got is None:
            return None
        sold, bought = got
        return max(0.0, sold - bought)

    def check(self, st, sym=None):
        if allow_fiat_buys(self._config_path):
            return Verdict(True, self.name,
                           "buying with dollars is permitted in trader_config.json "
                           "(allow_fiat_buys)")
        if st.orders_today is None:
            return Verdict(False, self.name,
                           "the day's orders are unknown, so what selling raised "
                           "today cannot be told -- cannot evaluate, blocking")
        got = _proceeds(st.orders_today)
        if got is None:
            return Verdict(False, self.name,
                           "one of today's orders does not record which side it was, "
                           "so sale proceeds cannot be separated from spending -- "
                           "cannot evaluate, blocking")
        sold, bought = got
        room = sold - bought
        if room <= 0:
            return Verdict(False, self.name,
                           f"nothing left from today's sales to buy with "
                           f"(${sold:,.2f} raised, ${bought:,.2f} already spent) -- "
                           f"a purchase now comes out of dollars, and that needs "
                           f"allow_fiat_buys in trader_config.json")
        return Verdict(True, self.name,
                       f"${room:,.2f} of today's sale proceeds may be re-bought "
                       f"without spending dollars (${sold:,.2f} raised, "
                       f"${bought:,.2f} spent)")


class WeeklyBudget(Guard):
    """Dollars put to work in any trailing seven days may not exceed the
    operator's weekly figure (weekly_fiat_budget_usd in trader_config.json).

    Asked 2026-09-06: "a budget of 100 a week". This is the ceiling the R6
    contribution rule sizes itself under, and the backstop if it ever does
    not. Counts fiat-funded buys only -- a rotation funded by today's sales is
    not new money (FiatBuyPermission draws that line). Reads the figure at
    every check, like the other caps, so a mid-week edit applies next cycle.
    A budget of 0 -- the shipped default -- means R6 is off and this guard does
    not constrain; the other guards still apply to any buy."""
    name = "weekly_budget"
    side = "buy"
    WINDOW_S = 7 * 86400

    def __init__(self, budget: Optional[float] = None, config_path: Optional[str] = None):
        self._budget, self._config_path = budget, config_path

    @property
    def budget(self):
        c = caps(self._config_path)
        return float(c["weekly_fiat_budget_usd"] if self._budget is None else self._budget)

    def spent(self, st) -> Optional[float]:
        if st.fiat_buys_week is None:
            return None
        total = 0.0
        if finite_number(st.now) is None:
            return None
        for r in st.fiat_buys_week:
            # H1: the same reading fiat_buys_week_from applies -- a row that is
            # not a finite time and a finite amount above zero makes the week
            # unknown, and check() blocks on that.
            if not isinstance(r, dict):
                return None
            at, usd = finite_nonnegative(r.get("at")), finite_positive(r.get("usd"))
            if at is None or usd is None:
                return None
            if st.now - at <= self.WINDOW_S:
                total += usd
        return total

    def headroom(self, st) -> Optional[float]:
        s = self.spent(st)
        return None if s is None else max(0.0, self.budget - s)

    def check(self, st, sym=None):
        b = self.budget
        if b <= 0:
            # No budget means the R6 contribution path is off (it checks the
            # same number before emitting anything). It does not mean every
            # buy is forbidden: an operator adding by hand under the other
            # guards is not bound by a contribution schedule that does not exist.
            return Verdict(True, self.name,
                           "no weekly contribution budget set (R6 off) -- not constraining")
        s = self.spent(st)
        if s is None:
            return Verdict(False, self.name,
                           "the week's fiat buys are unknown (no readable record) -- blocking")
        if s >= b:
            return Verdict(False, self.name,
                           f"${s:,.2f} of the ${b:,.2f} weekly budget already used")
        return Verdict(True, self.name,
                       f"${s:,.2f}/${b:,.2f} of the weekly budget used, ${b - s:,.2f} left")


DEFAULTS = [MaxDrawdown(0.25), DailyLossLimit(0.08), CooldownPeriod(7),
            LossStreak(4, 30), ConcentrationCap(0.20), CashFloor(0.10),
            ReserveFloor(0.50), PerDayCap(), PerTradeCap(), BuyBudget(0.50),
            FiatBuyPermission(), WeeklyBudget()]


class GuardStack:
    """Evaluates every guard and reports ALL blocks, not just the first.

    Short-circuiting on the first failure hides the rest, so you fix one thing,
    rerun, hit the next, and never see the shape of the problem."""
    def __init__(self, guards: Optional[List[Guard]] = None):
        self.guards = guards if guards is not None else DEFAULTS

    def evaluate(self, st: State, sym: Optional[str] = None):
        out = []
        for g in self.guards:
            try:
                out.append(g.check(st, sym))
            except Exception as e:
                out.append(Verdict(False, g.name,
                                   f"guard raised {type(e).__name__}: {e} -- blocking"))
        return out

    def may_buy(self, st: State, sym: str):
        """Whether SYM may be added to. Sell-side guards are not asked, because
        a rule about what may be sold has no view on what may be bought -- see
        Guard.side for the day that stopped being merely tidy."""
        buy_side = {g.name for g in self.guards if getattr(g, "side", "buy") != "sell"}
        v = self.evaluate(st, sym)
        blocks = [x for x in v if not x.allowed and x.guard in buy_side]
        return (len(blocks) == 0), blocks, v

    def report(self, st: State, sym: Optional[str] = None):
        v = self.evaluate(st, sym)
        tag = f" for {sym}" if sym else ""
        print(f"  GUARDS{tag}")
        for x in v:
            print(f"    [{'ok  ' if x.allowed else 'BLOCK'}] {x.guard:<14} {x.reason}")
        blocks = [x for x in v if not x.allowed]
        print(f"    -> buying {'ALLOWED' if not blocks else 'BLOCKED by ' + ', '.join(b.guard for b in blocks)}")
        return not blocks


# ===========================================================================
# THE PRECONDITIONS. ONE IMPLEMENTATION, TWO CALLERS.       (2026-09-07)
#
# Until today covenant_trader.preconditions() was the only thing that knew
# what may go live, and it lived in the file that places orders. The other
# path to a real order -- sentinel_witness/tradeGate.js -> seal_service.py --
# reached covenant_trader.seal_decision() and nothing else, so it recorded a
# decision through the ethics gate and then applied NONE of: armed,
# TRADER_HALT, the per-trade and per-day caps, Rule 5, or the guard stack.
#
# That is the same split the caps had before 2026-09-04, when they were
# enforced in preconditions() and invisible to daily.py -- and it is the same
# fix: move the rule here, where both callers can ask it, and leave ONE
# implementation. The trader's copy was deleted in the same commit; a
# consolidation that leaves the old body behind has not consolidated anything.
#
# WHY THIS CAN ONLY TIGHTEN. The answer is built as
#     base reasons  +  caller-specific reasons          (APPEND ONLY)
# so a caller may add a reason to refuse and can never remove one. The trader
# adds nothing and therefore behaves exactly as before. The sentinel went from
# zero checks to all of them plus two of its own, and there is no expressible
# way for it to end up more permissive than the trader.
#
# WHY guards.py AND NOT covenant_trader.py. covenant_trader imports daily,
# daily imports guards; guards importing covenant_trader would close the loop.
# guards.py already repeats CONFIG_PATH and TRADER_STATE for that exact
# reason, and imports only json/os/time/dataclasses/typing, so this adds
# nothing to daily.py's dependency cone.
# ===========================================================================

HALT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TRADER_HALT")


def load_config(path: Optional[str] = None) -> dict:
    """The whole trader config as a dict, OVER THE SHIPPED DEFAULTS.

    H5 (2026-11-12). This returned the file's own keys and nothing else, so a
    config that simply left `seal_required` and `min_sealed_signals` out had no
    seal requirement and no Rule 5: preconditions() reads them with .get(), and
    absent read as off. covenant_trader.load_config has always merged its
    DEFAULT_CONFIG; this reader -- the one the sentinel path uses -- did not.

    Unreadable or malformed still makes `armed` false: it reads as the shipped
    defaults alone, which are disarmed."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return with_defaults(cfg if isinstance(cfg, dict) else None)
    except (OSError, ValueError):
        return with_defaults(None)


def load_trader_state(path: Optional[str] = None):
    """covenant_trader's state, or None if it exists and will not parse.

    A MISSING FILE IS A KNOWN EMPTY STATE, not an unknown one -- the rule
    orders_today_now() already applies, for the same reason: the trader writes
    this the first time it runs, and treating "never run" as unknown would
    block every order until one had already been placed."""
    p = path or TRADER_STATE
    if _absent(p):               # H11
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


RULE5_WAIVER_FIELDS = ("by", "at", "words")


def rule5_waiver(cfg):
    """The operator's waiver of Rule 5 from cfg["rule5_waiver"], or None.

    Added 2026-10-05 in Sentinel-Witness on the operator's choice "Trade now
    within caps": turn Rule 5 off, keep $25/order, $50/day, 2 orders/day and the
    hold-only floors. A WAIVER IS A RECORD, NOT A MISSING CHECK: it must say
    waived=true exactly, and who, when, and in what words -- each a non-empty
    string. Anything less (absent, false, "true", a field missing or blank, not
    an object) is no waiver and Rule 5 refuses as before. The trader prints the
    waiver every cycle and seals it with the decision, so the chain records
    each day an order went live without Rule 5."""
    w = (cfg or {}).get("rule5_waiver") if isinstance(cfg, dict) else None
    if not isinstance(w, dict) or w.get("waived") is not True:
        return None
    for k in RULE5_WAIVER_FIELDS:
        v = w.get(k)
        if not isinstance(v, str) or not v.strip():
            return None
    return w


def preconditions(order, cfg=None, st=None, sealed_ok=None, guard_blocks=None,
                  caller="trader", config_path=None, state_path=None, now=None):
    """Every reason THIS order may not go live. Empty list means clear.

    order        {"side": "buy"|"sell", "usd": float, "sym": str}
    cfg / st     loaded from disk when None
    sealed_ok    True  -- already sealed and admitted
                 False -- sealing was attempted and refused
                 None  -- THE CALLER SEALS AFTER THIS AND ANDS THE ANSWER
                          ITSELF. Only False produces the "not sealed" reason,
                          because a caller that gates before it seals would
                          otherwise deadlock: seal_required is true and at gate
                          time the seal has not happened. A caller that passes
                          None owes the conjunction. seal_service.py does NOT
                          use this -- it seals first and passes the real
                          answer -- but the reading has to exist, or the order
                          of the two steps becomes load-bearing.
    guard_blocks the guard stack's blocking reasons, or None for "the guard
                 stack could not be evaluated" -- which is NOT the same as [].
    caller       "trader" or "sentinel". Adds reasons, never removes them.
    """
    bad = []
    # H5: a key the config leaves out takes the SHIPPED value, never "off" --
    # for a config read from disk (load_config) and for one handed in. The cap
    # keys are left out of the fill for a handed-in config because they have
    # their own fallback through caps(config_path), below.
    _from_disk = cfg is None
    cfg = (load_config(config_path) if cfg is None
           else with_defaults(cfg, skip=tuple(CAP_DEFAULTS)))
    if _from_disk:
        # H6: a config read from disk here (the path with no cycle around it)
        # is held to the armed terms and the pinned witness folder. A caller
        # that hands in its own cfg owes that check; covenant_trader.run_once
        # makes it in witness_ready before anything else happens.
        for r in live_terms_reasons(cfg) + witness.home_reasons(cfg):
            if r not in bad:
                bad.append(r)
    if st is None:
        st = load_trader_state(state_path)
    if st is None:
        bad.append(STATE_UNREADABLE)
        st = {}

    # H7: an arming is the boolean true with an unexpired armed_until, or it is
    # not an arming. arming() says which, and why.
    _armed, _arm_why = arming(cfg, now)
    if not _armed:
        bad.append(_arm_why)
    # H4: said in its own words as well as through the caps going unknown.
    _clock = clock_back(st, now)
    if _clock:
        bad.append(_clock)
    # H11 (2026-11-12): A HALT THAT CANNOT BE LOOKED AT IS A HALT. These were
    # os.path.exists(), which answers False for a path it could not examine.
    _halt = path_state(HALT)
    if _halt == "present":
        bad.append("TRADER_HALT file present")
    elif _halt != "absent":
        bad.append("TRADER_HALT could not be checked (%s) -- treated as a halt" % _halt)
    # A halt dropped in covenant stops this trader too: whoever reaches for
    # TRADER_HALT should not have to remember which folder trades now.
    try:
        _cov_halt = witness.halt_path(cfg)
    except Exception as e:                                       # noqa: BLE001
        _cov_halt = None
        bad.append("TRADER_HALT in covenant could not be checked (%s) -- treated as a halt"
                   % type(e).__name__)
    if _cov_halt:
        _halt = path_state(_cov_halt)
        if _halt == "present":
            bad.append("TRADER_HALT file present in covenant")
        elif _halt != "absent":
            bad.append("TRADER_HALT in covenant could not be checked (%s) -- treated as a halt"
                       % _halt)
    # 7. THE DAY'S PLAN IS APPROVED (2026-09-12, the operator's decision: "i'll
    # have to daily approve of the strategy it lays out"). No order goes live
    # without a signed approve for TODAY's plan, matching its hash --
    # covenant_daily_plan.py. cfg "daily_plan_required" false switches it off,
    # and only that.
    if cfg.get("daily_plan_required", True):
        # SENTINEL-WITNESS: the approvals are covenant's (ops/daily_plan/), so
        # the question is asked there. witness.py turns every failure to ask
        # into a reason, so this can only add refusals.
        try:
            bad.extend(witness.daily_plan_gate_reasons(now=now, cfg=cfg))
        except Exception as e:                                   # noqa: BLE001
            bad.append("daily plan gate unavailable (%s: %s) -- nothing may go live" % (type(e).__name__, str(e)[:80]))

    # THE CAPS ARE GUARDS, and this asks them rather than repeating their
    # arithmetic. A number absent from cfg falls back through caps() to the
    # shipped default, so a caller handing in a partial config still gets a
    # cap rather than none.
    c = caps(config_path)

    def _n(key):
        v = cfg.get(key)
        return v if finite_number(v) is not None else c[key]

    # H5: THE CEILINGS. The numbers about to be used -- the config's where it
    # states them, the fallback's where it does not -- are checked against
    # HARD_CEILINGS, and a number above one (or not a number) is a reason for
    # THIS order, said in words. Nothing is clamped.
    bad.extend(config_reasons({k: (cfg[k] if k in cfg else c[k])
                               for k in ("max_order_usd", "max_daily_notional_usd",
                                         "max_orders_per_day", "min_order_usd")}))
    # R4: and every other bounded number in the config (CONFIG_BOUNDS), which
    # have no fallback to resolve first.
    for r in config_reasons(cfg):
        if r not in bad:
            bad.append(r)

    gs = State(equity_now=0.0, equity_peak=0.0, equity_start_of_day=0.0,
               closed_trades=[], last_sold={}, positions={}, cash=0.0,
               orders_today=orders_today_from(st, now))
    per_trade = PerTradeCap(max_usd=_n("max_order_usd"), min_usd=_n("min_order_usd"),
                            max_orders=_n("max_orders_per_day"),
                            max_notional=_n("max_daily_notional_usd"))
    per_day = PerDayCap(max_orders=_n("max_orders_per_day"),
                        max_notional=_n("max_daily_notional_usd"))
    for v in (per_day.check(gs), per_trade.check(gs)):
        if not v.allowed:
            bad.append(f"{v.guard}: {v.reason}")
    room = per_trade.largest_allowed(gs)
    # H1 (2026-11-12): THE ORDER'S OWN NUMBERS, BEFORE ANY COMPARISON. This was
    # `float(order.get("usd") or 0.0)`: NaN is truthy, is not under the minimum
    # and is not over the room, so an order of NaN dollars cleared both caps.
    # A side that is not exactly "buy" or "sell" matters for the same reason:
    # the guard stack is applied to `side == "buy"`, so "BUY" skipped it.
    if order.get("side") not in ("buy", "sell"):
        bad.append("%s (got %r) -- refusing" % (BAD_SIDE, order.get("side")))
    if "qty" in order and finite_positive(order.get("qty")) is None:
        bad.append("order quantity is not a finite number above zero (got %r) -- refusing"
                   % (order.get("qty"),))
    usd = finite_positive(order.get("usd"))
    if usd is None:
        bad.append("%s (got %r) -- refusing" % (BAD_AMOUNT, order.get("usd")))
    elif usd < _n("min_order_usd"):
        bad.append(f"${usd:,.2f} under min_order_usd ${_n('min_order_usd'):,.2f}")
    elif room is not None and room > 0 and usd > room + 1e-9:
        # `room > 0` on purpose. When the day is fully used the two guards
        # above have already said so, in their own words, with their own
        # numbers; adding "over the $0.00 placeable now" makes one condition
        # read as three problems.
        bad.append(f"${usd:,.2f} over the ${room:,.2f} placeable now "
                   f"(order cap ${_n('max_order_usd'):,.2f}, daily notional "
                   f"cap ${_n('max_daily_notional_usd'):,.2f})")

    if cfg.get("seal_required") and sealed_ok is False:
        bad.append("decision not sealed to the chain")

    min_sig = cfg.get("min_sealed_signals", 0)
    _waiver = rule5_waiver(cfg)
    if _waiver is not None and cfg.get("armed"):
        # H6 (2026-11-12): ARMED, THE WAIVER IS NOT HONOURED. It is said, once,
        # and Rule 5 is then applied below exactly as if no waiver were there.
        if WAIVER_NOT_HONOURED not in bad:
            bad.append(WAIVER_NOT_HONOURED)
        _waiver = None
    if _waiver is not None:
        # THE OPERATOR WAIVED RULE 5 (2026-10-05), by a record in his config,
        # not by deleting the check. The record is still computed, printed and
        # sealed every cycle; it simply stops refusing. Every other line in
        # this function still applies.
        #
        # H6: reached only DISARMED now -- a rehearsal, which places nothing.
        pass
    elif st.get("sealed_signals", 0) < min_sig:
        bad.append(f"Rule 5: {st.get('sealed_signals', 0)} sealed signals on "
                   f"record, need {min_sig}")
    elif cfg.get("rule5_require_significance", True):
        r5 = st.get("rule5") or {}
        # The count is met; the record must also mean something. A losing or
        # luck-shaped record with 30 rows is the answer Rule 5 exists to give,
        # not a licence.
        if not r5.get("clears"):
            bad.append(f"Rule 5: {r5.get('why') or 'no scored record in state'}")

    # Guards gate BUYS only -- a guard never stops a risk-reducing sale.
    if guard_blocks and order.get("side") == "buy":
        bad.append("guards: " + ", ".join(guard_blocks))

    return bad + _caller_reasons(caller, order, guard_blocks)


# ---- H8 (2026-11-12): THE APPROVAL BINDS THE ORDERS ---------------------------
#
# The daily plan gate above asks covenant one question: is today's plan
# approved? On yes, covenant_trader.execute placed whatever plan() computed at
# run time. Nothing compared the two. The operator approved a plan at 07:10
# and the orders placed at 09:00 were the planner's of 09:00 -- usually the
# same, never checked, and after a price move or a config edit, not the same.
#
# approval_reasons() holds ONE order to covenant's record of what was approved
# (witness.approval_record). The rules are here, not in covenant, so that they
# are in the repository that places the order and is tested for it:
#
#   * A DECLINE ANYWHERE IN THE DAY'S RECORD IS FINAL FOR THAT DAY. An approve
#     row after it does not undo it.
#   * AN APPROVAL LASTS APPROVAL_VALID_S (12 hours) from the moment it was
#     given, and then it has expired. No document in this folder states a
#     validity; the operator approves between about 07:00 and 11:20
#     (test_g4_money_gates.py), and 12 hours keeps an order inside the day it
#     was approved for.
#   * THE ORDER MUST BE ONE OF THE PLAN'S PROPOSED ORDERS: the same symbol,
#     the same side, and a size no larger than the proposed one and no more
#     than APPROVAL_SIZE_TOLERANCE (2%) under it. A plan with no orders
#     approves no orders.
#   * EACH PROPOSED ORDER IS GOOD FOR ONE PLACEMENT. Orders already in today's
#     record use up the proposed orders they match, so the same approved order
#     cannot go out in two cycles on one day.
APPROVAL_VALID_S = 12 * 3600.0
APPROVAL_SIZE_TOLERANCE = 0.02
APPROVAL_FUTURE_SLACK_S = 300.0
APPROVAL_UNAVAILABLE = "approval binding unavailable"
APPROVAL_DECLINED = "today's plan was DECLINED"


def _approved_orders(approval):
    """The record's proposed orders as [(SYM, side, usd)], or None if any is
    not a symbol, a side and a finite amount above zero."""
    rows = approval.get("orders")
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        sym, side, usd = r.get("sym"), r.get("side"), finite_positive(r.get("usd"))
        if not isinstance(sym, str) or not sym.strip() or side not in ("buy", "sell") or usd is None:
            return None
        out.append((sym.strip().upper(), side, usd))
    return out


def _size_fits(usd, proposed):
    """Never larger than proposed; at most the tolerance under it."""
    return proposed * (1.0 - APPROVAL_SIZE_TOLERANCE) - 1e-9 <= usd <= proposed + 1e-9


def approval_reasons(order, approval, orders_today=None, now=None):
    """Why THIS order is not covered by today's approval. [] means it is."""
    t = time.time() if now is None else now
    if not isinstance(approval, dict) or approval.get("ok") is not True:
        why = approval.get("why") if isinstance(approval, dict) else "no record"
        return ["%s (%s) -- no order can be matched to an approval, so none goes live"
                % (APPROVAL_UNAVAILABLE, str(why)[:200])]
    if finite_number(t) is None:
        return ["%s (the time now is not a finite number)" % APPROVAL_UNAVAILABLE]
    today = local_day(t)
    if approval.get("date") != today:
        return ["%s (the record is for %r and today is %s)"
                % (APPROVAL_UNAVAILABLE, approval.get("date"), today)]
    plan_hash = approval.get("plan_hash")
    decisions = approval.get("decisions")
    if not isinstance(plan_hash, str) or not plan_hash.strip() or not isinstance(decisions, list):
        return ["%s (the record carries no plan hash or no list of decisions)" % APPROVAL_UNAVAILABLE]
    approved_at = []
    for d in decisions:
        if not isinstance(d, dict) or d.get("decision") not in ("approve", "decline"):
            return ["%s (a decision row in the day's record cannot be read)" % APPROVAL_UNAVAILABLE]
        if d["decision"] == "decline":
            # FINAL. Whatever else the day's record holds, before or after.
            return ["%s (%s) -- a decline is final for the day, whatever was approved "
                    "before or after it" % (APPROVAL_DECLINED, today)]
        at = finite_number(d.get("at"))
        if at is None:
            return ["%s (an approval in the day's record has no readable time)" % APPROVAL_UNAVAILABLE]
        if d.get("plan_hash") == plan_hash:
            approved_at.append(at)
    if not approved_at:
        return ["no approval on record for today's plan (hash %s...)" % plan_hash[:12]]
    at = max(approved_at)
    if at > t + APPROVAL_FUTURE_SLACK_S:
        return ["%s (the approval is dated %d s in the future)" % (APPROVAL_UNAVAILABLE, int(at - t))]
    if t - at > APPROVAL_VALID_S:
        return ["today's approval has EXPIRED: it was given %.1f hours ago and lasts %d -- "
                "approve the plan again" % ((t - at) / 3600.0, APPROVAL_VALID_S // 3600)]
    proposed = _approved_orders(approval)
    if proposed is None:
        return ["%s (a proposed order in the approved plan cannot be read)" % APPROVAL_UNAVAILABLE]
    sym = str(order.get("sym") or "").strip().upper()
    side, usd = order.get("side"), finite_positive(order.get("usd"))
    if not sym or side not in ("buy", "sell") or usd is None:
        return ["the order is not one an approval can be matched to (symbol, side, amount)"]
    if not proposed:
        return ["the approved plan proposes NO orders, so it approves none -- %s %s $%.2f is not in it"
                % (side, sym, usd)]
    # What today's record has already used up.
    free = list(proposed)
    for row in orders_today or []:
        if not isinstance(row, dict):
            continue
        r_sym = str(row.get("sym") or "").strip().upper()
        r_usd = finite_positive(row.get("usd"))
        if r_usd is None:
            continue
        for i, (p_sym, p_side, p_usd) in enumerate(free):
            if p_sym == r_sym and p_side == row.get("side") and _size_fits(r_usd, p_usd):
                del free[i]
                break
    same = [p for p in proposed if p[0] == sym and p[1] == side]
    if not same:
        return ["%s %s is not in the approved plan (it proposes: %s)"
                % (side, sym, ", ".join("%s %s $%.2f" % (p[1], p[0], p[2]) for p in proposed))]
    if any(_size_fits(usd, p[2]) for p in free if p[0] == sym and p[1] == side):
        return []
    if any(_size_fits(usd, p[2]) for p in same):
        return ["%s %s $%.2f is in the approved plan, and today's record shows it already "
                "placed -- one approved order is one placement" % (side, sym, usd)]
    return ["%s %s $%.2f does not match the approved size (%s): an order may be no larger "
            "than the one approved and at most %.0f%% under it"
            % (side, sym, usd, ", ".join("$%.2f" % p[2] for p in same),
               APPROVAL_SIZE_TOLERANCE * 100)]


# ===========================================================================
# R1 (2026-11-12): THE LIVE GATE TAKES NO DECISION FROM ITS CALLER.
#
# Review round one, confirmed by the outside reviewer: every check above is
# made on the config dict and the state dict a caller hands in, plus three
# things the caller simply asserts -- "it was sealed", "the guards did not
# block", "here is what was approved". covenant_trader.run_once hands in the
# truth. A Python caller that hands in {"armed": true, "armed_until": ...,
# "daily_plan_required": false, "min_sealed_signals": 0} and sealed_ok=True
# cleared all of it, and execute() went live on the result.
#
# live_gate() is the one thing that now turns "this order may go live" from
# false to true, and it judges FROM DISK, itself:
#
#   * the config: trader_config.json, read here, over the shipped defaults --
#     its caps and ceilings, its arming and expiry, the armed terms, the
#     pinned witness folder. A config handed in is only COMPARED: if it
#     differs from the file in any gate-relevant key, that is a refusal.
#   * the state: the trader's state file, read here -- the day's count, the
#     lifetime totals, the Rule 5 record, the clock marks. A state handed in
#     is only compared, the same way.
#   * the seal: witness.seal_reasons -- what THIS process has seen covenant
#     admit, not a caller's True.
#   * the approval: witness.approval_record, asked here.
#   * the venue's key: key_scope.key_reasons, asked here.
#   * the buy-side guards: evaluated here, on the caller's portfolio
#     MEASUREMENTS (equity, positions, cash) and the disk state's history.
#     A caller may add a block; it cannot remove one.
#
# When nothing refuses, the gate records a PERMIT in this module's own memory
# -- one order, one venue, one use, PERMIT_VALID_S -- and returns its id. A
# venue adapter's place(live=True) sends nothing without consuming one
# (venues._live_permit), so calling the venue directly places nothing either.
#
# WHAT THIS IS NOT. It is not a defence against code running in this process
# that reaches into this module's privates, replaces its functions, or edits
# the files on disk: Python has no such boundary, and the files belong to the
# same OS user. It closes the road the reviewer walked -- the public
# functions' own arguments.
# ===========================================================================
GATE_KEYS = (
    "armed", "armed_until", "max_order_usd", "max_daily_notional_usd", "max_orders_per_day",
    "min_order_usd", "max_position_pct", "min_cash_pct", "seal_required", "min_sealed_signals",
    "rule5_require_significance", "daily_plan_required", "rule5_waiver", "trading_scope",
    "allow_fiat_buys", "weekly_fiat_budget_usd", "contribution_min_cash_pct",
    "contribution_symbols", "symbols", "covenant_home")
STATE_GATE_KEYS = ("day", "last_seen_day", "last_seen_at", "orders_today", "bought_total_usd",
                   "fiat_buys", "sealed_signals", "rule5", "last_sold", "closed_trades",
                   "equity_peak", "equity_start_of_day", "book")          # G2: the measured book too

# G2 (review round 1, 2026-11-12). The buy-side guards -- drawdown, the day's loss, the cash
# floor, concentration -- are arithmetic on the book: equity now, its peak, its value at the
# start of the day, cash, positions. Until this change the gate took all of those from the
# State its caller handed it, so a caller in the same process could hand over a healthy book
# for an account that was down by half. The trader now records the book it measured in its
# state file before it executes (covenant_trader.run_once), and the gate reads it from there.
# What it is handed can only refuse: a portfolio that differs from the recorded book is a reason.
BOOK_VALID_S = 300.0          # a recorded book older than this is not judged against


def book_snapshot(state, now=None):
    """The part of a measured State that the gate will judge a buy against, as it is written to
    the state file."""
    return {"at": time.time() if now is None else now,
            "equity_now": state.equity_now, "cash": state.cash,
            "positions": dict(state.positions or {})}


def book_from_disk(dst, now=None):
    """(book, None) from the trader's state as read from disk, or (None, why). `book` holds
    equity_now, equity_peak, equity_start_of_day, cash and positions, every one a finite number
    that makes sense; anything else is a refusal, never a default."""
    snap = dst.get("book") if isinstance(dst, dict) else None
    if not isinstance(snap, dict):
        return None, "the trader's state file holds no measured book"
    at, eq, cash = finite_number(snap.get("at")), finite_positive(snap.get("equity_now")), finite_nonnegative(snap.get("cash"))
    pos = snap.get("positions")
    if at is None or eq is None or cash is None or not isinstance(pos, dict) \
            or any(not isinstance(k, str) or finite_nonnegative(v) is None for k, v in pos.items()):
        return None, "the measured book in the trader's state file cannot be read"
    t = time.time() if now is None else now
    age = t - at
    if not -CLOCK_SLACK_S <= age <= BOOK_VALID_S:
        return None, "the measured book in the trader's state file is %d s old (limit %d)" % (age, BOOK_VALID_S)
    peak, start = finite_positive(dst.get("equity_peak")), finite_positive(dst.get("equity_start_of_day"))
    if peak is None or start is None:
        return None, "the trader's state file holds no equity peak or no start-of-day equity"
    if eq > peak * (1.0 + 1e-9):
        return None, "the measured book is larger than the peak on record, which only ever rises"
    return {"equity_now": eq, "equity_peak": peak, "equity_start_of_day": start, "cash": cash,
            "positions": {k: float(v) for k, v in pos.items()}}, None


def _book_differs(portfolio, book):
    """Which of the caller's book numbers differ from the recorded book."""
    def near(a, b):
        a, b = finite_number(a), finite_number(b)
        return a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))
    out = [k for k in ("equity_now", "cash") if not near(getattr(portfolio, k, None), book[k])]
    mine = getattr(portfolio, "positions", None)
    if not isinstance(mine, dict) or set(mine) != set(book["positions"]) \
            or any(not near(mine[k], book["positions"][k]) for k in mine):
        out.append("positions")
    return out
PERMIT_VALID_S = 120.0
_PERMITS = {}


def _same_value(a, b):
    """Equal as config or state values: numbers by value (25 and 25.0), a bool
    only with a bool, anything else by its JSON."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(a) and num(b):
        return float(a) == float(b)
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    try:
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    except (TypeError, ValueError):
        return False


def config_differs(handed, disk):
    """The gate-relevant keys in which a config handed in differs from the one
    on disk (both over the shipped defaults). [] when it does not."""
    a = with_defaults(handed if isinstance(handed, dict) else None)
    b = with_defaults(disk if isinstance(disk, dict) else None)
    return [k for k in GATE_KEYS if not _same_value(a.get(k), b.get(k))]


def state_differs(handed, disk):
    """The gate-relevant keys in which a state handed in differs from the
    state file. [] when it does not."""
    a = handed if isinstance(handed, dict) else {}
    b = disk if isinstance(disk, dict) else {}
    return [k for k in STATE_GATE_KEYS if not _same_value(a.get(k), b.get(k))]


# ---- ONE CYCLE AT A TIME (C1, 2026-11-12) -----------------------------------
# Found by an outside review and reproduced: two cycles that read the same state
# file before either wrote to it BOTH passed the gate -- "the state handed in
# differs from the file" cannot tell them apart, because neither differs -- and
# each one's save then dropped the other's pending row, so the day's count was
# one short and the per-day ceilings could be passed. The scheduled task and a
# cycle started by hand are two such cycles.
#
# The lock is the operating system's, on a file beside the state file: a byte
# range on Windows (msvcrt), flock elsewhere. It is taken without waiting and
# belongs to the open handle, so the system drops it when the process ends,
# however it ends. There is no "stale lock" to judge and no pid or timestamp in
# the file to believe or forge. A cycle that cannot take it does nothing.
#
# What requires it: run_once (the whole cycle), execute, save_state, the gate
# (no permit without it) and consume_permit (no send without it).
#
# WHAT IT IS NOT. Like everything in this file it is a rule for well-behaved
# code under one account. Another process of the same account can hold the lock
# for ever (every cycle then refuses: it fails closed), and on POSIX it can
# unlink the file while a cycle holds it, after which a second cycle locks a new
# file of the same name. Windows refuses to delete a file that is open this way.
_CYCLE_LOCKS = {}            # lock path -> [file descriptor, depth]


def cycle_lock_path():
    return TRADER_STATE + ".lock"


def _lock_fd(fd, take):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


def take_cycle_lock():
    """(True, None) when this process holds the trader's cycle lock -- newly, or
    once more (it counts, so a holder may ask again) -- else (False, why). Never
    waits. Every True is owed one release_cycle_lock()."""
    path = cycle_lock_path()
    held = _CYCLE_LOCKS.get(path)
    if held is not None:
        held[1] += 1
        return True, None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        return False, "the trader's cycle lock %s cannot be opened (%s)" % (path, type(e).__name__)
    try:
        _lock_fd(fd, True)
    except OSError:
        os.close(fd)
        return False, "another trader cycle holds the cycle lock %s" % path
    except Exception as e:                                       # noqa: BLE001
        os.close(fd)
        return False, "the trader's cycle lock %s cannot be taken (%s)" % (path, type(e).__name__)
    _CYCLE_LOCKS[path] = [fd, 1]
    return True, None


def release_cycle_lock():
    """Give back one take_cycle_lock(); the last one frees the lock."""
    path = cycle_lock_path()
    held = _CYCLE_LOCKS.get(path)
    if held is None:
        return
    held[1] -= 1
    if held[1] > 0:
        return
    del _CYCLE_LOCKS[path]
    try:
        _lock_fd(held[0], False)
    except Exception:                                            # noqa: BLE001
        pass                                                     # closing the handle frees it too
    try:
        os.close(held[0])
    except OSError:
        pass


def holds_cycle_lock():
    return cycle_lock_path() in _CYCLE_LOCKS


def _issue_permit(venue_name, sym, side, usd):
    """Record a permit for one live order and return its id. Called by
    live_gate when nothing refused, and by nothing else in this program."""
    import secrets
    now = time.time()
    for k in [k for k, p in _PERMITS.items() if p["used"] or p["expires"] < now]:
        del _PERMITS[k]
    pid = secrets.token_hex(16)
    _PERMITS[pid] = {"venue": str(venue_name), "sym": str(sym).strip().upper(),
                     "side": str(side), "usd": float(usd), "expires": now + PERMIT_VALID_S,
                     "used": False}
    return pid


def consume_permit(pid, venue_name, sym, side):
    """The declared dollars permit `pid` covers, marking it used. ValueError
    when there is no such permit for this venue, symbol and side, or it has
    been used or has expired."""
    p = _PERMITS.get(pid) if isinstance(pid, str) else None
    if p is None:
        raise ValueError("there is no such permit")
    if not holds_cycle_lock():                                   # C1
        raise ValueError("this process does not hold the trader's cycle lock")
    if p["used"]:
        raise ValueError("the permit has already been used")
    if time.time() > p["expires"]:
        raise ValueError("the permit has expired")
    if (p["venue"], p["sym"], p["side"]) != (str(venue_name), str(sym).strip().upper(), str(side)):
        raise ValueError("the permit is for another order")
    p["used"] = True
    return p["usd"]


def live_gate(order, venue, cfg=None, st=None, portfolio=None, caller_blocks=None):
    """(reasons, permit). `permit` is an id only when `reasons` is empty.

    order          {"sym", "side", "usd", "qty"}
    venue          the adapter the order would go to (asked about its key)
    cfg, st        what the caller holds -- COMPARED with disk, never judged
    portfolio      a guards.State of the book as the caller measured it; needed
                   for a buy, whose guards are evaluated here
    caller_blocks  reasons the caller wants added. Append only.

    Reads the clock, the config, the state, the witness and the venue itself.
    There is deliberately no `now`, no `sealed_ok`, no `approval` and no path
    argument: each would be a decision, or a way to choose one."""
    reasons = []
    name = getattr(venue, "name", None)
    if venue is None or not isinstance(name, str):
        return ["live gate: no venue to send the order to"], None

    # -- one cycle at a time (C1) -------------------------------------------------
    if not holds_cycle_lock():
        reasons.append("live gate: this process does not hold the trader's cycle lock (%s), so "
                       "another cycle could be counting the same day's orders -- refused"
                       % cycle_lock_path())

    # -- the config, from disk -------------------------------------------------
    cstate = path_state(CONFIG_PATH)
    if cstate == "absent":
        reasons.append("live gate: there is no trader_config.json on disk (%s), so nothing "
                       "is armed" % CONFIG_PATH)
    disk = load_config(CONFIG_PATH)
    if cfg is not None:
        diff = config_differs(cfg, disk)
        if diff:
            reasons.append("live gate: the config handed in differs from trader_config.json on "
                           "disk in %s -- refused; the gate judges the file, not what it is "
                           "handed" % ", ".join(diff))

    # -- the state, from disk ---------------------------------------------------
    dst = load_trader_state(TRADER_STATE)
    if dst is None:
        dst = {}                      # preconditions below says STATE_UNREADABLE
    elif st is not None:
        diff = state_differs(st, dst)
        if diff:
            reasons.append("live gate: the state handed in differs from the trader's state "
                           "file in %s -- refused; the day's count is read from the file"
                           % ", ".join(diff))

    # -- the seal, from what this process saw covenant admit ---------------------
    try:
        seal_why = list(witness.seal_reasons(order))
    except Exception as e:                                       # noqa: BLE001
        seal_why = ["seal: could not be checked (%s)" % type(e).__name__]

    # -- the buy-side guards, evaluated here -------------------------------------
    blocks = []
    if order.get("side") == "buy":
        if not isinstance(portfolio, State):
            reasons.append("live gate: no portfolio was supplied, so the buy-side guards (cash "
                           "floor, concentration, budgets) could not be evaluated")
        else:
            import dataclasses
            measured, book_why = book_from_disk(dst)             # G2: the book is read, not handed in
            if measured is None:
                reasons.append("live gate: %s, so the buy-side guards (drawdown, the day's loss, "
                               "cash floor, concentration) could not be evaluated -- refused" % book_why)
                blocks = ["no measured book on record"]
            else:
                off = _book_differs(portfolio, measured)
                if off:
                    reasons.append("live gate: the portfolio handed in differs from the book the "
                                   "trader recorded in %s -- refused; the gate judges the record"
                                   % ", ".join(off))
                book = dataclasses.replace(
                    portfolio, equity_now=measured["equity_now"], equity_peak=measured["equity_peak"],
                    equity_start_of_day=measured["equity_start_of_day"], cash=measured["cash"],
                    positions=dict(measured["positions"]),
                    orders_today=orders_today_from(dst),
                    bought_total_usd=dst.get("bought_total_usd"),
                    fiat_buys_week=fiat_buys_week_from(dst),
                    last_sold=dict(dst.get("last_sold") or {}),
                    closed_trades=list(dst.get("closed_trades") or []),
                    starting_total_usd=None, now=time.time())
                try:
                    ok, blocking, _all = GuardStack().may_buy(book, order.get("sym"))
                    blocks = [] if ok else [b.guard for b in blocking]
                except Exception as e:                           # noqa: BLE001
                    blocks = ["guards unavailable (%s)" % type(e).__name__]
        blocks += [str(b) for b in (caller_blocks or [])]

    # -- everything preconditions knows, on the DISK config and DISK state --------
    for r in preconditions(order, cfg=None, st=None, sealed_ok=not seal_why,
                           guard_blocks=blocks, caller="trader",
                           config_path=CONFIG_PATH, state_path=TRADER_STATE):
        if r not in reasons:
            reasons.append(r)
    for r in witness.terms_reasons(disk) + seal_why:
        if r not in reasons:
            reasons.append(r)

    # -- the approval and the key: asked only for an order still standing ----------
    if not reasons:
        try:
            record = witness.approval_record(cfg=disk)
        except Exception as e:                                   # noqa: BLE001
            record = {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:80])}
        reasons += approval_reasons(order, record, orders_today_from(dst))
    if not reasons:
        reasons += key_scope.key_reasons(venue)
    if reasons:
        return reasons, None
    return [], _issue_permit(name, order["sym"], order["side"], order["usd"])


# ---- WHICH REASONS ARE A DECISION, AND WHICH ARE THE ABSENCE OF ONE --------
#
# Added 2026-09-18 for the Sentinel-Witness specification's A1-A3. Some reasons
# in this file are a RULE DECIDING against an order -- Rule 5's signal count, a
# per-trade cap, a halt file. Others are this module saying it COULD NOT TELL:
# no portfolio was supplied, no holdings to clamp against, a state file that
# will not parse. Both refuse, and refusing is right in both cases -- but they
# are not the same fact, and until now the envelope reported them identically.
#
# THE CLASSIFICATION IS BY CONSTRUCTION, NOT BY GREPPING PROSE. Each of these
# strings is now built from the constant below and matched against the same
# constant, so a reason cannot drift out of its own category by being reworded.
# A classifier that pattern-matched the sentences would be the antipattern this
# repository keeps finding: text inspection standing in for the thing itself.
#
# `preconditions()` KEEPS ITS EXACT CONTRACT -- a flat list of strings, same
# strings, same order. covenant_trader.preconditions() delegates to it, and
# test_g4_money_gates drives all eleven refusal reasons through it; this change
# adds a second view of the same answer and alters neither.
NO_PORTFOLIO = ("sentinel: no portfolio was supplied, so the buy-side guards "
                "(cash floor, concentration, budgets) could not be evaluated")
NO_HOLDINGS = ("sentinel: a sell cannot be admitted here -- the reserve floor "
               "is enforced on the QUANTITY by covenant_trader.plan(), and "
               "this path has no holdings or baseline to clamp against")
STATE_UNREADABLE = ("trader state exists but will not parse -- the day's order "
                    "count and Rule 5 cannot be read, so nothing may go live")

#: Reasons that mean NO DECISION WAS REACHED. Everything else is a decision.
ABSTENTION_REASONS = frozenset((NO_PORTFOLIO, NO_HOLDINGS, STATE_UNREADABLE))


def is_abstention(reason):
    """True when this reason is the absence of a decision, not a decision.

    Exact membership, deliberately. A prefix or substring test would let a
    reworded refusal drift into the abstention set and quietly stop counting as
    a refusal -- and an abstention that is mistaken for a refusal is merely
    imprecise, while a refusal mistaken for an abstention is a rule going
    unenforced. The asymmetry is why this is `in`, not `startswith`.
    """
    return str(reason) in ABSTENTION_REASONS


def split_reasons(reasons):
    """(refusals, abstentions) -- the same strings, partitioned.

    A single order can produce BOTH: measured 2026-09-18, a $25 buy on the
    sentinel path returns "Rule 5: 2 sealed signals on record, need 30" (a rule
    deciding) AND NO_PORTFOLIO (this module unable to tell). So these are not
    three exclusive states; they are two lists, and the caller decides the
    precedence. seal_service does: a refusal outranks an abstention, because a
    rule that said no HAS decided, while both remain fail-closed.
    """
    rs, ab = [], []
    for r in reasons or ():
        (ab if is_abstention(r) else rs).append(r)
    return rs, ab


def _caller_reasons(caller, order, guard_blocks):
    """APPEND ONLY. A caller may add a reason to refuse; it can never remove
    one. That is what makes the sentinel provably no looser than the trader."""
    if caller != "sentinel":
        return []
    out = []
    side = order.get("side")
    # The sentinel answers about a proposal from an app with no portfolio view,
    # so the two portfolio-dependent rules cannot be evaluated here. They FAIL
    # CLOSED rather than being skipped -- skipping them is precisely what this
    # consolidation exists to stop.
    if side == "buy" and guard_blocks is None:
        out.append(NO_PORTFOLIO)
    if side == "sell":
        out.append(NO_HOLDINGS)
    return out
