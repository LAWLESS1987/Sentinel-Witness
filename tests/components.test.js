import test from "node:test";
import assert from "node:assert/strict";
import { createDemoGate } from "../demoGate.js";
import { TIERS, gateTrade, executeIfAllowed } from "../tradeGate.js";
import { getClickableTierKeys } from "../tierNavigation.js";
import { isValidLimit, computeMaxDailyExposure } from "../automatedLimits.js";

test("preview tier transitions are reversible and enforce limits", async () => {
  const gate = createDemoGate();
  assert.equal(gate.tier, TIERS.DASHBOARD);
  assert.throws(() => gate.enableAutomated({ maxPerTradeUsd: 10, maxDailyTradesCount: 2 }));
  gate.enableManual();
  assert.throws(() => gate.enableAutomated({ maxPerTradeUsd: Infinity, maxDailyTradesCount: 2 }));
  assert.equal(gate.tier, TIERS.MANUAL);
  gate.enableAutomated({ maxPerTradeUsd: 10, maxDailyTradesCount: 2 });
  assert.equal(gate.tier, TIERS.AUTOMATED);
  assert.ok(getClickableTierKeys(gate.tier).includes(TIERS.DASHBOARD));
  gate.disableTrading();
  assert.equal(gate.tier, TIERS.DASHBOARD);
  assert.equal((await gate.getDashboardSnapshot()).balances[0].available, 1000);
  assert.equal(gate.execute, undefined);
});

test("limit input and exposure calculations", () => {
  for (const raw of ["", " ", "Infinity", "NaN", "0", "-1"]) assert.equal(isValidLimit(raw), false);
  assert.equal(isValidLimit("10"), true);
  assert.equal(computeMaxDailyExposure("10", "2"), 20);
  assert.equal(computeMaxDailyExposure("bad", "2"), null);
});

test("imported seal gate refuses network errors, refusal and malformed answers", async () => {
  const order = { venue: "test", symbol: "TEST", side: "buy", amountUsd: 1 };
  let executions = 0;
  for (const fetchImpl of [async () => { throw Error("offline"); },
    async () => ({ ok: true, json: async () => ({ ok: false, admission: "admitted" }) }),
    async () => ({ ok: true, json: async () => ({ ok: true }) })]) {
    assert.equal((await executeIfAllowed(order, () => executions++, { fetchImpl })).executed, false);
  }
  assert.equal(executions, 0);
  const fetchImpl = async () => ({ ok: true, json: async () => ({ ok: true, admission: "admitted" }) });
  assert.equal((await gateTrade(order, { fetchImpl })).allowed, true);
  assert.equal((await executeIfAllowed(order, () => executions++, { fetchImpl })).executed, true);
  assert.equal(executions, 1);
});
