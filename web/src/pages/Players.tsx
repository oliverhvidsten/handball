import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { abbrev } from "../hooks";
import { DataTable, Input, Select, Tag, StatChip, EmptyState, Alert } from "../ds";

interface PP {
  legacy_id: string;
  team_id: string | null;
  name: string;
  position: string;
  offense: number;
  defense: number;
  goalie_skill: number;
  retired: boolean;
}

const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"] as const;
const POS_TONE: Record<string, any> = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

const POS_OPTIONS = [{ value: "all", label: "All positions" }, ...POSITIONS.map((p) => ({ value: p, label: p }))];
const TEAM_OPTIONS = [
  { value: "all", label: "All active" },
  { value: "fa", label: "Free agents only" },
  { value: "signed", label: "Signed only" },
  { value: "retired", label: "Retired" },
];

export default function Players() {
  const [players, setPlayers] = useState<PP[]>([]);
  const [teamAbbr, setTeamAbbr] = useState<Map<string, string>>(new Map());
  const [err, setErr] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [pos, setPos] = useState("all");
  const [team, setTeam] = useState("all");

  const nav = useNavigate();

  useEffect(() => {
    (async () => {
      const [{ data: ps, error: pe }, { data: ts, error: te }] = await Promise.all([
        supabase
          .from("player_public")
          .select("legacy_id, team_id, name, position, offense, defense, goalie_skill, retired")
          .order("name", { ascending: true }),
        supabase.from("teams").select("id, name"),
      ]);
      if (pe || te) { setErr((pe ?? te)!.message); return; }
      setPlayers((ps as PP[]) ?? []);
      setTeamAbbr(new Map(((ts as { id: string; name: string }[]) ?? []).map((t) => [t.id, abbrev(t.name)])));
    })();
  }, []);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return players.filter((p) => {
      if (q && !p.name.toLowerCase().includes(q)) return false;
      if (pos !== "all" && p.position !== pos) return false;
      // Retired players are shown only under the explicit "Retired" filter.
      if (team === "retired") return p.retired;
      if (p.retired) return false;
      if (team === "fa" && p.team_id !== null) return false;
      if (team === "signed" && p.team_id === null) return false;
      return true;
    });
  }, [players, query, pos, team]);

  const columns = [
    { key: "name", header: "Player", render: (r: PP) => r.name },
    {
      key: "position",
      header: "Pos",
      render: (r: PP) => <Tag tone={POS_TONE[r.position] || "neutral"} size="sm">{r.position}</Tag>,
    },
    {
      key: "stats",
      header: "Stats",
      render: (r: PP) =>
        r.position === "Goalie" ? (
          <StatChip kind="goalie" value={r.goalie_skill.toFixed(1)} />
        ) : (
          <span style={{ display: "inline-flex", gap: 6 }}>
            <StatChip kind="offense" value={r.offense.toFixed(1)} />
            <StatChip kind="defense" value={r.defense.toFixed(1)} />
          </span>
        ),
    },
    {
      key: "team",
      header: "Team",
      render: (r: PP) =>
        r.retired ? (
          <Tag tone="neutral" size="sm">Retired</Tag>
        ) : r.team_id === null ? (
          <Tag tone="neutral" size="sm">FA</Tag>
        ) : (
          <span style={{ fontWeight: "var(--weight-semibold)" }}>{teamAbbr.get(r.team_id) ?? "—"}</span>
        ),
    },
  ];

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Players</h2>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", marginBottom: 16 }}>
        <div style={{ flex: "1 1 220px", minWidth: 200 }}>
          <Input
            label="Search"
            placeholder="Find a player by name…"
            value={query}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
          />
        </div>
        <div style={{ width: 170 }}>
          <Select label="Position" options={POS_OPTIONS} value={pos} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setPos(e.target.value)} />
        </div>
        <div style={{ width: 170 }}>
          <Select label="Status" options={TEAM_OPTIONS} value={team} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTeam(e.target.value)} />
        </div>
      </div>

      {err && <Alert tone="error">{err}</Alert>}

      {!err && rows.length === 0 ? (
        <EmptyState
          title="No players match"
          message={players.length === 0 ? "No players found." : "Try clearing the search or filters."}
        />
      ) : (
        <>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 8px" }}>
            {rows.length} {rows.length === 1 ? "player" : "players"}
          </p>
          <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
            <DataTable
              columns={columns}
              rows={rows}
              getRowKey={(r: PP) => r.legacy_id}
              onRowClick={(r: PP) => nav(`/players/${r.legacy_id}`)}
            />
          </div>
        </>
      )}
    </section>
  );
}
