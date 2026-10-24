import { createRoot } from "react-dom/client";
import Dashboard from "./Dashboard.jsx";
import { createDemoGate } from "./demoGate.js";

createRoot(document.getElementById("root")).render(
  <main style={{ maxWidth: 620, margin: "0 auto" }}>
    <p role="status" style={{ color: "#edc875", padding: 16, fontFamily: "system-ui" }}>
      Component preview — synthetic balances and price. Tier changes are local
      demonstrations only. No exchange, wallet, or trading connection is present.
    </p>
    <Dashboard gate={createDemoGate()} ledgerSupport={{ supported: false, reason: "Preview has no device adapter" }} />
  </main>
);
