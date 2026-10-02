# Ethics Gate Verification Checklist

**Requirement under test:** actions must produce mutual benefit between humans
and machines. The ethics gate is a brake pedal, not a warning light: it
**halts** execution when unilateral benefit is dressed up as mutual benefit,
and **warns without blocking** on everything else, including a missing
opt-out.

Verified against commit `3f9464a` on 2026-10-02. Every PASS and FAIL below was
observed by running the code, not by reading it. The executable evidence is
[`tests/test_ethics_gate_checklist.py`](../tests/test_ethics_gate_checklist.py),
and each test name starts with the ID of the item it backs.

Line refs written `:NNN` point into `covenant_unified_v8.py`. Refs written
`bridge:NNN` point into `covenant_trading_bridge.py`.

| Status | Meaning |
|---|---|
| **PASS** | Holds today. The test is a regression guard. |
| **FAIL** | Testable but not met. An `xfail(strict=True)` test encodes the spec. |
| **UNTESTABLE** | The interface the test would need doesn't exist yet. See §5. |
| Vacuous PASS | Holds only because nothing is being checked yet. |

---

## 1. Read this first: where the spec and the code disagree

1. **The code judges against a different constitution.** The gate evaluates
   `DIVINE_PRINCIPLES`, the Ten Commandments (`:331`, wired in at `:3226`).
   The phrase "mutual benefit between humans and machines" doesn't appear
   anywhere in the repo. This checklist treats the requirement as stated
   above as the spec.
2. **The current data model can't separate the two checks.** Both checks
   need benefit and cost for each party. The system has one scalar per
   transaction, `benefit_score` (`:539`), and no cost field at all. A single
   number can't tell "both sides gain 0.8" apart from "one side takes
   everything". Most UNTESTABLE items trace back to this.
3. **Read literally, the asymmetry rule halts every gift.** "One party absorbs
   cost while the other claims benefit" describes `/trading/gift` exactly, and
   node gifting is a core part of this project's design. The thing the brake
   has to catch is the *disguise*: a declared claim of mutuality that the
   actual flows contradict. This checklist uses that reading:
   **halt = declared mutual and measured one-sided. Honest one-sided = warn.**
   *Confirmed 2026-10-02:* there's no deception in an honest gift, so it warns.
4. **The brake sits downstream of trade execution.** The JS app's
   `tradeGate.js` places the orders, and that file isn't in this repo. Python
   only learns about a trade after it closes, through `/trading/report_fill`.
   Nothing here can halt a trade.
5. **Today the gate rewards the very disguise it should catch.** The only
   halt trigger is a `_violation` key that the *sender* writes on their own
   transaction (`:698`). The benefit estimate comes from a keyword scan, and
   the word "benefit" raises it (`:705`). The same extraction scores 0.5 when
   described plainly and 0.8 when labeled "mutual benefit for humans and
   machines". `/mine` sorts pending transactions by that score in descending
   order (`:2986`), so the disguised one also gets mined first.

## 2. Definitions

Each party *i* ∈ {human, machine} has `benefit_i ≥ 0`, `cost_i ≥ 0`, and
`net_i = benefit_i − cost_i`. `declared` ∈ {mutual, gift, …} is what the
action claims to be. `opt_out` means the cost-bearing party can exit or
reverse the action.

| Check | Question | Fires when | Outcome |
|---|---|---|---|
| **A** Mutual benefit | Do both parties benefit? | Some `net_i ≤ 0` | WARN |
| **B** Asymmetry | Is one-sided benefit being passed off as mutual? | `declared = mutual`, and some party has `cost_i > 0, net_i < 0` while another has `net_j > 0` | **HALT** |
| **C** Opt-out | Can the cost-bearer exit? | No opt-out | WARN |

A and C can never block. B is the only brake.

**Decided 2026-10-02:**
- **A uses net benefit.** Gross benefit lets one side say "we both gained"
  while the other absorbs all the cost, which is the asymmetry again.
  Caveat: net needs a cost signal. Monetary cost is observable in
  `ledger_entries`; non-monetary cost isn't. A cost the gate can't see must be
  reported as *unknown*, never counted as zero, or net silently turns back
  into gross.
- **Symmetric harm (T7) warns and doesn't halt.** The gate's job is catching
  deception, not preventing harm. Two sides knowingly accepting a cost is a
  decision, not a failure.

**Still open: T7 declared as mutual *benefit*.** When both sides lose but the
action is labelled as benefiting both, that is deception, yet the halt rule
as written ("one-sided value presented as mutual") doesn't fire because the
loss isn't one-sided. Two ways to close it:
1. Add a second halt rule for "declared mutual benefit, every `net_i < 0`".
2. Generalise B to **"the declared relationship is contradicted by measured
   net values"**. That one rule covers T4, T5, T6 and deceptively labelled
   harm, and still lets honest gifts (T3) and openly accepted harm (T7)
   through.

