# Auto-trading plan: a ladder, with a gate on every rung (2026-11-09)

The operator's words: *"also build out a better auto trading plan"*, said while
the witness was being hardened (SW3, same day).

## What this is, and what it is not

It is a plan for **how automation is allowed to grow**: which stage the trader
is in, what has to be measured before it moves up a stage, and what moves it
back down. Every gate named here is one that already exists in this folder and
can be run.

It is **not** a prediction, a strategy that makes money, or advice about what
to buy or sell. Nothing here claims an edge. The standing result in
[`MY_STRATEGY.md`](../MY_STRATEGY.md) is that no tested variant predicted
returns, and that document's own retest narrowed its one positive finding.
Nothing in this plan changes that, and a safety ladder cannot. Written by an
AI (Claude), which is not a licensed adviser. The numbers that are choices
about money are marked **his** and are left as they are.

## Why a ladder is the better plan

Today there are two states: disarmed, and armed with Rule 5 waived. The step
between them is one switch. What "better" can honestly mean, given that no
strategy has passed, is not a cleverer signal. It is that the step from
planning to placing is taken in rungs, each one earned by a measurement that
can fail, with the way back down automatic.

## The rungs

| Stage | What runs by itself | What it may do | Gate to enter (runnable) | What sends it back down |
|---|---|---|---|---|
| **0. Plan-only** (where it is now: `armed: false`) | `covenant_trader.py --plan-only` each cycle | Read, plan, seal the decision. Calls no order endpoint | Covenant reachable and its floor file present: `python witness.py` | Nothing lower exists |
| **1. Paper** | `paper_run.py` sealing and settling paper calls; the Rule 5 ledger recording every cycle | Nothing with money. Builds the record the next gate reads | Stage 0 running and sealed, and a `TRADING_POLICY.json` for `paper_run.py` to read (missing today, see below) | A paper chain that fails `paper_run.py --verify` |
| **2. Capped live** | `covenant_trader.py` armed | Orders inside the caps and scope already set (**his**: $25 an order, $50 a day, 2 a day; XRP, HBAR and LINK hold-only) | All of `guards.preconditions` (the list in the README), **and** the two evidence gates below, **and** the security conditions below | Any refusal reason; a `TRADER_HALT` file here or in covenant; a refused or failed seal; the guard stack (drawdown, daily loss, loss streak, cooldown) |
| **3. Anything wider** | not designed | Larger caps, new venues, buys from the bank | **His** decision, and not before stage 2 has its own record | n/a |

### The two evidence gates (both exist; neither is new)

- **Rule 5 ledger**: `python signal_ledger.py`. It clears only with at least
  `MIN_SIGNALS` settled signals (30 in the file today), a mean return above zero
  after its pessimistic round-trip cost, and a p-value at or under `MAX_P`
  (0.05). Its own words for a record that does not clear: not distinguishable
  from luck.
- **Paper graduation**: `python paper_run.py --gate --trials N`. Exit 0 only
  when every line of `TRADING_POLICY.json`'s graduation requirements is PASS.
  UNAVAILABLE is its own state and never counts as a pass. The trial count is
  required on purpose, because a deflated Sharpe with an unstated number of
  tries is not deflated.
  **Measured 2026-11-09: this gate cannot run here yet.** `paper_run.py` looks
  for `TRADING_POLICY.json` in this folder (or at the path in
  `COVENANT_TRADING_POLICY`) and fails closed without it. The file is not in
  this folder and not at the root of covenant's working copy either. So stage 1
  is blocked on a policy file, and what goes in it (how many scored signals,
  which p, which deflated Sharpe, whether it must beat buy-and-hold) is
  **his**. Where the file went was not traced.

### Said plainly: what is enforced and what is only written

`guards.preconditions` enforces Rule 5 **unless it is waived**, and it has been
waived since 2026-10-05 on the operator's choice (the README says so, and every
sealed record carries `waived: yes`). The paper graduation gate is **not** a
precondition of a live order anywhere in the code. So today stage 2 can be
entered from stage 0 by arming, and this ladder is a document, not a lock.

Making it a lock is one change: `preconditions` asks the paper gate and the
ledger, and a waiver has to name the stage it skips. That narrows what a
waiver can do, which is a decision about his money and his free will, so it is
**proposed here and not built**.

## Security conditions for stage 2

From covenant's consult of 2026-10-30
(`ops/CONSULT_SENTINEL_WITNESS_2026-10-30.md` in covenant), in its ranked
order, with where each stands on this branch:

1. **The witness owns its own terms.** Done here as SW3 for the seal: only the
   record crosses to covenant, covenant reads its own key, port and
   public-ledger switches, and an armed config with the seal off refuses the
   cycle. `test_sw3_witness_terms.py`, driven both ways.
   **Still open under the same heading:** the caps live in this trader's own
   gitignored config, so the party being witnessed still sets them. Moving
   their ceiling to the witness is his call, because the ceiling is his number.
2. **One executor.** Covenant's older copy is still armed beside this one. The
   retire step he stopped on 2026-10-06 is still not done. Two executors can
   each spend a day's cap.
3. **The exchange key out of the executor's reach, and seals anchored off this
   host.** Not started. Until then the seal guards against a bug or a bad
   config, not against whoever controls this user account.
4. **The seal bound to the exact order** (one seat's point): side, size, plan
   hash, a nonce and an expiry, used once. Today the check is that the note
   describes the order. Not started.

Stage 2 with only item 1 done is what exists now. Each of 2, 3 and 4 that
stays open is a risk he is carrying, and should be read as that rather than as
something the green suites cover.

## What is his to decide

- Whether the ladder becomes a lock (the proposal above).
- Whether the Rule 5 waiver stands as it is, or is rewritten to name a stage.
- The cap ceiling, and whether it moves to the witness.
- When covenant's older executor is retired.
- Anything in stage 3.

## How to see which stage it is in today

```
python witness.py                          covenant, the floors, today's approval
python covenant_trader.py --plan-only      today's plan, sealed, no order
python signal_ledger.py                    Rule 5: does the record clear
python paper_run.py --status               the paper record
```

None of the four places an order.
