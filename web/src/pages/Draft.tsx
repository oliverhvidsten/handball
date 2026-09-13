import { useMemo, useState } from "react";
import { apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import {
  Alert, Button, DataTable, DraftOrderRow, EmptyState, Input, Select, StatCard,
  StatChip, Tag, Toast,
} from "../ds";
import { abbrev } from "../hooks";
import {
  DRAFT_PHASE, formatTimeLeft, problemsOf, rookieDeal, sortBoard,
  useDraftState, type DraftPick, type DraftProspect, type SortKey,
} from "../lib/draft";

const money = (m: number) => `$${m}M`;
const POS_TONE: Record<string, string> = {
  Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple",
};
const SORTS: { value: SortKey; label: string }[] = [
  { value: "rating", label: "Best available" },
  { value: "offense", label: "Offense" },
  { value: "defense", label: "Defense" },
  { value: "goalie_skill", label: "Goalie" },
  { value: "age", label: "Youngest" },
  { value: "ord", label: "Uploaded order" },
];
const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"];

// Under this, the turn is close enough to expiring to say so in a warning colour.
// Matches the AuctionBoard's treatment of the free-agency clock.
const CLOCK_WARNING_SECONDS = 2 * 3600;

/** One row of the order. `overall` is null until the lottery numbers round 1, so a
 *  pre-lottery board shows the slot as undecided rather than inventing a number. */
function OrderRow({ pick, current }: { pick: DraftPick; current: boolean }) {
  const traded = pick.original !== pick.team;
  const deal = pick.used
    ? { term: pick.term ?? 0, value: pick.value ?? 0 }
    : rookieDeal(pick.overall);
  return (
    <div style={{ position: "relative" }}>
      <DraftOrderRow
        pick={{
          overall: pick.overall ?? "—",
          round: pick.round,
          inRound: pick.overall == null ? "??" : pick.overall,
          team: pick.team_name,
          abbr: abbrev(pick.team_name),
          viaTeam: traded ? pick.original_name : null,
          current,
          player: pick.player_name
            ? { name: pick.player_name, position: pick.player_position }
            : null,
        }}
      />
      <div style={{ position: "absolute", right: 12, top: 10, display: "flex",
                    gap: 6, alignItems: "center" }}>
        {pick.protection_top_n != null && (
          <Tag tone={pick.protection_outcome === "reverted" ? "purple" : "neutral"} size="sm">
            {pick.protection_outcome === "reverted"
              ? `top ${pick.protection_top_n} — reverted`
              : pick.protection_outcome === "conveyed"
                ? `top ${pick.protection_top_n} — conveyed`
                : `top ${pick.protection_top_n} protected`}
          </Tag>
        )}
        {pick.auto_pick && <Tag tone="amber" size="sm">auto</Tag>}
        {deal && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)",
                         color: "var(--muted)" }}>
            {deal.term}yr / {money(deal.value)}
          </span>
        )}
      </div>
    </div>
  );
}

