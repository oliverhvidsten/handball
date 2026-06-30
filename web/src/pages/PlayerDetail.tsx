import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { StatCard, StatChip, DataTable, Tag, Alert, EmptyState } from "../ds";

interface PP {
  id: string;
  legacy_id: string;
  name: string;
  position: string;
  age: number;
  offense: number;
  defense: number;
  goalie_skill: number;
  is_injured: boolean;
  contract_term: number;
  contract_value: number;
  years_remaining: number;
}
interface SeasonRow { season: number; games: number; goals: number; shots: number; saves: number; goals_allowed: number; }
interface Injury { year: number; injury_type: string; duration: number; is_current: boolean; }
interface Award { season: number; award: string; }
const POS_TONE: Record<string, any> = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

export default function PlayerDetail() {
  const { legacyId = "" } = useParams();
  const [p, setP] = useState<PP | null>(null);
  const [seasons, setSeasons] = useState<SeasonRow[]>([]);
  const [injuries, setInjuries] = useState<Injury[]>([]);
  const [awards, setAwards] = useState<Award[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const { data: pp, error } = await supabase.from("player_public").select("*").eq("legacy_id", legacyId).maybeSingle();
      if (error) { setErr(error.message); return; }
      if (!pp) { setErr(`No player "${legacyId}".`); return; }
      setP(pp as PP);
      const [{ data: s }, { data: inj }, { data: aw }] = await Promise.all([
        supabase.from("player_leaderboard").select("season, games, goals, shots, saves, goals_allowed").eq("legacy_id", legacyId).order("season", { ascending: false }),
        supabase.from("injuries").select("year, injury_type, duration, is_current").eq("player_id", (pp as PP).id).order("year", { ascending: false }),
        supabase.from("awards").select("season, award").eq("player_id", (pp as PP).id).order("season", { ascending: false }),
      ]);
      setSeasons((s as SeasonRow[]) ?? []);
      setInjuries((inj as Injury[]) ?? []);
      setAwards((aw as Award[]) ?? []);
    })();
  }, [legacyId]);

  if (err) return <Alert tone="error">{err}</Alert>;
  if (!p) return <div className="center">Loading…</div>;

  const career = seasons.reduce(
    (a, s) => ({ games: a.games + s.games, goals: a.goals + s.goals, shots: a.shots + s.shots, saves: a.saves + s.saves }),
    { games: 0, goals: 0, shots: 0, saves: 0 }
  );
  const isG = p.position === "Goalie";
  const yrs = p.years_remaining;
  const isExpired = yrs <= 0;
  const yearsLeft = yrs > 1 ? `${yrs} yrs left` : yrs === 1 ? "1 yr left" : "expired";
  const labelStyle: React.CSSProperties = { fontWeight: "var(--weight-bold)", color: "var(--muted)" };

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <h2>{p.name}</h2>
        <Tag tone={POS_TONE[p.position] || "neutral"}>{p.position}</Tag>
        {p.is_injured && <Tag tone="red" solid>INJ</Tag>}
      </div>
      <div style={{ color: "var(--muted)", marginTop: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <span><span style={labelStyle}>Age</span> {p.age}</span>
        <span>
          <span style={labelStyle}>Contract</span> {p.contract_term}yr / ${p.contract_value}M ·{" "}
          {isExpired
            ? <span style={{ color: "var(--red-text)", fontWeight: "var(--weight-semibold)" }}>expired</span>
            : yearsLeft}
        </span>
      </div>

      <div style={{ display: "flex", gap: 8, margin: "12px 0 22px" }}>
        <StatChip kind="offense" value={p.offense.toFixed(1)} />
        <StatChip kind="defense" value={p.defense.toFixed(1)} />
        {isG && <StatChip kind="goalie" value={p.goalie_skill.toFixed(1)} />}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 24 }}>
        <StatCard label="Career GP" value={career.games} />
        <StatCard label={isG ? "Career Saves" : "Career Goals"} value={isG ? career.saves : career.goals} accent="var(--green-600)" />
        {!isG && <StatCard label="Career Shots" value={career.shots} accent="var(--blue-600)" />}
      </div>

      <h3 style={{ marginBottom: 10 }}>By season</h3>
      {seasons.length === 0 ? (
        <EmptyState compact title="No game data yet" message="Stats appear once games are simulated." />
      ) : (
        <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden", marginBottom: 24 }}>
          <DataTable
            columns={[
              { key: "season", header: "Season" },
              { key: "games", header: "GP", numeric: true },
              { key: "goals", header: "G", numeric: true },
              { key: "shots", header: "Shots", numeric: true },
              { key: "saves", header: "SV", numeric: true },
              { key: "goals_allowed", header: "GA", numeric: true },
            ]}
            rows={seasons}
            getRowKey={(r: SeasonRow) => r.season}
          />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <div>
          <h3 style={{ marginBottom: 10 }}>Injury history</h3>
          {injuries.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No injuries on record.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {injuries.map((i, k) => (
                <div key={k} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "var(--text-sm)" }}>
                  <Tag tone={i.is_current ? "red" : "neutral"} size="sm">{i.year}</Tag>
                  <span>{i.injury_type}</span>
                  <span style={{ color: "var(--muted)" }}>· {i.duration} {i.duration === 1 ? "period" : "periods"}{i.is_current ? " · active" : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <h3 style={{ marginBottom: 10 }}>Awards</h3>
          {awards.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No awards yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {awards.map((a, k) => (
                <div key={k} style={{ fontSize: "var(--text-sm)" }}>
                  <Tag tone="amber" size="sm">{a.season}</Tag> <span style={{ marginLeft: 6 }}>{a.award}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
