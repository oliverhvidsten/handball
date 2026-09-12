// Dev-only harness: renders the TopNav exactly as App does, with no auth, so the
// header can be screenshotted at arbitrary widths. Not part of the build.
import React from "react";
import ReactDOM from "react-dom/client";
import { TopNav, TeamSwitcher } from "./ds";
import { NAV } from "./nav";
import "./ds/styles.css";

const params = new URLSearchParams(location.search);
const active = params.get("active") ?? "/roster";
const links = NAV.map((n) => ({
  label: n.to === "/free-agents" ? `${n.label} (2)` : n.label,
  href: "#" + n.to, scope: n.scope, group: n.group,
  active: active.startsWith(n.to),
  onClick: (e: React.MouseEvent) => e.preventDefault(),
}));
links.push({ label: "★ Commissioner", href: "#/commissioner", scope: "league", group: undefined,
  active: active.startsWith("/commissioner"), onClick: (e: React.MouseEvent) => e.preventDefault() });
const teams = [
  { abbr: "LV", name: "Las Vegas", wins: 0, losses: 0, ties: 0 },
  { abbr: "SEA", name: "Seattle", wins: 0, losses: 0, ties: 0 },
  { abbr: "DEN", name: "Denver", wins: 0, losses: 0, ties: 0 },
  { abbr: "ATL", name: "Atlanta", wins: 0, losses: 0, ties: 0 },
];
ReactDOM.createRoot(document.getElementById("root")!).render(
  <div className="app">
    <TopNav
      links={links}
      email="ohvidsten@lbl.gov"
      commissioner
      tradeRequests={3}
      teamSwitcher={<TeamSwitcher teams={teams} activeAbbr="LV" onChange={() => {}} />}
      onBrandClick={() => {}} onBell={() => {}} onAccount={() => {}} onSignOut={() => {}}
    />
    <main style={{ padding: 24 }}>page body</main>
  </div>
);
