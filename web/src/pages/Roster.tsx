import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import { RosterColumns, PlayerRow, Alert, Button, Toast, EmptyState } from "../ds";
import { ROLE_LABEL, ROLE_ORDER } from "../lib/coaches";

const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"] as const;
type Group = "starters" | "bench" | "reserves";
const STARTER_CAPS: Record<string, number> = { Forward: 3, Midfielder: 3, Defense: 3, Goalie: 1 };
const BENCH_CAPS: Record<string, number> = { Forward: 2, Midfielder: 2, Defense: 2, Goalie: 1 };
const RESERVE_MAX = 4;

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
interface CoachRow { role: string; coach_legacy_id: string; coach_name: string; }

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

// Mirrors handball/game_simulator.py's init_stats/calculate_stats, minus the
// per-game RNG draw -- this is a static "current roster strength" reading,
// not a simulated single-game roll. Weights/minutes match MAIN_STAT (3),
// SECONDARY_STAT (1), MIDDIE_STATS (2), STARTER_MINUTES (45), BENCH_MINUTES
// (22.5) in handball/simulation_vars.py. Weighted by lineup slot, not a
// player's rated `position` -- the simulator iterates
// team_obj.starters["Forward"] etc., so an off-position player (e.g. a
// Forward-rated player slotted at Midfielder) is weighted by the slot.
// Takes the in-progress `arr` (not the last-saved `players` rows) so the
// numbers track lineup edits live, before Save.
const POSITION_WEIGHTS: Record<string, { off: number; def: number }> = {
  Forward: { off: 3, def: 1 },
  Midfielder: { off: 2, def: 2 },
  Defense: { off: 1, def: 3 },
};
function computeTeamStats(arr: Arrangement, byId: Map<string, PP>): { offense: number; defense: number } {
  let offense = 0;
  let defense = 0;
  const tally = (group: "starters" | "bench", minutes: number) => {
    for (const pos of POSITIONS) {
      for (const id of arr[group][pos]) {
        const p = byId.get(id);
        if (!p || p.is_injured) continue;
        if (pos === "Goalie") {
          defense += p.goalie_skill * 4 * (minutes / 60);
          continue;
        }
        const w = POSITION_WEIGHTS[pos];
        if (!w) continue;
        offense += p.offense * w.off * (minutes / 60);
        defense += p.defense * w.def * (minutes / 60);
      }
    }
  };
  tally("starters", 45);
  tally("bench", 22.5);
  return { offense, defense };
}

// Large, stacked variants of the player-card StatChip (see
// ds/components/data/StatChip.jsx) -- same offense/defense color tokens,
// scaled up into standalone tiles for the team-level summary.
function TeamStatBox({ kind, label, value }: { kind: "offense" | "defense"; label: string; value: number }) {
  const bg = kind === "offense" ? "var(--stat-offense-bg)" : "var(--stat-defense-bg)";
  const fg = kind === "offense" ? "var(--stat-offense-text)" : "var(--stat-defense-text)";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 2,
        minWidth: 100,
        padding: "10px 20px",
        background: bg,
        color: fg,
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <span style={{ fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)", letterSpacing: "0.05em", opacity: 0.75 }}>
        {label}
      </span>
      <span style={{ fontSize: "var(--text-2xl)", fontWeight: "var(--weight-black)", fontVariantNumeric: "tabular-nums" }}>
        {value.toFixed(1)}
      </span>
    </div>
  );
}

