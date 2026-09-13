import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, apiFetch } from "../lib/api";
import { DataTable, Alert, Tabs } from "../ds";

interface Row {
  rank: number;
  slug: string;
  name: string;
  wins: number;
  losses: number;
  ties: number;
  points: number;
  goals_for: number;
  goals_against: number;
  goal_diff: number;
  conference: string | null;
  division: string | null;
  division_leader: boolean;
  playoff_seed: number | null;
}

// Served by the API, not read from Supabase: the ranking's head-to-head step can't
// be expressed as an ORDER BY, and a table that sorted differently from the bracket
// it seeds would be worse than no table at all.
export default function Standings() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState("Eastern");
  const nav = useNavigate();

  useEffect(() => {
    apiFetch<{ teams: Row[] }>("/standings", { method: "GET" })
      .then((d) => setRows(d.teams))
      .catch((e) => setErr(e instanceof ApiError ? e.message : "could not load standings"));
  }, []);

  const conferences = [...new Set(rows.map((r) => r.conference).filter(Boolean))] as string[];
  const tabs = [...conferences, "League"].map((c) => ({ value: c, label: c }));
  const shown =
    view === "League" ? rows : rows.filter((r) => r.conference === view);

  const columns = [
    {
      key: "name",
      header: "Team",
      render: (r: Row) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          {/* A seed is only meaningful inside a conference view; in the league
              view the numbers from the two conferences would interleave. */}
          {view !== "League" && r.playoff_seed != null && (
            <span
              title={`Seed ${r.playoff_seed}`}
              style={{
                minWidth: 18, textAlign: "center", fontSize: "var(--text-xs)",
                fontWeight: "var(--weight-bold)", color: "var(--text-soft)",
                background: "var(--surface-3)", borderRadius: "var(--radius-sm)",
                padding: "1px 4px",
              }}
            >
              {r.playoff_seed}
            </span>
          )}
          <span>{r.name}</span>
          {r.division_leader && (
            <span title={`Leads the ${r.division} division`} style={{ color: "var(--green-700)" }}>
              ★
            </span>
          )}
        </span>
      ),
    },
    { key: "division", header: "Division", render: (r: Row) => r.division ?? "—" },
    { key: "wins", header: "W", numeric: true },
    { key: "losses", header: "L", numeric: true },
    { key: "ties", header: "T", numeric: true },
    { key: "points", header: "PTS", numeric: true },
    {
      key: "goal_diff",
      header: "GD",
      numeric: true,
      render: (r: Row) => (r.goal_diff > 0 ? `+${r.goal_diff}` : String(r.goal_diff)),
    },
  ];

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>Standings</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        ★ Division Leader
      </p>
      {err && <Alert tone="error">{err}</Alert>}

      {conferences.length > 0 && (
        <div style={{ margin: "16px 0" }}>
          <Tabs items={tabs} value={view} onChange={setView} />
        </div>
      )}

      <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
        <DataTable
          columns={columns}
          rows={shown}
          getRowKey={(r: Row) => r.slug}
          onRowClick={(r: Row) => nav(`/teams/${r.slug}`)}
        />
      </div>
    </section>
  );
}