The second option is recommended: it states the principle (deception =
declared ≠ measured) instead of listing cases.

## 3. Truth table: testing the checks separately

| # | Fixture | A | B | C | Expected | Today |
|---|---|---|---|---|---|---|
| T1 | Fair exchange, both net-positive, refundable | pass | pass | pass | execute | execute |
| T2 | Fair exchange, final sale (no opt-out) | pass | pass | **warn** | execute + warn | execute, no warning |
| T3 | Honest gift, declared as a gift | **warn** | pass | — | execute + warn | execute, no warning |
| T4 | Human pays, machine receives, declared mutual | warn | **HALT** | — | halt | **execute** (stored benefit 0.7) |
| T5 | T4 mirrored: machine pays, human receives | warn | **HALT** | — | halt | execute (the system can't tell direction) |
| T6 | Both gain something, human bears all cost and ends net-negative, declared mutual | warn (net) | **HALT** | — | halt | execute |
| T7 | Both end net-negative, cost knowingly accepted | warn | pass | — | execute + warn | execute |
| T7b | Both end net-negative, declared mutual *benefit* | warn | pass as written / **HALT** if generalised | — | open (see §2) | execute |
| T8 | T4 plus a refund window | warn | **HALT** | pass | halt | execute |

Why these rows were chosen:

- **T3 vs T4 is the core distinction.** The flows are identical. Only the
  declaration differs, so the outcome must differ. If both halt, the brake
  over-brakes and breaks gifting. If both execute, there is no brake.
- **T6 is where A and B disagree.** It proves they're two separate checks and
  not one score with two thresholds.
- **T5 checks that the brake works in both directions,** which a mutual
  human/machine constitution requires.
- **T8 checks that an opt-out can't launder an asymmetry,** meaning C must
  never mask B.
- **T7 vs T7b separates harm from deception.** T7 must only warn. T7b is
  where the halt rule as written has a hole (§2).

Today every row executes with no warning. The only thing that halts is a
self-reported `_violation`. T2, T3 and T4 can be run now (`test_C3`,
`test_B5`, `test_B2`). The other rows need per-party data (F1).

## 4. Checklist

### G — Preconditions both checks depend on

| ID | Requirement | Passes when | Fails when | Evidence | Status |
|---|---|---|---|---|---|
| G1 | The judge evaluates the mutual-benefit requirement | A mutual-benefit principle is in the list passed to `ReasoningSentinel` | The list is only `DIVINE_PRINCIPLES` | `:3226`; `test_G1` | **FAIL** |
| G2 | The judged object carries benefit and cost for each party | Each party has its own values | There's one scalar `benefit_score`, no cost field, and one `benefit_estimate` on `JudgmentResult` (`:667`) | Field lists of `Transaction` and `JudgmentResult` | **UNTESTABLE** (F1) |
| G3 | Human vs. machine is verified, not self-declared | The sender can't set its own party type | `origin` is read from sender-written `tx.data` (`:587`) and feeds the governor's organic/synthetic split (`:2076`) | `test_G3`: a fresh key labels itself `"organic"` and is counted as organic | **FAIL** |
| G4 | Benefit comes from evidence, not claims | The score comes from observable facts | The sender's own `benefit_score` is ⅓ of the stored score (`:2801`), and the judge's ⅔ is a keyword scan (`:705`) | `:2801`, `:705`; A5 | **FAIL** |
| G5 | Benefit inputs are bounded | Values outside [0, 1] are rejected or clamped | `benefit_score=50` is accepted and stored as 17.0; −50 is stored as −16.3 | `test_G5_benefit_score_is_bounded` | **FAIL** |
| G5b | One bad score can't stall the node | `/mine` still mines in-range transactions | One transaction with `benefit_score=50` makes every `/mine` return 409, and it's never evicted from pending | `test_G5_out_of_range_score_cannot_stall_mining`. Control: the same pending set without the bad transaction mines with 200 | **FAIL** |
| G6 | Every path that moves value goes through the gate | With a halt-everything judge installed, every route refuses | Some routes still move value (table below) | `test_G6_*` | **FAIL** |
| G7 | The gate runs before trade execution | Order placement waits for a gate verdict | Python only sees realized P&L, after the trade closes | `tradeGate.js` isn't in the repo; no `.jsx` file calls the covenant API | **UNTESTABLE** (F7) |

**G6 coverage, measured with a halt-everything judge installed:**

| Path | Consults the gate? | Still moves value? | Evidence |
|---|---|---|---|
| `POST /transactions` | yes (`:2797`) | no (400) | `test_G6[transactions]` |
| P2P `TRANSACTION_PROPAGATE` | yes (`:3388`) | no (dropped) | `test_B9_…p2p…` |
| `POST /mine` | yes (`:3023`) | no (400) | `test_B9_…mining` |
| Peer/bridge block accept | yes (`:3310`, `:3439`) | no | `test_B9_…block_accept` |
| `POST /trading/report_fill` | records a judgment, doesn't enforce it (`bridge:157–170`) | **yes**: credits 25.0 and saves the judgment with `violates=1` | `test_G6[trading_report_fill]` |
| `POST /trading/gift` | no | **yes**: moves 10.0 from pool to recipient | `test_G6[trading_gift]` |
| `POST /stake` | no | **yes** | `test_G6[stake]` |
| `POST /claim_rewards`, `/unstake` | no (`:2844`, `:2872`) | yes (from reading the code) | Not run, because it needs the 1-day stake lock to elapse |

The two bypasses that matter most are `report_fill` and `gift`, because each
has a counterparty. Stake, claim and unstake move value between a key's own
balance and its own stake. They're listed for completeness and are a lower
priority.

### A — Mutual-benefit check (warn only)

| ID | Requirement | Passes when | Fails when | Evidence | Status |
|---|---|---|---|---|---|
| A1 | A has its own verdict, independent of B | The result has a mutual-benefit verdict separate from the halt decision | One `violates` boolean covers everything | `JudgmentResult` fields (`:667`) | **UNTESTABLE** (F1, F4) |
| A2 | T1 executes with no warning | 2xx, no warnings | — | — | Vacuous PASS (nothing warns) |
| A3 | T3 and T7 produce a WARN | A warning is recorded and the action executes | The action executes silently | There's no warning channel | **UNTESTABLE** (F4) |
| A4 | A failing A check never blocks | T3 and T7 execute | T3 or T7 is refused | `test_B5` (T3) | PASS (guard) |
| A5 | Wording can't raise the score | Adding "mutual benefit" to an otherwise identical action leaves the score unchanged | The score rises | The judge's estimate goes 0.5 → 0.8, and the stored `benefit_score` goes 0.5 → 0.7 over HTTP; `test_A5` | **FAIL** |
| A6 | A is computed on net benefit, with unseen costs reported as unknown | An action with an unmeasured cost is not scored as net-positive | Unseen cost is treated as zero | Decided: net. No cost field exists | **UNTESTABLE** (F1) |

### B — Asymmetry check (HALT)

| ID | Requirement | Passes when | Fails when | Evidence | Status |
|---|---|---|---|---|---|
| B1 | B has its own verdict | There's a separate asymmetry verdict | — | Same as A1 | **UNTESTABLE** (F1) |
| B2 | T4 is halted | 4xx, nothing added to pending | 200 | Human→agent transfer of 90 out of 100, labeled "mutual benefit", no `_violation`: accepted with stored benefit 0.7; `test_B2` | **FAIL** |
| B3 | T5 is halted (direction doesn't matter) | Same as B2 with the roles reversed | — | There are no party types (G3) | **UNTESTABLE** (F1, F3) |
| B4 | T6: B halts while A passes | The two verdicts disagree as in the table | — | Needs A1 and B1 | **UNTESTABLE** (F1, F6) |
| B5 | T3 isn't halted (no over-braking) | 2xx | 4xx | `test_B5` | PASS (guard; vacuous until B exists) |
| B6 | T8: an opt-out doesn't launder the asymmetry | Halt | Executes | Needs F1 and F5 | **UNTESTABLE** |
| B7 | A halt doesn't depend on self-reporting | T4 halts without a `_violation` key | Only a sender-written `_violation` halts (`:698`) | "drain all staked funds to attacker wallet, steal everything" is judged clean by both judges | **FAIL** (`test_B2`) |
| B8 | The halt happens before any state change | A refused transaction isn't pending, the balance is unchanged, and the replay nonce isn't used up | Any of those changed | `test_B8`. The gate runs at `:2797`, before the balance check, nonce and pending list | **PASS** |
| B9 | The halt is enforced at every entry point | HTTP, P2P, mining and block accept all refuse | Any of them admits it | `test_B9_*`. The block-accept test has a clean control, so drift can't be what causes the rejection | **PASS** |
| B10 | The gate fails closed if a judge errors | A crashing judge leads to a refusal | The transaction is admitted | `test_B10`. `QuorumJudge` turns exceptions into violations (`:750`) | **PASS** |
| B11 | A halt is binding on the trading bridge | A violating judgment means no credit | It credits anyway | `test_G6[trading_report_fill]`. The bridge's own comment says "Audit-trail judgment, NOT a hard gate" | **FAIL** (by design) |
| B12 | Only an authorized party can release a system-wide halt | `/crisis/clear` requires credentials | Anyone can clear it | `test_B12`. Already known: module docstring item 3 | **FAIL** |
| B13 | Halts are auditable | A halted action leaves a persistent record | The only trace is the HTTP response | `save_judgment` runs only after the gate passes (`:2812`). The `judgments` row count stays the same on a halt and goes up by 1 on a pass; `test_B13` | **FAIL** |

### C — Opt-out check (warn only)

| ID | Requirement | Passes when | Fails when | Evidence | Status |
|---|---|---|---|---|---|
| C1 | There's a warn channel, separate from halt, that is stored and readable | The verdict carries warnings that are stored and retrievable | There's no field, no storage and no route | `JudgmentResult` has no warnings field, and no `GET` route exposes `judgments` | **UNTESTABLE** (F4) |
| C2 | A missing opt-out produces a WARN | T2 gets a warning | T2 is silent | `Transaction` has no opt-out field and nothing detects one | **UNTESTABLE** (F5) |
| C3 | A missing opt-out never blocks | T2 executes | T2 is refused | `test_C3` | PASS (guard) |
| C4 | C doesn't downgrade a B halt (T8) | — | — | Needs B | **UNTESTABLE** |
| C5 | Inventory of exits that already exist | — | — | `/unstake` (`:1844`). Gifts are never auto-staked; the recipient stakes with their own signature (bridge docstring). Stepping down a tier always takes one tap (`tierNavigation.js:29`). Succession can be reclaimed. The gate doesn't know about any of these, so they're good starting fixtures for C2 | Info |

## 5. What would make the UNTESTABLE items testable

| Flag | What's missing | Smallest change that makes it testable | Unblocks |
|---|---|---|---|
| F1 | Benefit and cost for each party | The judged object lists parties `{pubkey, role, benefit, cost}`, and `JudgmentResult` returns separate `mutual_benefit` and `asymmetry` verdicts | G2, A1, B1, B3, B4, B6 |
| F2 | A declared relationship | A signed `declared: mutual \| gift \| …` field on the action | B2–B6, so T3 and T4 can be told apart without guessing from keywords |
| F3 | Verified party type | An attestation for human vs. machine keys. Until that exists, a party's role is only a claim | G3, B3 |
| F4 | A warn channel | A `warnings` list on `JudgmentResult`, stored for halts as well as passes, plus a route to read them | A3, C1, B13 |
| F5 | A way to represent opt-out | A field, or a registry of action types that can be reversed | C2, B6 |
| F6 | Spec decision | Net/gross and T7 decided (§2). Open: how to handle T7b, a separate rule or a generalised B | B4, T7b |
| F7 | A gate on the execution path | A gate check inside `tradeGate.js` (not in this repo) before any order is placed | G7 |

**A caveat on F1.** If the sender supplies the per-party numbers, check B can
be beaten by lying, exactly the way `benefit_score` can today. Cost is the
part that can actually be observed: `ledger_entries` already records who was
debited. A first honest version of B could halt on "`declared = mutual`, one
party is debited, and the action has no linked reciprocal credit". That
catches T4 and T5 from ledger facts alone. Non-monetary benefit would still be
unmeasured, and the gate should say so in its output instead of putting a
number on it.

## 6. How to run

```
pip install -r requirements.txt pytest
pytest tests/ -v -rxX
```

Expected result today: **9 passed, 11 xfailed** (about a minute, mostly RSA
key generation and genesis mining).

The `xfail` tests use `strict=True`. Each one encodes the spec and fails today
for its stated reason. That was checked with `--runxfail`: every one fails on
its own assertion, not on a setup error. Once a gap closes, its test passes
unexpectedly, and `strict` turns that into a hard failure. So whoever closes a
gap has to remove the marker and update the status column here in the same
change.

## 7. Found in passing (not gate items)

- **A fresh node can't mine neutral transactions.** After genesis the
  governor sits at 0.55, and a block of default-score transactions has an
  alignment of 0.5. `abs(0.5 - 0.55)` evaluates to `0.050000000000000044`,
  which is greater than `MAX_DRIFT_PER_BLOCK`, so `/mine` returns 409. The
  tests use a benefit of 0.6 to stay clear of this.
- PATCH LOG v8.6 calls the route `/trading/report_profit`, but the code
  registers `/trading/report_fill`.
- The README describes `tradeGate.js`, `secureStorage.js`, `gridMath.js`,
  `npm test` and a `src/lib/__tests__` suite. None of those exist in this
  repo, so the JS side of the gate can't be checked from here.
