import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { DataTable, Alert } from "../ds";

interface Row {
  slug: string;
  name: string;
  wins: number;
  losses: number;
  ties: number;
}

export default function Standings() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    supabase
      .from("teams")
      .select("slug, name, wins, losses, ties")
      .order("wins", { ascending: false })
      .order("losses", { ascending: true })
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setRows((data as Row[]) ?? []);
      });
  }, []);

  const columns = [
    { key: "name", header: "Team", render: (r: Row) => r.name },
    { key: "wins", header: "W", numeric: true },
    { key: "losses", header: "L", numeric: true },
    { key: "ties", header: "T", numeric: true },
    { key: "pts", header: "PTS", numeric: true, render: (r: Row) => r.wins * 2 + r.ties },
  ];

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Standings</h2>
      {err && <Alert tone="error">{err}</Alert>}
      <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
        <DataTable
          rank
          columns={columns}
          rows={rows}
          getRowKey={(r: Row) => r.slug}
          onRowClick={(r: Row) => nav(`/teams/${r.slug}`)}
        />
      </div>
    </section>
  );
}
