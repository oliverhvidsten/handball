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

export default function Schedule() {
  const [tab, setTab] = useState("results");
  const [games, setGames] = useState<GameRow[]>([]);
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

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Schedule &amp; Results</h2>
      <Tabs items={[{ value: "results", label: "Results" }, { value: "upcoming", label: "Upcoming" }]} value={tab} onChange={setTab} style={{ marginBottom: 16 }} />
      {err && <Alert tone="error">{err}</Alert>}

      {tab === "upcoming" ? (
        <EmptyState title="Upcoming games coming soon" message="The schedule isn't persisted yet, so upcoming fixtures aren't available. Recent results are under the Results tab." />
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
