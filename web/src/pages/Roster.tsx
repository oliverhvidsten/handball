import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import { RosterColumns, PlayerRow, Alert, Button, Toast } from "../ds";

const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"] as const;
type Group = "starters" | "bench" | "reserves";
const STARTER_CAPS: Record<string, number> = { Forward: 3, Midfielder: 3, Defense: 3, Goalie: 1 };
const BENCH_CAPS: Record<string, number> = { Forward: 2, Midfielder: 2, Defense: 2, Goalie: 1 };

interface PP {
  id: string;
  legacy_id: string;
  name: string;
  position: string;
  slot_group: Group | null;
  slot_position: string | null;
  slot_order: number | null;
  offense: number;
  defense: number;
  goalie_skill: number;
  is_injured: boolean;
}
interface Arrangement {
  starters: Record<string, string[]>;
  bench: Record<string, string[]>;
  reserves: string[];
}

function emptyArr(): Arrangement {
  const byPos = () => Object.fromEntries(POSITIONS.map((p) => [p, [] as string[]]));
  return { starters: byPos(), bench: byPos(), reserves: [] };
}
function buildArr(players: PP[]): Arrangement {
  const arr = emptyArr();
  for (const p of [...players].sort((a, b) => (a.slot_order ?? 0) - (b.slot_order ?? 0))) {
    if (p.slot_group === "starters" && p.slot_position) arr.starters[p.slot_position].push(p.legacy_id);
    else if (p.slot_group === "bench" && p.slot_position) arr.bench[p.slot_position].push(p.legacy_id);
    else if (p.slot_group === "reserves") arr.reserves.push(p.legacy_id);
  }
  return arr;
}

export default function Roster() {
  const { slug = "" } = useParams();
  const { isCommissioner, teams } = useAuth();
  const editable = isCommissioner || teams.some((t) => t.slug === slug);
  const nav = useNavigate();

  const [players, setPlayers] = useState<PP[]>([]);
  const [teamName, setTeamName] = useState(slug);
  const [arr, setArr] = useState<Arrangement>(emptyArr());
  const [problems, setProblems] = useState<string[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setProblems([]);
    setErr(null);
    const { data: team } = await supabase.from("teams").select("id, name").eq("slug", slug).maybeSingle();
    if (!team) { setErr(`No team "${slug}".`); return; }
    setTeamName(team.name);
    const { data, error } = await supabase.from("player_public").select("*").eq("team_id", team.id);
    if (error) { setErr(error.message); return; }
    const ps = (data as PP[]) ?? [];
    setPlayers(ps);
    setArr(buildArr(ps));
  }, [slug]);

  useEffect(() => { void load(); }, [load]);

  const byId = useMemo(() => new Map(players.map((p) => [p.legacy_id, p])), [players]);
  const dsPlayer = (id: string) => {
    const p = byId.get(id);
    return p && { id: p.legacy_id, name: p.name, position: p.position, offense: p.offense, defense: p.defense, goalie: p.goalie_skill, injured: p.is_injured };
  };
  const cols = (group: "starters" | "bench") =>
    Object.fromEntries(POSITIONS.map((pos) => [pos, arr[group][pos].map(dsPlayer).filter(Boolean)]));

  function update(mut: (d: Arrangement) => void) {
    setArr((cur) => {
      const next: Arrangement = {
        starters: Object.fromEntries(Object.entries(cur.starters).map(([k, v]) => [k, [...v]])),
        bench: Object.fromEntries(Object.entries(cur.bench).map(([k, v]) => [k, [...v]])),
        reserves: [...cur.reserves],
      };
      mut(next);
      return next;
    });
  }
  function removeEverywhere(d: Arrangement, id: string) {
    for (const pos of POSITIONS) {
      d.starters[pos] = d.starters[pos].filter((x) => x !== id);
      d.bench[pos] = d.bench[pos].filter((x) => x !== id);
    }
    d.reserves = d.reserves.filter((x) => x !== id);
  }
  function locate(d: Arrangement, id: string): string[] | null {
    for (const pos of POSITIONS) {
      if (d.starters[pos].includes(id)) return d.starters[pos];
      if (d.bench[pos].includes(id)) return d.bench[pos];
    }
    if (d.reserves.includes(id)) return d.reserves;
    return null;
  }
  const onMove = (pl: { id: string }, dir: number) =>
    update((d) => {
      const list = locate(d, pl.id);
      if (!list) return;
      const i = list.indexOf(pl.id);
      const j = i + dir;
      if (j < 0 || j >= list.length) return;
      [list[i], list[j]] = [list[j], list[i]];
    });
  const onSlot = (pl: { id: string }, group: Group) => {
    const p = byId.get(pl.id);
    if (!p) return;
    update((d) => {
      removeEverywhere(d, pl.id);
      if (group === "reserves") d.reserves.push(pl.id);
      else d[group][p.position].push(pl.id);
    });
  };

  async function save() {
    setProblems([]);
    setErr(null);
    setSaving(true); // shows feedback through a slow first request (cold-start host)
    try {
      await apiFetch(`/teams/${slug}/arrangement`, { method: "PUT", body: JSON.stringify(arr) });
      setToast("Lineup saved.");
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.problems) setProblems(e.problems);
      else setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 16 }}>
        <h2>{teamName}{editable ? "" : <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)", marginLeft: 10 }}>read-only</span>}</h2>
      </div>
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}
      {problems.length > 0 && <Alert tone="error" title="Invalid lineup" items={problems} style={{ marginBottom: 14 }} />}

      <h3 style={{ margin: "16px 0 8px" }}>Starters</h3>
      <RosterColumns byPosition={cols("starters")} caps={STARTER_CAPS} editable={editable} slot="starters"
        onMove={onMove} onSlot={onSlot} onPlayerClick={(p: { id: string }) => nav(`/players/${p.id}`)} />

      <h3 style={{ margin: "20px 0 8px" }}>Bench</h3>
      <RosterColumns byPosition={cols("bench")} caps={BENCH_CAPS} editable={editable} slot="bench"
        onMove={onMove} onSlot={onSlot} onPlayerClick={(p: { id: string }) => nav(`/players/${p.id}`)} />

      <h3 style={{ margin: "20px 0 8px" }}>Reserves</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, maxWidth: 520 }}>
        {arr.reserves.length === 0 && <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>None.</span>}
        {arr.reserves.map((id) => {
          const p = dsPlayer(id);
          return p && <PlayerRow key={id} player={p} editable={editable} slot="reserves"
            onMoveUp={() => onMove(p, -1)} onMoveDown={() => onMove(p, 1)} onSlot={(g: Group) => onSlot(p, g)}
            onClick={() => nav(`/players/${id}`)} />;
        })}
      </div>

      {editable && (
        <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
          <Button variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save lineup"}
          </Button>
          <Button onClick={() => setArr(buildArr(players))} disabled={saving}>Reset</Button>
        </div>
      )}

      {toast && (
        <div style={{ position: "fixed", right: 20, bottom: 20, zIndex: 80 }}>
          <Toast tone="success" title={toast} onClose={() => setToast(null)} />
        </div>
      )}
    </section>
  );
}
