// Tests of automatedLimits.js, the one module of the app in this branch that can be loaded here.
// Run: node --test tests/        (Node 22.7 or newer; nothing is installed)
//
// README.md's table describes tests under src/lib/__tests__/ that are not in this branch. These are written against
// what the module's own comments say it does, driven both ways. They pin behaviour; they do not judge it.
import assert from "node:assert/strict";
import { test } from "node:test";

import { computeExposurePctOfBalance, computeMaxDailyExposure, isValidLimit } from "../automatedLimits.js";

test("isValidLimit: a positive finite number written as text is valid", () => {
  for (const raw of ["1", "25", "0.01", "1e3", " 5 "]) assert.equal(isValidLimit(raw), true, raw);
});

test("isValidLimit: zero, a negative, empty, blank, words and infinity are not", () => {
  for (const raw of ["0", "-1", "-0.5", "", "   ", "abc", "12abc", "Infinity", "-Infinity", "NaN"]) {
    assert.equal(isValidLimit(raw), false, JSON.stringify(raw));
  }
});

test("isValidLimit: only text is accepted, as it comes from an input field", () => {
  for (const raw of [10, 0, null, undefined, true, {}, [], ["5"]]) assert.equal(isValidLimit(raw), false, String(raw));
});

test("isValidLimit: nothing stricter than finite and above zero -- what Number() reads, it accepts", () => {
  // The module says it mirrors the gate's own check on purpose ("finite, > 0, nothing stricter"). Pinned, not judged:
  // if the gate ever becomes stricter, this is the test that has to change with it.
  assert.equal(isValidLimit("0x10"), true);
  assert.equal(isValidLimit("1_000"), false);
});

test("computeMaxDailyExposure: the worst case, every trade at the cap", () => {
  assert.equal(computeMaxDailyExposure(25, 2), 50);
  assert.equal(computeMaxDailyExposure("25", "2"), 50);
  assert.equal(computeMaxDailyExposure("0.5", "3"), 1.5);
});

test("computeMaxDailyExposure: null, never 0 or NaN, until both inputs are valid", () => {
  for (const [a, b] of [[0, 2], [25, 0], ["", "2"], ["25", ""], [-25, 2], [NaN, 2], [25, Infinity], [null, 2], [undefined, undefined]]) {
    assert.equal(computeMaxDailyExposure(a, b), null, `${a}, ${b}`);
  }
});

test("computeExposurePctOfBalance: the exposure as a percentage of the balance", () => {
  assert.equal(computeExposurePctOfBalance(50, 1000), 5);
  assert.equal(computeExposurePctOfBalance(50, 50), 100);
  assert.equal(computeExposurePctOfBalance(150, 100), 150);
});

test("computeExposurePctOfBalance: null when there is no exposure yet or no balance to relate it to", () => {
  for (const [exposure, balance] of [[null, 1000], [50, 0], [50, -1], [50, NaN], [50, Infinity], [50, undefined], [50, "1000"]]) {
    assert.equal(computeExposurePctOfBalance(exposure, balance), null, `${exposure}, ${balance}`);
  }
});

test("tierNavigation.js cannot be loaded from this branch: it imports ./tradeGate.js, which is not here", async () => {
  // Pinned so the README's statement stays true. When tradeGate.js is added to this branch this test fails, and that
  // is the moment to replace it with real tests of tierNavigation.js and take the file off checks/NOT_IN_THIS_BRANCH.txt.
  await assert.rejects(import("../tierNavigation.js"), { code: "ERR_MODULE_NOT_FOUND" });
});
