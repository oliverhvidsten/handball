// Dev-only harness: renders CapMeter in each cap zone with no auth, for screenshots.
// Not part of the build.
import ReactDOM from "react-dom/client";
import { CapMeter, Button } from "./ds";
import "./ds/styles.css";

const limits = { salary_cap: 150, first_luxury_threshold: 175, second_luxury_threshold: 200,
                 hard_cap: 250, max_contract_years: 5, max_contract_value: 45, min_contract_value: 0 };
const base = { roster_size: 21, max_roster: 21, roster_spots: 0, over_cap: true,
               over_first_threshold: true, over_second_threshold: true, extension_window_open: false };
const cases = [
  { ...base, payroll: 246, cap_room: 0, mid_level_exception: 0, hard_cap_room: 4, projected_next_payroll: 146 },
  { ...base, payroll: 182, cap_room: 0, mid_level_exception: 5, hard_cap_room: 68, projected_next_payroll: 120, roster_size: 19, roster_spots: 2 },
  { ...base, payroll: 132, cap_room: 18, mid_level_exception: 10, hard_cap_room: 118, projected_next_payroll: 90, over_cap: false, over_first_threshold: false, over_second_threshold: false, roster_size: 20, roster_spots: 1 },
  { ...base, payroll: 296, cap_room: 0, mid_level_exception: 0, hard_cap_room: -46, projected_next_payroll: 210 },
].map((c) => ({ ...c, limits }));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <div className="app" style={{ padding: 24, display: "grid", gap: 20, maxWidth: 760 }}>
    {cases.map((c, i) => (
      <CapMeter key={i} cap={c} actions={<><Button size="sm">Free agents</Button><Button size="sm">Trades</Button></>} />
    ))}
  </div>
);
