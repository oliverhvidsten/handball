import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { TopNav, TeamSwitcher } from "./ds";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import MyTeams from "./pages/MyTeams";
import Teams from "./pages/Teams";
import Roster from "./pages/Roster";
import PlayerDetail from "./pages/PlayerDetail";
import Standings from "./pages/Standings";
import Leaderboard from "./pages/Leaderboard";
import Schedule from "./pages/Schedule";
import Trades from "./pages/Trades";
import Draft from "./pages/Draft";
import Commissioner from "./pages/Commissioner";
import { usePendingTradeCount } from "./hooks";

const NAV = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "My Teams", to: "/my-teams" },
  { label: "Teams", to: "/teams" },
  { label: "Standings", to: "/standings" },
  { label: "Leaders", to: "/leaderboard" },
  { label: "Schedule", to: "/schedule" },
  { label: "Draft", to: "/draft" },
  { label: "Trades", to: "/trades" },
];

export default function App() {
  const { session, isCommissioner, teams, activeTeam, setActiveTeam, signOut, loading } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const pending = usePendingTradeCount();

  if (loading) return <div className="center">Loading…</div>;
  if (!session) return <Login />;

  const links = NAV.map((n) => ({
    label: n.label,
    href: "#" + n.to,
    active: loc.pathname.startsWith(n.to),
    onClick: (e: React.MouseEvent) => { e.preventDefault(); nav(n.to); },
  }));
  if (isCommissioner) {
    links.push({
      label: "★ Commissioner",
      href: "#/commissioner",
      active: loc.pathname.startsWith("/commissioner"),
      onClick: (e: React.MouseEvent) => { e.preventDefault(); nav("/commissioner"); },
    });
  }

  const switcher =
    teams.length > 0 ? (
      <TeamSwitcher
        teams={teams.map((t) => ({ abbr: t.abbr, name: t.name, wins: t.wins, losses: t.losses, ties: t.ties }))}
        activeAbbr={activeTeam?.abbr}
        onChange={(abbr: string) => {
          const t = teams.find((x) => x.abbr === abbr);
          if (t) setActiveTeam(t.slug);
        }}
      />
    ) : null;

  return (
    <div className="app">
      <TopNav
        links={links}
        email={session.user.email ?? ""}
        commissioner={isCommissioner}
        tradeRequests={pending}
        teamSwitcher={switcher}
        onBrandClick={() => nav("/dashboard")}
        onBell={() => nav("/trades")}
        onSignOut={signOut}
      />
      <main style={{ maxWidth: "var(--container-max)", margin: "0 auto", padding: "24px 20px" }}>
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/my-teams" element={<MyTeams />} />
          <Route path="/teams" element={<Teams />} />
          <Route path="/teams/:slug" element={<Roster />} />
          <Route path="/players/:legacyId" element={<PlayerDetail />} />
          <Route path="/standings" element={<Standings />} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/schedule" element={<Schedule />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/draft" element={<Draft />} />
          {/* Mounted unconditionally: role resolves async after a cold load, so a
              conditional mount + the catch-all redirect would bounce a deep link
              to /commissioner over to /dashboard. The nav link stays gated, and
              the panel's actions are enforced server-side (commissioner + RLS). */}
          <Route path="/commissioner" element={<Commissioner />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}