export default function Draft() {
  const { teams } = useAuth();
  const { draft, refresh, error } = useDraftState();

  const [sort, setSort] = useState<SortKey>("rating");
  const [pos, setPos] = useState("all");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  const clock = draft?.on_the_clock ?? null;
  // Whose turn it is is a fact the server already decided; the page only has to
  // work out whether that team is one of MINE.
  const myTurn = !!clock && teams.some((t) => t.slug === clock.team);
  const canPick = myTurn || (!!draft?.is_commissioner && !!clock);

  const board = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = (draft?.board ?? []).filter((p) => {
      if (q && !p.name.toLowerCase().includes(q)) return false;
      if (pos !== "all" && p.position !== pos) return false;
      return true;
    });
    return sortBoard(filtered, sort);
  }, [draft?.board, query, pos, sort]);

  const made = (draft?.order ?? []).filter((p) => p.used);
  const timeLeft = formatTimeLeft(clock?.turn_seconds_left);
  const urgent = clock?.turn_seconds_left != null
    && clock.turn_seconds_left < CLOCK_WARNING_SECONDS;

  async function pick(prospect: DraftProspect) {
    if (!canPick) return;
    setBusy(true);
    setProblems([]);
    try {
      const res = await apiFetch<{ player_name: string; overall: number; term: number;
                                   value: number; placed: boolean; complete: boolean }>(
        "/draft/pick",
        { method: "POST", body: JSON.stringify({ prospect_id: prospect.id, force: !myTurn }) },
      );
      setToast(
        `${res.player_name} goes ${res.overall} overall — ${res.term}yr / ${money(res.value)}`
        + (res.complete ? ". That's the draft; undrafted prospects are now free agents."
          : res.placed ? " — lineup re-derived; adjust it on the Roster page."
            : " — the roster is still incomplete, so they're unplaced.")
      );
      await refresh();
    } catch (e) {
      setProblems(problemsOf(e));
    } finally {
      setBusy(false);
    }
  }

  const columns = [
    { key: "name", header: "Prospect", render: (r: DraftProspect) => r.name },
    {
      key: "position", header: "Pos",
      render: (r: DraftProspect) => (
        <Tag tone={POS_TONE[r.position] || "neutral"} size="sm">{r.position}</Tag>
      ),
    },
    { key: "age", header: "Age", numeric: true, render: (r: DraftProspect) => r.age ?? "—" },
    {
      key: "stats", header: "Stats",
      render: (r: DraftProspect) =>
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
      key: "rating", header: "Rating", numeric: true,
      render: (r: DraftProspect) => r.rating.toFixed(1),
    },
    {
      key: "act", header: "",
      render: (r: DraftProspect) =>
        canPick ? (
          <Button variant={myTurn ? "primary" : undefined} disabled={busy}
                  onClick={() => void pick(r)}>
            {myTurn ? "Draft" : "Force pick"}
          </Button>
        ) : null,
    },
  ];

  if (error && !draft) {
    return (
      <section>
        <h2 style={{ marginBottom: 16 }}>Draft</h2>
        <Alert tone="error">{error}</Alert>
      </section>
    );
  }

  if (!draft || draft.status == null) {
    return (
      <section>
        <h2 style={{ marginBottom: 16 }}>Draft</h2>
        <EmptyState
          title="No draft yet"
        />
      </section>
    );
  }

  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14,
                    flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>{draft.season} Draft</h2>
        <Tag tone={draft.status === "open" ? "green" : draft.status === "complete" ? "neutral" : "amber"}>
          {DRAFT_PHASE[draft.status]}
        </Tag>
      </div>

      {/* Turns the poll itself expired. Saying so matters: otherwise a pick nobody
          made simply appears in the order. */}
      {draft.swept.length > 0 && (
        <Alert tone="warning" style={{ marginBottom: 12 }}
               items={draft.swept.map((s) =>
                 `${s.team_name}'s turn expired at pick ${s.overall} — the clock took ${s.player_name}.`)} />
      )}

      {problems.length > 0 && (
        <Alert tone="error" items={problems} style={{ marginBottom: 12 }} />
      )}

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <StatCard label="Picks made" value={`${draft.picks_made} / ${draft.picks}`} />
        {clock && (
          <StatCard label={`On the clock · pick ${clock.overall}`} value={clock.team_name}
                    hint={timeLeft ?? undefined} />
        )}
        <StatCard label="Board" value={draft.board.length} hint="prospects available" />
        {draft.auto_picks > 0 && (
          <StatCard label="Auto-picks" value={draft.auto_picks}
                    hint={`the clock ran out (${draft.turn_limit_hours}h per turn)`} />
        )}
      </div>

      {clock && (
        <Alert tone={myTurn ? "success" : urgent ? "warning" : "info"} style={{ marginBottom: 14 }}>
          {myTurn ? (
            <><strong>You're on the clock</strong> with pick {clock.overall}
              {clock.original_name !== clock.team_name && <> (via {clock.original_name})</>}.
              {" "}Pick a player from the board below{timeLeft ? ` — ${timeLeft}` : ""}. If the
              clock runs out the league takes the best available for you.</>
          ) : (
            <><strong>{clock.team_name}</strong> is on the clock with pick {clock.overall}
              {timeLeft ? ` — ${timeLeft}` : ""}. Each turn lasts {draft.turn_limit_hours} hours;
              after that the best available is taken automatically.</>
          )}
        </Alert>
      )}

      {draft.status === "pending" && (
        <Alert tone="info" style={{ marginBottom: 14 }}>
          The order is seeded but the first {draft.lottery?.pool_size ?? 16} picks are
          undecided — the commissioner has yet to draw the lottery. Picks {(draft.lottery?.pool_size ?? 16) + 1}
          {" "}onwards are set by how far each playoff team went.
        </Alert>
      )}

      {/* -- the lottery, once it has been drawn ----------------------------- */}
      {draft.lottery && draft.lottery.results.length > 0 && (
        <details style={{ marginBottom: 16 }}>
          <summary style={{ cursor: "pointer", color: "var(--muted)",
                            fontSize: "var(--text-sm)" }}>
            Lottery results (seed {draft.lottery.seed})
          </summary>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {draft.lottery.results.map((r) => (
              <span key={r.slot} style={{ fontSize: "var(--text-xs)", padding: "3px 8px",
                                          border: "1px solid var(--line)",
                                          borderRadius: "var(--radius-sm)" }}>
                <strong>{r.slot}</strong> {r.team_name}
              </span>
            ))}
          </div>
        </details>
      )}

      <div style={{ display: "grid", gap: 20, gridTemplateColumns: "minmax(0, 1fr)" }}>
        {/* -- the board ---------------------------------------------------- */}
        {draft.status === "open" && (
          <div>
            <h3 style={{ margin: "0 0 8px" }}>Board</h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              <Input placeholder="Search prospects…" value={query}
                     onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)} />
              <Select value={pos}
                      onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setPos(e.target.value)}
                      options={[{ value: "all", label: "All positions" },
                                ...POSITIONS.map((p) => ({ value: p, label: p }))]} />
              <Select value={sort}
                      onChange={(e: React.ChangeEvent<HTMLSelectElement>) =>
                        setSort(e.target.value as SortKey)}
                      options={SORTS} />
            </div>
            {board.length === 0 ? (
              <EmptyState compact title="Nobody left" message="Every prospect matching that filter is off the board." />
            ) : (
              <DataTable columns={columns} rows={board} getRowKey={(r: DraftProspect) => r.id} />
            )}
          </div>
        )}

        {/* -- the order ---------------------------------------------------- */}
        <div>
          <h3 style={{ margin: "0 0 8px" }}>
            Order{made.length > 0 && <span style={{ color: "var(--muted)",
                                                    fontWeight: "normal" }}> · {made.length} made</span>}
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {draft.order.map((p) => (
              <OrderRow key={p.id} pick={p}
                        current={p.overall != null && p.overall === draft.current_overall} />
            ))}
          </div>
        </div>
      </div>

      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
    </section>
  );
}
