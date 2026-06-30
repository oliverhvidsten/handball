import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { BoxScore, EmptyState, Tabs, Alert } from "../ds";

interface GameRow {
  id: string;
  season: number;
  week: number | null;
  home_score: number;
  away_score: number;
  went_to_overtime: boolean;
  home: { name: string } | null;
  away: { name: string } | null;
}

interface FixtureRow {
  id: string;
  season: number;
  week: number;
  home: { name: string } | null;
  away: { name: string } | null;
}

export default function Schedule() {
  const [tab, setTab] = useState("results");
  const [games, setGames] = useState<GameRow[]>([]);
  const [upcoming, setUpcoming] = useState<FixtureRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    supabase
      .from("games")
      .select("id, season, week, home_score, away_score, went_to_overtime, home:home_team_id(name), away:away_team_id(name)")
      .order("season", { ascending: false })
      .order("week", { ascending: false })
      .limit(40)
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setGames((data as any as GameRow[]) ?? []);
      });
  }, []);

  // Upcoming fixtures: the persisted schedule for the latest season, restricted to
  // weeks that haven't been played yet (max played week derived from games above).
  useEffect(() => {
    (async () => {
      const { data: seasonRow } = await supabase
        .from("schedule_games")
        .select("season")
        .order("season", { ascending: false })
        .limit(1);
      const season = (seasonRow as { season: number }[] | null)?.[0]?.season;
      if (season == null) { setUpcoming([]); return; }

      const { data: playedRow } = await supabase
        .from("games")
        .select("week")
        .eq("season", season)
        .order("week", { ascending: false })
        .limit(1);
      const playedThrough = (playedRow as { week: number | null }[] | null)?.[0]?.week ?? 0;

      const { data, error } = await supabase
        .from("schedule_games")
        .select("id, season, week, home:home_team_id(name), away:away_team_id(name)")
        .eq("season", season)
        .gt("week", playedThrough ?? 0)
        .order("week", { ascending: true })
        .limit(80);
      if (error) setErr(error.message);
      else setUpcoming((data as any as FixtureRow[]) ?? []);
    })();
  }, []);

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Schedule &amp; Results</h2>
      <Tabs items={[{ value: "results", label: "Results" }, { value: "upcoming", label: "Upcoming" }]} value={tab} onChange={setTab} style={{ marginBottom: 16 }} />
      {err && <Alert tone="error">{err}</Alert>}

      {tab === "upcoming" ? (
        upcoming.length === 0 ? (
          <EmptyState title="No upcoming games" message="Generate a schedule from the Commissioner page, or the season has finished playing out." />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {upcoming.map((f) => {
              const away = f.away?.name ?? "Away";
              const home = f.home?.name ?? "Home";
              return (
                <div
                  key={f.id}
                  className="nha-row"
                  style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "12px 14px",
                    background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)",
                  }}
                >
                  <span style={{ fontSize: "var(--text-xs)", color: "var(--muted)", width: 90 }}>
                    Wk {f.week} · S{f.season}
                  </span>
                  <span style={{ flex: 1, fontFamily: "var(--font-display)", fontWeight: 700 }}>
                    {away} <span style={{ color: "var(--muted)" }}>@</span> {home}
                  </span>
                </div>
              );
            })}
          </div>
        )
      ) : games.length === 0 ? (
        <EmptyState title="No games played yet" message="Results appear once the season has been simulated." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {games.map((g) => {
            const expanded = open === g.id;
            const away = g.away?.name ?? "Away";
            const home = g.home?.name ?? "Home";
            return (
              <div key={g.id} style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)" }}>
                <button
                  onClick={() => setOpen(expanded ? null : g.id)}
                  className="nha-row"
                  style={{
                    width: "100%", textAlign: "left", display: "flex", alignItems: "center", gap: 12,
                    padding: "12px 14px", background: "transparent", border: "none", cursor: "pointer",
                  }}
                >
                  <span style={{ fontSize: "var(--text-xs)", color: "var(--muted)", width: 90 }}>
                    {g.week != null ? `Wk ${g.week} · ` : ""}S{g.season}
                  </span>
                  <span style={{ flex: 1, fontFamily: "var(--font-display)", fontWeight: 700 }}>
                    {away} <span style={{ color: "var(--muted)" }}>@</span> {home}
                  </span>
                  <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
                    {g.away_score}–{g.home_score}{g.went_to_overtime ? " OT" : ""}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{expanded ? "▴" : "▾"}</span>
                </button>
                {expanded && (
                  <div style={{ padding: "0 14px 14px" }}>
                    <BoxScore
                      away={{ name: away, score: g.away_score, periods: [], scorers: [] }}
                      home={{ name: home, score: g.home_score, periods: [], scorers: [] }}
                      periods={[]}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
