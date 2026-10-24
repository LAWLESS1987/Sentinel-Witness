// In-memory presentation adapter. There is no executor, wallet or network call.
import { TIERS, enableAutomated } from "./tradeGate.js";

export function createDemoGate() {
  return {
    asset: "xrp",
    tier: TIERS.DASHBOARD,
    getDashboardSnapshot: async () => ({ price: 0, balances: [{ asset: "USD", available: 1000 }] }),
    disableTrading() { this.tier = TIERS.DASHBOARD; },
    enableManual() { this.tier = TIERS.MANUAL; },
    enableAutomated(limits) {
      if (this.tier !== TIERS.MANUAL) throw new Error("Preview the manual tier first");
      const state = enableAutomated(limits);
      this.tier = state.tier;
      return state;
    },
  };
}
