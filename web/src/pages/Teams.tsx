import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { useAuth } from "../auth";
import { abbrev } from "../hooks";
import { TeamCard, Alert } from "../ds";

interface Row {
  slug: string;
  name: string;
  wins: number;
  losses: number;
  ties: number;
}

// Conference / division structure. Source of truth is the `league` dict in
// handball/schedule_generator.py — keep these in sync. Teams are keyed by their
// display name (the `name` column), and listed in league-defined order.
const LEAGUE: { conference: string; divisions: { division: string; teams: string[] }[] }[] = [
  {
    conference: "Eastern Conference",
    divisions: [
      { division: "Mid-Atlantic", teams: ["Boston", "New York", "Philadelphia", "Washington"] },
      { division: "South", teams: ["Charlotte", "Atlanta", "Miami", "Tampa Bay"] },
      { division: "Midwest", teams: ["Toronto", "Detroit", "Cleveland", "Chicago"] },
      { division: "Country", teams: ["Cincinnati", "Louisville", "Nashville", "Indianapolis"] },
    ],
  },
  {
    conference: "Western Conference",
    divisions: [
      { division: "North", teams: ["Milwaukee", "Minneapolis", "St. Louis", "Kansas City"] },
      { division: "South", teams: ["Oklahoma City", "New Orleans", "Dallas", "Houston"] },
      { division: "Pacific", teams: ["Phoenix", "Los Angeles", "San Diego", "San Francisco"] },
      { division: "Mountain", teams: ["Las Vegas", "Denver", "Seattle", "Vancouver"] },
    ],
  },
];

const ASSIGNED = new Set(LEAGUE.flatMap((c) => c.divisions.flatMap((d) => d.teams)));

export default function Teams() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const { teams } = useAuth();
  const nav = useNavigate();
  const ownedSlugs = new Set(teams.map((t) => t.slug));

  useEffect(() => {
    supabase
      .from("teams")
      .select("slug, name, wins, losses, ties")
      .order("wins", { ascending: false })
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setRows((data as Row[]) ?? []);
      });
  }, []);

  // Look up by name; league-wide rank follows the wins-desc fetch order.
  const byName = new Map(rows.map((r) => [r.name, r]));
  const rankByName = new Map(rows.map((r, i) => [r.name, i + 1]));

  const card = (r: Row) => (
    <TeamCard
      key={r.slug}
      team={{ name: r.name, abbr: abbrev(r.name), wins: r.wins, losses: r.losses, ties: r.ties, rank: rankByName.get(r.name) }}
      yours={ownedSlugs.has(r.slug)}
      onClick={() => nav(`/teams/${r.slug}`)}
    />
  );

  const grid: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
    gap: 12,
  };

  // Any team not present in LEAGUE (defensive — shouldn't normally happen).
  const unassigned = rows.filter((r) => !ASSIGNED.has(r.name));

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Teams</h2>
      {err && <Alert tone="error">{err}</Alert>}

      {LEAGUE.map((conf) => (
        <div key={conf.conference} style={{ marginBottom: 32 }}>
          <h3
            style={{
              fontSize: "var(--text-xl)",
              paddingBottom: 8,
              marginBottom: 16,
              borderBottom: "2px solid var(--line)",
            }}
          >
            {conf.conference}
          </h3>
          {conf.divisions.map((div) => {
            const divRows = div.teams.map((n) => byName.get(n)).filter((r): r is Row => !!r);
            if (divRows.length === 0) return null;
            return (
              <div key={div.division} style={{ marginBottom: 20 }}>
                <div
                  style={{
                    fontSize: "var(--text-2xs)",
                    fontWeight: "var(--weight-bold)",
                    letterSpacing: "var(--tracking-wide)",
                    textTransform: "uppercase",
                    color: "var(--muted)",
                    marginBottom: 8,
                  }}
                >
                  {div.division}
                </div>
                <div style={grid}>{divRows.map(card)}</div>
              </div>
            );
          })}
        </div>
      ))}

      {unassigned.length > 0 && (
        <div style={{ marginBottom: 32 }}>
          <h3 style={{ fontSize: "var(--text-xl)", paddingBottom: 8, marginBottom: 16, borderBottom: "2px solid var(--line)" }}>
            Other
          </h3>
          <div style={grid}>{unassigned.map(card)}</div>
        </div>
      )}
    </section>
  );
}
