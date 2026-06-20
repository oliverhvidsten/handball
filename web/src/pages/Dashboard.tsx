import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { useAuth } from "../auth";
import { StatCard, Alert, EmptyState, Button } from "../ds";

interface Issue { text: string; to: string; }

export default function Dashboard() {
  const { activeTeam, teams } = useAuth();
  const nav = useNavigate();
  const [season, setSeason] = useState<number | null>(null);
  const [games, setGames] = useState(0);
  const [issues, setIssues] = useState<Issue[]>([]);

  useEffect(() => {
    supabase.from("games").select("season", { count: "exact" }).order("season", { ascending: false }).limit(1)
      .then(({ data, count }) => {
        setGames(count ?? 0);
        setSeason((data && data[0]?.season) ?? null);
      });
  }, []);

  useEffect(() => {
    if (!activeTeam) { setIssues([]); return; }
    (async () => {
      const found: Issue[] = [];
      // injured player in a starting slot
      const { data: starters } = await supabase
        .from("player_public")
        .select("name, is_injured, slot_group")
        .eq("team_id", activeTeam.id)
        .eq("slot_group", "starters")
        .eq("is_injured", true);
      (starters ?? []).forEach((p: any) =>
        found.push({ text: `Injured starter: ${p.name} — set your lineup`, to: `/roster` })
      );
      // pending trade requests addressed to this team
      const { count } = await supabase
        .from("trades")
        .select("id", { count: "exact", head: true })
        .eq("status", "proposed")
        .eq("to_team_id", activeTeam.id);
      if (count && count > 0)
        found.push({ text: `${count} trade request${count > 1 ? "s" : ""} awaiting your response`, to: "/trades" });
      setIssues(found);
    })();
  }, [activeTeam]);

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>Dashboard</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        {activeTeam ? activeTeam.name : "No active team"}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12, margin: "16px 0 28px" }}>
        <StatCard label="Season" value={season ?? "—"} sub={season ? "current" : "no games yet"} />
        <StatCard label="Games played" value={games} accent="var(--blue-600)" />
        <StatCard label="Your teams" value={teams.length} accent="var(--purple-600)" />
        {activeTeam && (
          <StatCard label="Record" value={`${activeTeam.wins}-${activeTeam.losses}-${activeTeam.ties}`} accent="var(--amber-600)" />
        )}
      </div>

      <h3 style={{ marginBottom: 10 }}>Items to resolve {activeTeam ? `· ${activeTeam.name}` : ""}</h3>
      {issues.length === 0 ? (
        <EmptyState compact title="All clear" message="No pending trades, injured starters, or lineup problems." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {issues.map((it, i) => (
            <Alert key={i} tone="warning" style={{ alignItems: "center" }}>
              <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, width: "100%" }}>
                {it.text}
                <Button size="sm" onClick={() => nav(it.to)}>Resolve</Button>
              </span>
            </Alert>
          ))}
        </div>
      )}
    </section>
  );
}