export default function Roster() {
  // The "/teams/:slug" route views any team (read-only unless owned); the
  // "/roster" nav entry has no param and follows the TeamSwitcher selection.
  const { slug: paramSlug } = useParams();
  const { isCommissioner, teams, activeTeam } = useAuth();
  const slug = paramSlug ?? activeTeam?.slug ?? "";
  const editable = isCommissioner || teams.some((t) => t.slug === slug);
  const nav = useNavigate();

  const [players, setPlayers] = useState<PP[]>([]);
  const [coaches, setCoaches] = useState<CoachRow[]>([]);
  const [teamName, setTeamName] = useState("");
  const [arr, setArr] = useState<Arrangement>(emptyArr());
  const [problems, setProblems] = useState<string[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    setProblems([]);
    setErr(null);
    if (!slug) { setPlayers([]); setArr(emptyArr()); setTeamName(""); setCoaches([]); return; }
    const { data: team } = await supabase.from("teams").select("id, name").eq("slug", slug).maybeSingle();
    if (!team) { setErr(`No team "${slug}".`); return; }
    setTeamName(team.name);
    const { data, error } = await supabase.from("player_public").select("*").eq("team_id", team.id);
    if (error) { setErr(error.message); return; }
    const ps = (data as PP[]) ?? [];
    setPlayers(ps);
    setArr(buildArr(ps));
    // Current coaches (authoritative source: the team_coaches view, not teams.coaches).
    const { data: cs } = await supabase
      .from("team_coaches")
      .select("role, coach_legacy_id, coach_name")
      .eq("team_slug", slug);
    setCoaches((cs as CoachRow[]) ?? []);
  }, [slug]);

  useEffect(() => { void load(); }, [load]);

  const byId = useMemo(() => new Map(players.map((p) => [p.legacy_id, p])), [players]);
  const teamStats = useMemo(() => computeTeamStats(arr, byId), [arr, byId]);
  const dsPlayer = (id: string) => {
    const p = byId.get(id);
    return p && { id: p.legacy_id, name: p.name, position: p.position, offense: p.offense, defense: p.defense, goalie: p.goalie_skill, injured: p.is_injured };
  };
  const cols = (group: "starters" | "bench") =>
    Object.fromEntries(POSITIONS.map((pos) => [pos, arr[group][pos].map(dsPlayer).filter(Boolean)]));

  // Client mirror of the server's domain.validate rules: surface lineup problems
  // live so the user gets immediate feedback instead of a rejected save. Only the
  // tier caps are hard blocks — an injured player in the starting lineup is now
  // allowed (it just needs a confirmation; see injuredStarters below).
  const issues = useMemo(() => {
    const out: string[] = [];
    for (const pos of POSITIONS) {
      const s = arr.starters[pos].length, sc = STARTER_CAPS[pos];
      if (s !== sc) out.push(`Starters ${pos}: ${s}/${sc} — ${s < sc ? `need ${sc - s} more` : `${s - sc} too many`}`);
      const b = arr.bench[pos].length, bc = BENCH_CAPS[pos];
      if (b !== bc) out.push(`Bench ${pos}: ${b}/${bc} — ${b < bc ? `need ${bc - b} more` : `${b - bc} too many`}`);
    }
    if (arr.reserves.length > RESERVE_MAX)
      out.push(`Reserves: ${arr.reserves.length}/${RESERVE_MAX} — ${arr.reserves.length - RESERVE_MAX} too many`);
    return out;
  }, [arr, byId]);

  // Injured players left in the starting lineup: allowed, but the manager must
  // confirm (an injured player contributes nothing in the sim).
  const injuredStarters = useMemo(() => {
    const names: string[] = [];
    for (const pos of POSITIONS)
      for (const id of arr.starters[pos]) {
        const p = byId.get(id);
        if (p?.is_injured) names.push(p.name);
      }
    return names;
  }, [arr, byId]);

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
  const onSlot = (pl: { id: string }, group: Group, position?: string) => {
    const p = byId.get(pl.id);
    if (!p) return;
    update((d) => {
      removeEverywhere(d, pl.id);
      // Any player may go to any position; fall back to the card position only
      // if a target wasn't supplied (e.g. legacy callers).
      if (group === "reserves") d.reserves.push(pl.id);
      else d[group][position ?? p.position].push(pl.id);
    });
  };

  // Save asks for confirmation first if an injured player is in the starting
  // lineup (allowed, but they contribute nothing in the sim).
  function requestSave() {
    if (injuredStarters.length > 0 && !confirming) {
      setConfirming(true);
      return;
    }
    void save();
  }

  async function save() {
    setProblems([]);
    setErr(null);
    setConfirming(false);
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

  if (!slug) {
    return (
      <section>
        <EmptyState
          title="No team selected"
          message="A commissioner assigns teams to managers. Once you own one, pick it from the team switcher to manage its roster."
        />
      </section>
    );
  }

  return (
    <section>
      <h2 style={{ marginBottom: 8 }}>{teamName}{editable ? "" : <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)", marginLeft: 10 }}>read-only</span>}</h2>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
        {coaches.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "var(--text-sm)", color: "var(--muted)" }}>
            {ROLE_ORDER.map((role) => {
              const c = coaches.find((x) => x.role === role);
              if (!c) return null;
              return (
                <div key={role}>
                  <span style={{ fontWeight: "var(--weight-bold)" }}>{ROLE_LABEL[role]}:</span>{" "}
                  <a
                    href={`#/coaches/${c.coach_legacy_id}`}
                    onClick={(e) => { e.preventDefault(); nav(`/coaches/${c.coach_legacy_id}`); }}
                    className="nha-navlink"
                    style={{ color: "var(--muted)", textDecoration: "none" }}
                  >
                    {c.coach_name}
                  </a>
                </div>
              );
            })}
          </div>
        ) : <div />}
        {players.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <TeamStatBox kind="offense" label="OFFENSE" value={teamStats.offense} />
            <TeamStatBox kind="defense" label="DEFENSE" value={teamStats.defense} />
          </div>
        )}
      </div>
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}
      {problems.length > 0 && <Alert tone="error" title="Invalid lineup" items={problems} style={{ marginBottom: 14 }} />}

      <h3 style={{ margin: "16px 0 8px" }}>Starters</h3>
      <RosterColumns byPosition={cols("starters")} caps={STARTER_CAPS} editable={editable} slot="starters"
        onMove={onMove} onSlot={onSlot}
        onPlayerClick={(p: { id: string }) => nav(`/players/${p.id}`)} />

      <h3 style={{ margin: "20px 0 8px" }}>Bench</h3>
      <RosterColumns byPosition={cols("bench")} caps={BENCH_CAPS} editable={editable} slot="bench"
        onMove={onMove} onSlot={onSlot}
        onPlayerClick={(p: { id: string }) => nav(`/players/${p.id}`)} />

      <h3 style={{ margin: "20px 0 8px" }}>Reserves</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, maxWidth: 520 }}>
        {arr.reserves.length === 0 && <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>None.</span>}
        {arr.reserves.map((id, idx) => {
          const p = dsPlayer(id);
          return p && <PlayerRow key={id} player={p} editable={editable} slot="reserves"
            canMoveUp={idx > 0} canMoveDown={idx < arr.reserves.length - 1}
            onMoveUp={() => onMove(p, -1)} onMoveDown={() => onMove(p, 1)} onSlot={(g: Group, pos?: string) => onSlot(p, g, pos)}
            onClick={() => nav(`/players/${id}`)} />;
        })}
      </div>

      {editable && confirming && (
        <Alert tone="warning" title="Injured player in starting lineup" style={{ marginTop: 16 }}>
          {injuredStarters.length === 1
            ? `${injuredStarters[0]} is injured and will contribute nothing this chunk.`
            : `${injuredStarters.join(", ")} are injured and will contribute nothing this chunk.`}{" "}
          Start them anyway?
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <Button variant="primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save anyway"}
            </Button>
            <Button onClick={() => setConfirming(false)} disabled={saving}>Cancel</Button>
          </div>
        </Alert>
      )}

      {editable && (
        <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
          <Button variant="primary" onClick={requestSave} disabled={saving || confirming || issues.length > 0}
            title={issues.length > 0 ? "Fill each position to its limit before saving" : undefined}>
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
