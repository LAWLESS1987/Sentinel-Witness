# trader/ — the Sentinel-Witness trading program

This folder is the trading layer that used to live in covenant. It was copied here on
2026-10-05 from covenant commit `260f3dd`. Every file it copied is listed, with its size
and SHA-256, in [`COPIED_FROM_COVENANT.tsv`](COPIED_FROM_COVENANT.tsv). The operator asked
for the move and chose to **copy, prove, then retire**: covenant's own copy still exists
until this one has run live, and is retired after that.

## Who does what

**Sentinel-Witness executes. Covenant witnesses.**

| Here (`trader/`) | In covenant, reached through [`witness.py`](witness.py) |
|---|---|
| reads balances, prices and the 200-day regime | **the seal**: each cycle's decision is signed with covenant's node key and admitted or refused by covenant's node, then mined |
| plans orders under the rules in [`MY_STRATEGY.md`](MY_STRATEGY.md) | **the day's approval**: no order goes live without today's plan approved in covenant (`ops/daily_plan`, approved on the phone or with `covenant_daily_plan.py --approve` in covenant) |
| places orders when armed and every check says yes | **the floors**: `private/RESERVE.json`, read in place. The frozen hold-only floors on XRP, LINK and HBAR and the starting book live there. This folder never creates floors of its own |

`witness.py` runs each of those in covenant's own folder with covenant's own Python, so the
code that signs is the code covenant runs. That matters because this repository has its own,
older `covenant_unified_v8.py` at the root. Every failure to reach covenant is a refusal. With
no `covenant_home` configured, or no floor file, a cycle reads, plans, seals and sends
nothing.

**The witness owns its terms (SW3, 2026-11-09).** Until that day the seal call handed covenant
this trader's own config, and covenant's seal took the node key, the node port and the
public-ledger switches from it, so the party being witnessed chose who sealed. Now only the
record crosses. Covenant reads those terms from its own `trader_config.json`, refuses a key
outside its own folder, and the answer carries `"terms": "covenant"`. The Python that runs in
covenant no longer inherits `PYTHONPATH` and its relatives from this process, a seal's `ok`
counts only as the boolean true, and an armed config with `seal_required` off refuses the whole
cycle. What this does not do: the subprocess is still the same user on the same machine, and
the caps are still set in this trader's own config. See
[`docs/AUTO_TRADING_PLAN_2026-11-09.md`](docs/AUTO_TRADING_PLAN_2026-11-09.md) for what is
still open before arming.

## Hardening pass, 2026-11-12 (H1 to H11)

An independent review of this folder listed eleven defects. Each was checked in the code,
fixed, and given a suite that drives it both ways (`test_h01_*.py` to `test_h11_*.py`). Written
by an AI (Claude). Nothing below was run against an exchange: every venue in those suites is
a fake or has its transport stubbed, and no live cycle has run on this code. **The sections
further down this file are kept as they were written; where one of them is no longer true,
this section says so.**

