import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { ROLE_LABEL, ROLE_ORDER, ROLE_TONE } from "../lib/coaches";
import { DataTable, Input, Select, Tag, EmptyState, Alert } from "../ds";

interface Row {
  coach_legacy_id: string;
  coach_name: string;
  age: number | null;
  pool_role: string;
  cur_role: string | null;
  current_team_slug: string | null;
  current_team_name: string | null;
}

const ROLE_OPTIONS = [
  { value: "all", label: "All roles" },
  ...ROLE_ORDER.map((r) => ({ value: r, label: ROLE_LABEL[r] })),
];
const STATUS_OPTIONS = [
  { value: "all", label: "All coaches" },
  { value: "assigned", label: "Assigned only" },
  { value: "fa", label: "Free agents only" },
];

export default function Coaches() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [status, setStatus] = useState("all");
  const nav = useNavigate();

  useEffect(() => {
    supabase
      .from("coach_public")
      .select("coach_legacy_id, coach_name, age, pool_role, cur_role, current_team_slug, current_team_name")
      .order("coach_name", { ascending: true })
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setRows((data as Row[]) ?? []);
      });
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !r.coach_name.toLowerCase().includes(q)) return false;
      if (role !== "all" && (r.cur_role ?? r.pool_role) !== role) return false;
      if (status === "assigned" && r.cur_role === null) return false;
      if (status === "fa" && r.cur_role !== null) return false;
      return true;
    });
  }, [rows, query, role, status]);

  const columns = [
    { key: "coach_name", header: "Coach", render: (r: Row) => r.coach_name },
    {
      key: "role",
      header: "Role",
      render: (r: Row) => {
        const role = r.cur_role ?? r.pool_role;
        return <Tag tone={ROLE_TONE[role] || "neutral"} size="sm">{ROLE_LABEL[role] || role}</Tag>;
      },
    },
    {
      key: "team",
      header: "Team",
      render: (r: Row) =>
        r.current_team_name ?? <span style={{ color: "var(--muted)" }}>Free agent</span>,
    },
    { key: "age", header: "Age", numeric: true, render: (r: Row) => r.age ?? "—" },
  ];

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Coaches</h2>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", marginBottom: 16 }}>
        <div style={{ flex: "1 1 220px", minWidth: 200 }}>
          <Input
            label="Search"
            placeholder="Find a coach by name…"
            value={query}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
          />
        </div>
        <div style={{ width: 200 }}>
          <Select label="Role" options={ROLE_OPTIONS} value={role} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setRole(e.target.value)} />
        </div>
        <div style={{ width: 170 }}>
          <Select label="Status" options={STATUS_OPTIONS} value={status} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setStatus(e.target.value)} />
        </div>
      </div>

      {err && <Alert tone="error">{err}</Alert>}

      {!err && filtered.length === 0 ? (
        <EmptyState
          title="No coaches match"
          message={rows.length === 0 ? "No coaches have been assigned yet." : "Try clearing the search or filters."}
        />
      ) : (
        <>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 8px" }}>
            {filtered.length} {filtered.length === 1 ? "coach" : "coaches"}
          </p>
          <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
            <DataTable
              columns={columns}
              rows={filtered}
              getRowKey={(r: Row) => r.coach_legacy_id}
              onRowClick={(r: Row) => nav(`/coaches/${r.coach_legacy_id}`)}
            />
          </div>
        </>
      )}
    </section>
  );
}
