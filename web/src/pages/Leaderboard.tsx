import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { DataTable, Tabs, EmptyState, Alert } from "../ds";

interface Row {
  legacy_id: string;
  name: string;
  team_name: string | null;
  position: string;
  games: number;
  goals: number;
  shots: number;
  saves: number;
  goals_allowed: number;
}

const CATEGORIES = [
  { value: "goals", label: "Goals" },
  { value: "shots", label: "Shots" },
  { value: "saves", label: "Saves" },
];

export default function Leaderboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [cat, setCat] = useState("goals");
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    supabase
      .from("player_leaderboard")
      .select("legacy_id, name, team_name, position, games, goals, shots, saves, goals_allowed")
      .order(cat, { ascending: false })
      .limit(25)
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setRows((data as Row[]) ?? []);
      });
  }, [cat]);

  const columns = [
    { key: "name", header: "Player", render: (r: Row) => r.name },
    { key: "team_name", header: "Team", render: (r: Row) => r.team_name ?? "—" },
    { key: "position", header: "Pos" },
    { key: cat, header: CATEGORIES.find((c) => c.value === cat)!.label, numeric: true },
    { key: "games", header: "GP", numeric: true },
  ];

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Scoring leaders</h2>
      <Tabs items={CATEGORIES} value={cat} onChange={setCat} style={{ marginBottom: 16 }} />
      {err && <Alert tone="error">{err}</Alert>}
      {rows.length === 0 && !err ? (
        <EmptyState title="No games played yet" message="Leaders appear once the season's games have been simulated." />
      ) : (
        <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
          <DataTable
            rank
            columns={columns}
            rows={rows}
            getRowKey={(r: Row) => r.legacy_id}
            onRowClick={(r: Row) => nav(`/players/${r.legacy_id}`)}
          />
        </div>
      )}
    </section>
  );
}