| | What was wrong | What happens now |
|---|---|---|
| H1 | NaN fails every comparison, so an order of NaN dollars cleared both caps; a NaN or negative row in the day's record made the day never full | an amount is a finite number above zero, in an order and in the record; anything else refuses, and a bad row blocks rather than being skipped |
| H2 | a state file that could not be read came back as "never run" (0 orders today, $0 ever bought) and was then overwritten | the cycle refuses with the reason, the file is left exactly as it is, and a copy is kept beside it (`trader_state.json.unreadable-<hash>`). Exit code 4 |
| H3 | a holding with no price was left out of the total, the rest looked over-concentrated, and a sale was planned | a cycle that cannot price or read the whole book plans no orders, and names what is missing |
| H4 | the day rolled over whenever the stored date differed, so a clock set back reset the day's count and revived an old approval | the state keeps the latest day and moment seen; an earlier clock refuses the cycle. Exit code 4 |
| H5 | a config that left `seal_required` or `min_sealed_signals` out had neither; caps were whatever the config said | missing keys take the shipped values; **hard ceilings in `guards.py`: $25 an order, $50 a day, 2 orders a day**, which a config may only lower. A cap above one refuses, it is not clamped |
| H6 | `rule5_waiver`, `trading_scope.open_reserve_pct` and `covenant_home` / `SENTINEL_COVENANT_HOME` could remove Rule 5, remove the reserve, and choose the witness | disarmed they still work and the cycle prints **REHEARSAL ONLY**. Armed, each refuses the cycle; the witness folder must be **pinned** (below) |
| H7 | arming was one key press and never ended | `armed: true` counts only with `armed_until`, an ISO time with its zone, at most 24 hours ahead. `SENTINEL_ARM_AND_RUN.bat` shows what arming means, requires the typed phrase `ARM REAL ORDERS`, and arms for 12 hours |
| H8 | the approval was a yes or no for the day; the orders placed were whatever was planned at run time | each order must be one the approved plan proposed (same symbol and side, no larger, at most 2% smaller); a decline anywhere in the day is final; an approval lasts 12 hours |
| H9 | nothing checked what the exchange key may do, or who else can read it | before a live order the venue is asked; a key that can withdraw or transfer places nothing. A credential file another account can read is not used (Windows: the file's ACL) |
| H10 | a Kraken rejection was booked as "maybe placed" and used one of the day's orders; a resting order was never looked at again; Kraken and Robinhood sent market orders | definite refusals free the day's row; open orders are read and nothing is planned for a symbol that has one; a sale counts as proceeds only once the venue confirms a fill; orders are limits within 1% of the plan's price |
| H11 | a halt file that could not be looked at read as "no halt" | it is treated as a halt; "not there" is only ever the operating system saying so |

### What an operator will notice

- **An armed cycle now refuses if the config carries the Rule 5 waiver, a `trading_scope`
  reserve under 50%, `daily_plan_required: false`, `min_sealed_signals` under 30, or
  `rule5_require_significance: false`.** This reverses, for armed runs only, the operator's
  recorded choices of 2026-10-05 (the waiver) and 2026-10-06 (the scope). Neither record is
  deleted and both still work disarmed. With the waiver gone Rule 5 applies, and on the record
  this file quotes (7 of 30 settled) it refuses every live order. Undoing this is an edit to
  `guards.live_terms_reasons`, in the open.
- **An armed cycle needs the witness folder pinned.** Write covenant's absolute path as the one
  line of `%USERPROFILE%\.sentinel_witness\covenant_home.pin` (a folder this trader never
  writes to), and keep `covenant_home` in `trader_config.json` naming the same folder.
- **An armed cycle needs covenant to say what was approved.** `witness.approval_record` calls
  `covenant_daily_plan.approval_record(now)` in covenant; the contract is in that function's
  docstring. Whether covenant has it is UNDETERMINED from this repository. Until it does,
  every live order is refused with "approval binding unavailable".
- **Live Robinhood orders need a hand-written record** of what the key may do, in
  `%USERPROFILE%\.sentinel_witness\key_scope.json` (shape in `key_scope.recorded`). Coinbase
  and Kraken are asked directly.
- A config with a cap above $25 / $50 / 2 refuses every cycle, armed or not.
- `covenant_trader.py` can exit 4 (the cycle was refused before it began: unreadable state, or
  a clock behind the record).

### What this pass did not do

- It did not run against a venue. The endpoints and field names are from the venues' public
  documentation as read on 2026-11-12, not from an observed answer.
- The pin file, the key-scope record and the state file all belong to the same OS user. This
  closes one-line roads (an environment variable, a config edit); it does not defend against
  whoever controls the account.
- The armed terms are checked for the cycle (`witness_ready`) and for a config
  `guards.preconditions` reads from disk. A Python caller that hands `preconditions()` its own
  dict is held to the waiver refusal, the caps, the arming and the clock, and not to the rest.
- Robinhood fills are not read, so no sale there is ever confirmed. A row whose venue answer
  was lost has no order id and stays UNKNOWN for a person to resolve.
- `plan()` still counts a sale planned in the same cycle as cash on hand for R6.
- covenant's older executor is still armed beside this one (the plan of 2026-11-09, item 2).

## Venues

`venues.py` holds three order adapters: **Coinbase**, **Kraken** and **Robinhood**. A
disarmed order goes to the venue's own dry-run endpoint where the venue offers one: Coinbase
`/orders/preview`, Kraken `validate=true`. Robinhood publishes no preview endpoint, so its dry
run is local only. Credentials are read from `~/.coinbase`, `~/.kraken` and `~/.robinhood`,
outside every repository, and never from this folder.

## What has to say yes before a live order

All of these, every order, every time (`guards.preconditions`):

1. `armed: true` in `trader_config.json`.
2. No `TRADER_HALT` file, here **or in covenant**.
3. Inside **$25 per order, $50 per day, 2 orders per day**.
4. The guard stack, for buys, including the **10% cash floor**. A guard never stops a sale.
5. **Today's plan approved in covenant.**
6. The decision **sealed** by covenant's node (`seal_required`).
7. **Rule 5**, unless it is waived (next section).

`plan()` also clamps every sale to the reserve, so a hold-only position at its frozen floor
cannot be sold.

## Rule 5 — waived 2026-10-05, on the operator's choice

Rule 5 refuses live orders until the regime signals have a scored record: 30 settled, a
positive mean after costs, and p ≤ 0.05. On 2026-10-05 the operator chose **"Trade now within
caps"**: turn Rule 5 off, keep the caps and the hold-only floors. The record when he chose:
**7/30 settled, 0/7 wins, mean −6.13% after costs, p = 1.000.**

The waiver is a record, not a deleted check. It is `rule5_waiver` in `trader_config.json`,
and it only counts when it says `waived: true` with who, when and in what words
(`guards.rule5_waiver`). Rule 5 is still computed and printed every cycle. Each sealed
decision carries `"waived": "yes"`, so covenant's chain shows every day an order could go
live without it. To restore Rule 5, delete the `rule5_waiver` entry or set `waived` to
`false`.

## What stood between this and an order on 2026-10-05

The proof run (`--plan-only`, from this folder) read the Coinbase balances, applied
covenant's floors, printed Rule 5 as waived, and had its decision admitted and mined by
covenant's node. It planned **no orders**, for these reasons:

- **Cash is below the 10% floor.** Every buy is blocked, and the R6 weekly contribution stays
  as cash.
- **XRP is at its frozen floor.** The concentration trim it would otherwise get is dropped.
- ~~**`contribution_symbols` is empty**, so the weekly contribution has nothing to buy.~~
  **Corrected 2026-10-06, and this line was wrong:** an empty `contribution_symbols` means
  *any* held coin under the 20% cap and above its 200-day line qualifies (`plan()`:
  `not wanted or p["sym"] in wanted`). The weekly contribution was blocked by cash alone.
- **No daily plan has been approved in covenant since 2026-09-19.**
- `armed` is `false` in this folder's config until the operator arms it.

**A reader cannot check the account facts in this section or the Rule 5 record above.**
They rest on the exchange account, covenant's `private/RESERVE.json`, its gitignored
approvals ledger and the gitignored `trader_log.txt`, and none of those is ever published.
What is public is the code that reads them, the tests that drive it, and the sealed
decision's commitment on the operator's node.

The strategy has no buy signal by design: no timing rule beat chance out of sample
(`docs/STRATEGY_VALIDATION_2026-09-03.md`). On 2026-10-05, orders came from the concentration
cap, the cash floor and the weekly contribution.

## Operation — every coin but the three is open, 2026-10-06

The operator, 2026-10-06: "We already have a rule about those three. Everything else is good
to sell in trade." Then: "build on and edit the rules for all bur the 3 we mentioned to begin
operation i'm willing to take the risk."

It is recorded as `trading_scope` in `trader_config.json`: `open_reserve_pct` plus who, when
and in what words (`guards.trading_scope`). Anything incomplete is no scope, and the rules
before it apply. With the record:

- **XRP, HBAR and LINK are untouched.** They keep a 100% reserve of a frozen floor. Anything
  held above the floor stays tradeable, and adding to them stays allowed. Nothing below
  applies to them.
- **Every other coin is open to sell.** The 50% reserve of 2026-09-04 becomes
  `open_reserve_pct`, set to 0.
- **R3 is automated**, as MY_STRATEGY.md wrote it: "Flip DOWN through the 200d -> reduce that
  position toward your floor." An open coin below its 200-day line is sold toward its floor,
  one capped order a day, until it gets there.
- **R1's cash sleeve is automated.** If cash is still under 10% after trims and R3, the
  largest open coin above its line is sold, by the shortfall only. This was the step the
  planner used to decline: "which position to reduce is a judgement the rules do not make."
  The record is the operator making that judgement.
- **A concentration trim on an open coin is cut to one capped order.** Before this, a trim
  over $25 was proposed at full size and then refused by the cap every day, so it never
  happened.
- **A trim the cap will refuse is marked, and counts for nothing (SW4, 2026-11-09).** The three
  are not cut to a capped order, so a trim of what one of them holds above its floor can be
  proposed at more than $25. The cap refuses it every day. It is still shown and sealed, now
  marked `unplaceable`. Before this it also used one of the day's order slots, was counted as
  cash raised (so the plan called the cash floor "covered" by a sale that would not happen),
  and counted as cash on hand for the R6 contribution. It does none of those now. The three
  are still never sold by this. `test_sw4_unplaceable.py`.

Nothing in this section buys on a signal. R6, the weekly contribution, still buys only once
cash is over the floor. Every order still passes the caps, the guards, the day's approval and
the seal. `test_sw2_operation.py` drives all of it both ways.

**The plan for 2026-10-06, from this folder (`--plan-only`):** two sells of $25 each, one
under R3 and one under R1. XRP, HBAR and LINK were left as they were. Which coins, and how far
cash is from the floor, are portfolio facts. They are not published here, and a reader cannot
check them.

**The cost, stated with the change.** Selling to build cash, and selling coins below their
line, costs fees on every order. `signal_ledger.py` charges 1.3% per round trip: the 60 bps
maker fee each way plus 10 bps of spread.
It can also sell a coin before it recovers. The regime record that R3 acts on was 0 for 7,
mean −6.13% after costs, when this was switched on.

## Commands

Run these from this folder:

```
python covenant_trader.py --status       caps, Rule 5 and its waiver, covenant, floors, venues
python covenant_trader.py --plan-only    today's plan; seals it, calls no order endpoint
python covenant_trader.py --once         one cycle; live only if armed and every check says yes
python money_posture.py                  ARMED or DISARMED, said plainly
python signal_ledger.py                  the Rule 5 record
python witness.py                        is covenant reachable, are the floors there, is today approved
```

`SENTINEL_TRADE.bat` runs the read-only three: posture, status, plan. `SENTINEL_ARM_AND_RUN.bat`
asks first, then arms and runs one live cycle. `TRADER.bat`, `TRADER_TASK.bat` and `TRADE.bat`
are covenant's launchers, copied unchanged.

## Setup

1. Copy `trader_config.example.json` to `trader_config.json`. Set `covenant_home` to your
   covenant checkout, or set the environment variable `SENTINEL_COVENANT_HOME`. There is no
   built-in default, so a machine without covenant cannot trade by accident.
2. Python with `cryptography` (in the repository's `requirements.txt`). The `.bat` files use the
   repository's `.venv` if there is one, and otherwise covenant's.
3. A trade-scoped exchange key in `~/.coinbase` (or `~/.kraken`), with no withdrawal
   permission.

`trader_config.json`, `trader_log.txt`, `TRADER_HALT`, `private/` and every credential and
balance file are gitignored. This repository is public.

## Tests

```
python test_sw1_witness.py               the waiver, covenant's halt, the bridge, no-covenant refusal
python test_sw2_operation.py             the trading scope, R3 and the R1 cash sleeve; the three untouched
python test_sw3_witness_terms.py         covenant seals on its own terms, whatever this trader's config says
python test_sw4_unplaceable.py           an order the cap will refuse takes no slot and is not cash
python test_g4_money_gates.py            every refusal reason, driven
python test_f5_reserve.py                the 50% reserve and the frozen hold-only floors
python test_f7_caps.py                   the per-order and per-day caps
python test_v2_venue_guarantee.py        each venue's dry run is what the documents say
python test_rule5_ledger.py              the Rule 5 ledger
python test_h01_amounts.py               H1: NaN, infinite, zero and negative amounts, in orders and in the record
python test_h02_state.py                 H2: an unreadable state stops the cycle and is never overwritten
python test_h03_unpriced.py              H3: no orders on a book that cannot be priced or read whole
python test_h04_clock.py                 H4: the day rolls forward only
python test_h05_config.py                H5: shipped defaults, and the $25 / $50 / 2 ceilings
python test_h06_waivers_witness.py       H6: armed, no waiver, no reserve under 50%, a pinned witness folder
python test_h07_arming.py                H7: armed_until, and the arming file's typed phrase
python test_h08_approval.py              H8: the approval binds the orders, expires, a decline is final
python test_h09_venue_keys.py            H9: what the key may do; who else can read the credential
python test_h10_order_mechanics.py       H10: definite refusals, open orders, confirmed fills, the price band
python test_h11_fail_closed.py           H11: a halt that cannot be looked at is a halt
```

The rest are `test_a92_topic_is_a_credential.py`, `test_d3_daily_guards.py`,
`test_g2_promised_commands.py`, `test_maker_orders.py`, `test_paper_run.py`,
`test_r6_contribution.py` and `test_backtest_guardrails.py`. CI runs all of them
(`.github/workflows/verified-bridge.yml`, job `trader`). On a machine without covenant, the
checks that need covenant's ledger or seal service say **NOT RUN** rather than passing.

## Also here

- **Research**: `strategy_validate.py`, `strategy_lab.py`, `strategy_pairs.py`,
  `strategy_cross_sectional.py`, `paper_bot.py`, `paper_run.py`, `multi_scan.py`,
  `rebalance_strategy.py`, `signal_watch.py`, `quant/`, and the price series in `realdata/`.
- **Records**: `docs/` holds the strategy validations, `TRADING_READINESS.md`, and the
  Sentinel-Witness specification and baseline written in covenant.
- **Verifier**: `sentinel_witness/order_claims.py` and `verify_record.py` check a sealed record
  against its order. They import covenant's node code, so they run from covenant. These are
  reference copies.

## Not moved

The seal service (`sentinel_witness/seal_service.py`, on `127.0.0.1:8433`) stays in covenant. It
is the witness, and the root `tradeGate.js` calls it. Covenant's node, judges and daily-plan
approvals stay there too.
