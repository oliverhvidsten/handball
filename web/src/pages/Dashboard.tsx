import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { apiFetch, teamLogoUrl } from "../lib/api";
import { useAuth } from "../auth";
import { StatCard, CapMeter, Alert, EmptyState, Button } from "../ds";

interface Issue { text: string; to: string; }

// handball/signing_service.team_cap_report, drawn by CapMeter. Every rule (cap room,
// tax tiers, MLE, hard-cap headroom) is decided server-side.
interface CapReport {
  payroll: number; cap_room: number; over_cap: boolean;
  over_first_threshold: boolean; over_second_threshold: boolean;
  mid_level_exception: number; hard_cap_room: number; projected_next_payroll: number;
  roster_size: number; max_roster: number; roster_spots: number;
  limits: { salary_cap: number; first_luxury_threshold: number; second_luxury_threshold: number; hard_cap: number };
}

export default function Dashboard() {
  const { activeTeam } = useAuth();
  const nav = useNavigate();
  const [season, setSeason] = useState<number | null>(null);
  const [games, setGames] = useState(0);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [cap, setCap] = useState<CapReport | null>(null);

  useEffect(() => {
    if (!activeTeam) { setCap(null); return; }
    apiFetch<CapReport>(`/teams/${activeTeam.slug}/cap`, { method: "GET" })
      .then(setCap)
      .catch(() => setCap(null));
  }, [activeTeam]);

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
      {activeTeam ? (
        <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "6px 0 4px" }}>
          {/* The team's mark: its logo when it has one, else the abbreviation. Both
              are edited on the Settings page; the city itself is fixed. */}
          <span style={{
            flex: "none", width: 48, height: 48, borderRadius: "var(--radius-md)", overflow: "hidden",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            background: "var(--ink-900)", color: "#fff",
            fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)", fontSize: "var(--text-md)",
          }}>
            {teamLogoUrl(activeTeam.slug, activeTeam.logoVersion)
              ? <img src={teamLogoUrl(activeTeam.slug, activeTeam.logoVersion)!} alt="" width={48} height={48}
                     style={{ width: "100%", height: "100%", objectFit: "contain", display: "block", background: "#fff" }} />
              : activeTeam.abbr}
          </span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-xl)", lineHeight: 1.15 }}>
              {activeTeam.displayName}
            </div>
            <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
              {activeTeam.wins}-{activeTeam.losses}-{activeTeam.ties}
              {!activeTeam.nickname && <> · <a href="#/settings" onClick={(e) => { e.preventDefault(); nav("/settings"); }}>name your team</a></>}
            </div>
          </div>
        </div>
      ) : (
        <p style={{ color: "var(--muted)", marginTop: 0 }}>No active team</p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12, margin: "16px 0 28px" }}>
        <StatCard label="Season" value={season ?? "—"} sub={season ? "current" : "no games yet"} />
        <StatCard label="Games played" value={games} accent="var(--blue-600)" />
        {activeTeam && (
          <StatCard label="Record" value={`${activeTeam.wins}-${activeTeam.losses}-${activeTeam.ties}`} accent="var(--amber-600)" />
        )}
      </div>

      {activeTeam && cap && (
        <div style={{ marginBottom: 28 }}>
          <CapMeter
            cap={cap}
            actions={
              <>
                <Button size="sm" onClick={() => nav("/free-agents")}>Free agents</Button>
                <Button size="sm" onClick={() => nav("/trades")}>Trades</Button>
              </>
            }
          />
        </div>
      )}

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
