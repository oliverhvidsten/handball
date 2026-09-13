import { useCallback, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { TradeRow, EmptyState, Alert, Button, Toast } from "../ds";
import { useFreeAgencyState } from "../lib/freeAgency";
import DraftPanel from "../components/commissioner/DraftPanel";
import VotingPanel from "../components/commissioner/VotingPanel";
import HallOfFamePanel from "../components/commissioner/HallOfFamePanel";

interface TeamLite { id: string; name: string; }
interface TradeT { id: string; from_team_id: string; to_team_id: string; status: string; internal: boolean; }
interface SeasonState {
  season: number;
  periods_run: number;
  next_period: number;
  total_periods: number;
  schedule_generated: boolean;
  queue_clear: boolean;
  run_status: "idle" | "running" | "done" | "error";
  run_period: number | null;
  run_kind: "period" | "playoff";
  run_error: string | null;
  run_stale: boolean;
  regular_season_complete: boolean;
  // Postseason cursor (handball/playoffs.py). One run slot serves both phases, so
  // run_kind says which one a 'running'/'error' status belongs to.
  playoffs_started: boolean;
  playoffs_complete: boolean;
  playoff_next_round: number | null;
  playoff_total_rounds: number;
  champion: string | null;
  // Season-start readiness (handball/season_readiness.py). The check list is a
  // registry that can grow, so render whatever the API sends rather than naming
  // individual checks here.
  season_ready: boolean;
  season_blockers: Blocker[];
  readiness_checks: { name: string; description: string }[];
  readiness_gates_next_period: boolean;
}
interface Blocker { check: string; subject: string; message: string; }
interface Candidate { legacy_id: string; name: string; age: number; position: string; team_name: string | null; }
// Bulk contract administration (handball/contract_admin.py). `expired` is the number
// that calls for the tool: counters at or below zero, which the rollover would
// release without a contract ever having run its course. `expiring_next_rollover`
// is a normal league fact (players on the last year of a real deal).
interface ContractAudit {
  rostered: number;
  expired: number;
  expiring_next_rollover: number;
  restart_runnable: boolean;
  restart_would_change?: number;
  restart_expiring_next_rollover?: number;
  restart_problems?: string[];
}
type BulkStrategy = "restart_expired" | "stagger_expired";

interface BulkPlan {
  strategy: BulkStrategy | "none";
  seed: number | null;
  changed: number;
  unchanged: number;
  expiring_before: number;
  expiring_next_rollover: number;
  expiry_cohorts: Record<string, number>;
  payrolls: { team: string; before: number; after: number }[];
}

export default function Commissioner() {
  const { fa, refresh: refreshFa } = useFreeAgencyState();
  const [teams, setTeams] = useState<TeamLite[]>([]);
  const [queue, setQueue] = useState<TradeT[]>([]);
  const [season, setSeason] = useState<SeasonState | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [contracts, setContracts] = useState<ContractAudit | null>(null);
  const [contractPlan, setContractPlan] = useState<BulkPlan | null>(null);
  // Stagger is the default: a league where every deal restarts on the same day
  // has a third of its players expiring together at the next rollover.
  const [contractStrategy, setContractStrategy] = useState<BulkStrategy>("stagger_expired");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const teamById = (id: string) => teams.find((t) => t.id === id);

  const load = useCallback(async () => {
    const { data: ts } = await supabase.from("teams").select("id, name");
    setTeams((ts as TeamLite[]) ?? []);
    // accepted trades await commissioner approval (commissioner RLS sees all)
    const { data: tr } = await supabase
      .from("trades")
      .select("id, from_team_id, to_team_id, status, internal")
      .eq("status", "accepted")
      .order("created_at", { ascending: true });
    setQueue((tr as TradeT[]) ?? []);
    let st: SeasonState | null = null;
    try {
      st = await apiFetch<SeasonState>("/season/state", { method: "GET" });
    } catch { /* not signed in / API down */ }
    setSeason(st);
    // Retirement candidates are only relevant once the season is over.
    if (st?.regular_season_complete) {
      try {
        const r = await apiFetch<{ candidates: Candidate[] }>("/retirement/candidates", { method: "GET" });
        setCandidates(r.candidates);
      } catch { setCandidates([]); }
    } else {
      setCandidates([]);
    }
    try {
      setContracts(await apiFetch<ContractAudit>("/contracts/audit", { method: "GET" }));
    } catch { setContracts(null); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // While a period actively simulates in the background, poll until it finishes.
  // A "running" row whose heartbeat went stale is a dead run, not a live one.
  const activelyRunning = season?.run_status === "running" && !season.run_stale;
  const needsReset =
    season?.run_status === "error" ||
    (season?.run_status === "running" && (season?.run_stale ?? false));
  useEffect(() => {
    if (!activelyRunning) return;
    const id = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(id);
  }, [activelyRunning, load]);

  async function act(path: string, ok: string) {
    setErr(null);
    setBusy(path);
    try {
      await apiFetch(path, { method: "POST" });
      setToast(ok);
      await Promise.all([load(), refreshFa()]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
    }
  }

  async function retireSelected() {
    if (selected.size === 0) return;
    setErr(null);
    setBusy("/retirement");
    try {
      await apiFetch("/retirement", { method: "POST", body: JSON.stringify({ player_ids: [...selected] }) });
      setToast(`Retired ${selected.size}.`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
    }
  }

  // The repair is two clicks on purpose: it rewrites every expired contract in the
  // league, so the plan is shown (dry run) before anything is written. A stagger
  // draws random counters, so the preview's seed is sent back on apply -- what was
  // shown is exactly what gets written.
  async function runContractRepair(dryRun: boolean) {
    const path = dryRun ? "/contracts/bulk:preview" : "/contracts/bulk";
    setErr(null);
    setBusy(path);
    try {
      const plan = await apiFetch<BulkPlan>("/contracts/bulk", {
        method: "POST",
        body: JSON.stringify({
          dry_run: dryRun,
          strategy: contractStrategy,
          seed: dryRun ? null : contractPlan?.seed ?? null,
        }),
      });
      if (dryRun) {
        setContractPlan(plan);
      } else {
        setContractPlan(null);
        setToast(
          contractStrategy === "stagger_expired"
            ? `Staggered ${plan.changed} contract(s).`
            : `Restarted ${plan.changed} contract(s).`,
        );
        await load();
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
    }
  }

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const queueClear = queue.length === 0;
  const scheduled = season?.schedule_generated ?? false;
  const seasonComplete = season != null && season.next_period > season.total_periods;
  // Readiness blocks starting a season (period 1) only; mid-season it's informational.
  const blockers = season?.season_blockers ?? [];
  const readinessBlocked = season?.readiness_gates_next_period ?? false;
  const canRun =
    scheduled && queueClear && !readinessBlocked && !seasonComplete &&
    busy == null && !activelyRunning && !needsReset;
  // The postseason stands between the last period and the rollover: the bracket is
  // seeded from the final standings, and advancing zeroes them.
  const playoffsDone = season?.playoffs_complete ?? false;
  const canAdvance =
    seasonComplete && playoffsDone && queueClear && busy == null &&
    !activelyRunning && !needsReset;
  // A failed run belongs to whichever phase started it; resetting it through the
  // other phase's endpoint would roll back the wrong thing.
  const failedRunIsPlayoff = needsReset && season?.run_kind === "playoff";
  const playoffRunning = activelyRunning && season?.run_kind === "playoff";
  const periodRunning = activelyRunning && !playoffRunning;
  const periodNeedsReset = needsReset && !failedRunIsPlayoff;

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>★ Commissioner</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>Approve trades and run the league.</p>
      {err && <Alert tone="error" style={{ margin: "12px 0" }}>{err}</Alert>}

      <h3 style={{ margin: "20px 0 10px" }}>Trades awaiting approval</h3>
      {queueClear ? (
        <EmptyState compact title="Queue clear" message="No accepted trades are waiting for approval." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {queue.map((t) => (
            <TradeRow
              key={t.id}
              trade={{
                fromTeam: teamById(t.from_team_id)?.name ?? "?",
                toTeam: teamById(t.to_team_id)?.name ?? "?",
                status: t.status,
                internal: t.internal,
              }}
              actions={[
                { label: "Approve", variant: "primary", onClick: () => act(`/trades/${t.id}/approve`, "Committed.") },
                { label: "Veto", variant: "danger", onClick: () => act(`/trades/${t.id}/cancel`, "Vetoed.") },
              ]}
            />
          ))}
        </div>
      )}

      <h3 style={{ margin: "28px 0 10px" }}>Run the league</h3>
      {season && (
        <p style={{ color: "var(--muted)", marginTop: 0 }}>
          Season {season.season} · {season.periods_run} of {season.total_periods} periods played
          {seasonComplete && " · regular season complete"}
        </p>
      )}
      {periodRunning ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          Simulating period {season?.run_period ?? season?.next_period}… this can take a few minutes. Results appear automatically when it finishes — you can leave this tab open.
        </Alert>
      ) : periodNeedsReset ? (
        <Alert tone="error" style={{ marginBottom: 12 }}>
          {season?.run_status === "error"
            ? `The last period run failed: ${season?.run_error ?? "unknown error"}.`
            : "The last run was interrupted (the server didn't finish it)."}{" "}
          Reset it to roll back any partial results, then run the period again.
        </Alert>
      ) : !scheduled ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          No schedule for this season yet. Generate one to begin — fixtures appear on the Schedule page's Upcoming tab.
        </Alert>
      ) : !queueClear ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          The trade approval queue must be cleared before a period can run.
        </Alert>
      ) : readinessBlocked ? (
        <Alert
          tone="error"
          title={`Season ${season?.season} can’t start until these are resolved`}
          items={blockers.map((b) => b.message)}
          style={{ marginBottom: 12 }}
        />
      ) : seasonComplete ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          Every regular-season period has been played — the Playoffs section below is next.
        </Alert>
      ) : null}
      {!seasonComplete && (
        <div style={{ display: "flex", gap: 8 }}>
          {!scheduled ? (
            <Button
              variant="primary"
              disabled={busy != null || activelyRunning}
              onClick={() => act("/schedule/generate", "Schedule generated.")}
            >
              {busy === "/schedule/generate" ? "Generating…" : "Generate schedule"}
            </Button>
          ) : periodNeedsReset ? (
            <Button
              variant="danger"
              disabled={busy != null}
              onClick={() => act("/periods/reset", "Run reset.")}
            >
              {busy === "/periods/reset" ? "Resetting…" : "Reset run"}
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={!canRun}
              onClick={() => act("/periods/run", "Period started.")}
            >
              {activelyRunning ? "Running…" : "Run next period"}
            </Button>
          )}
        </div>
      )}

      {/* Award and All-Star voting. The panel knows its own phase (a ballot opens
          itself once enough periods have run), so it sits with the run controls
          rather than inside a seasonComplete branch. */}
      {season && <VotingPanel season={season.season} onToast={setToast} />}

      {/* The draft: lottery, prospect class, the room, and forcing a pick. Mounted
          unconditionally because the draft's own phase decides whether there is
          anything to show -- it renders nothing at all until the rollover has seeded
          an order, then walks the commissioner through one step at a time. It cannot live in
          the Offseason block below: that block is for a season that has FINISHED, and
          a draft belongs to the one the rollover has just opened. */}
      {season && <DraftPanel season={season.season} onToast={setToast} />}

      {/* -- postseason ----------------------------------------------------
          One round per click. Managers set lineups between rounds, which is the
          reason the bracket isn't simulated in one go. The bracket itself renders
          on the Playoffs page; this is only the controls. */}
      {seasonComplete && (
        <>
          <h3 style={{ margin: "28px 0 10px" }}>Playoffs</h3>
          <p style={{ color: "var(--muted)", marginTop: 0 }}>
            {season?.playoffs_complete
              ? `${season.champion} won the ${season.season} championship.`
              : season?.playoffs_started
                ? `Round ${season.playoff_next_round} of ${season.playoff_total_rounds} is next — best-of-seven, so this can take several minutes.`
                : "Seed the bracket from the final standings: four division winners plus the best four remaining in each conference, then best-of-seven rounds."}
          </p>

          {playoffRunning ? (
            <Alert tone="info" style={{ marginBottom: 12 }}>
              Playing round {season?.run_period ?? season?.playoff_next_round}… each matchup is a
              best-of-seven, so this takes a while. Results appear on the Playoffs page
              automatically when it finishes — you can leave this tab open.
            </Alert>
          ) : failedRunIsPlayoff ? (
            <Alert tone="error" style={{ marginBottom: 12 }}>
              {season?.run_status === "error"
                ? `The last playoff round failed: ${season?.run_error ?? "unknown error"}.`
                : "The last playoff round was interrupted (the server didn't finish it)."}{" "}
              Resetting deletes that round's games and undoes its results so it can be run again.
              Injuries rolled during the round stand.
            </Alert>
          ) : !queueClear ? (
            <Alert tone="info" style={{ marginBottom: 12 }}>
              The trade approval queue must be cleared before the postseason can proceed.
            </Alert>
          ) : null}

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {failedRunIsPlayoff ? (
              <Button
                variant="danger"
                disabled={busy != null}
                onClick={() => act("/playoffs/reset", "Playoff round reset.")}
              >
                {busy === "/playoffs/reset" ? "Resetting…" : "Reset playoff round"}
              </Button>
            ) : !season?.playoffs_started ? (
              <Button
                variant="primary"
                disabled={busy != null || activelyRunning || !queueClear}
                onClick={() => act("/playoffs/start", "Bracket seeded.")}
              >
                {busy === "/playoffs/start" ? "Seeding…" : "Seed the bracket"}
              </Button>
            ) : !season.playoffs_complete ? (
              <Button
                variant="primary"
                disabled={busy != null || activelyRunning || !queueClear}
                onClick={() => act("/playoffs/rounds/run", `Round ${season.playoff_next_round} started.`)}
              >
                {playoffRunning ? "Playing…" : `Run round ${season.playoff_next_round}`}
              </Button>
            ) : null}
          </div>
        </>
      )}

      {seasonComplete && (
        <>
          <h3 style={{ margin: "28px 0 10px" }}>Offseason</h3>
          <p style={{ color: "var(--muted)", marginTop: 0 }}>
            Review potential retirees, then advance to season {season!.season + 1}. Advancing assigns
            awards, seeds the draft order, ages every player, and opens the new season. This can't be undone.
          </p>

          {/* The rollover zeroes the records the bracket was seeded from, so the
              postseason has to finish first. */}
          {!playoffsDone && (
            <Alert tone="info" style={{ marginBottom: 12 }}>
              Finish the playoffs before advancing — the rollover clears the standings the
              bracket is seeded from.
            </Alert>
          )}

          {/* Advancing is allowed while these are outstanding -- they only block the
              first period of the NEW season -- but surfacing them now is the point:
              the offseason is when teams have room to fix them. */}
          {blockers.length > 0 && (
            <Alert
              tone="warning"
              title={`Outstanding before season ${season!.season + 1} can start`}
              items={blockers.map((b) => b.message)}
              style={{ marginBottom: 12 }}
            />
          )}

          <h4 style={{ margin: "16px 0 8px" }}>Potential retirees ({candidates.length})</h4>
          {candidates.length === 0 ? (
            <EmptyState compact title="No candidates" message="No active players are over the retirement age." />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
              {candidates.map((c) => (
                <label
                  key={c.legacy_id}
                  className="nha-row"
                  style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", cursor: "pointer",
                    background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)",
                  }}
                >
                  <input type="checkbox" checked={selected.has(c.legacy_id)} onChange={() => toggle(c.legacy_id)} />
                  <span style={{ flex: 1, fontWeight: 600 }}>{c.name}</span>
                  <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>
                    {c.position} · age {c.age} · {c.team_name ?? "Free agent"}
                  </span>
                </label>
              ))}
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <Button
              disabled={busy != null || selected.size === 0}
              onClick={retireSelected}
            >
              {busy === "/retirement" ? "Retiring…" : `Retire selected (${selected.size})`}
            </Button>
            <Button
              variant="primary"
              disabled={!canAdvance}
              onClick={() => act("/season/advance", `Advanced to season ${season!.season + 1}.`)}
            >
              {busy === "/season/advance" ? "Advancing…" : `Advance to season ${season!.season + 1}`}
            </Button>
          </div>

          {/* The rest of the offseason: the Hall of Fame class, which belongs to the
              window between the Final and the rollover. The draft panel is NOT here --
              a draft belongs to the season the rollover has just opened, when
              seasonComplete is false again; see where it is mounted above. */}
          <HallOfFamePanel season={season!.season} onToast={setToast} />
        </>
      )}

      {/* -- contracts -----------------------------------------------------
          Rosters imported before contracts were modelled carry a years_remaining
          nobody ever set, and the rollover releases everyone at or below zero. This
          is the bulk repair (handball/contract_admin.py). Shown only while some
          counter has actually run out: a league where every player is simply on
          some year of a real deal renders nothing here, however many of those deals
          end this season -- that is free agency, not a fault. */}
      {contracts != null && (contracts.expired > 0 || contracts.restart_runnable === false) && (
        <>
          <h3 style={{ margin: "28px 0 10px" }}>Contracts</h3>
          <Alert
            tone={contracts.expired === contracts.rostered ? "error" : "warning"}
            title={`${contracts.expired} of ${contracts.rostered} rostered players have a contract counter that has run out`}
            style={{ marginBottom: 12 }}
          >
            {contracts.expired === contracts.rostered
              ? "That is every player in the league. Contracts imported before the contract model carry a countdown that was never set, and advancing the season would empty all 32 rosters."
              : `The rollover releases everyone at or below zero. Including players on the last year of a real deal, ${contracts.expiring_next_rollover} would reach free agency.`}
          </Alert>

          {contracts.restart_runnable === false && (
            <Alert
              tone="error"
              title="The bulk restart can't run as-is"
              items={contracts.restart_problems ?? []}
              style={{ marginBottom: 12 }}
            />
          )}

          {contracts.restart_runnable !== false && (
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12, fontSize: "var(--text-sm)" }}>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="radio"
                  name="contract-strategy"
                  checked={contractStrategy === "stagger_expired"}
                  onChange={() => { setContractStrategy("stagger_expired"); setContractPlan(null); }}
                />
                Stagger: each expired deal restarts 1 to N years from its end, N being its length
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="radio"
                  name="contract-strategy"
                  checked={contractStrategy === "restart_expired"}
                  onChange={() => { setContractStrategy("restart_expired"); setContractPlan(null); }}
                />
                Restart: every expired deal starts over at its full length
              </label>
            </div>
          )}

          {contractPlan && (
            <Alert tone="info" title="Preview — nothing has been written" style={{ marginBottom: 12 }}>
              {contractPlan.strategy === "stagger_expired" ? "Staggers" : "Restarts"} {contractPlan.changed} contract(s),
              keeping every term and salary as they are, and leaving {contractPlan.unchanged} untouched.
              Players expiring at the next rollover: {contractPlan.expiring_before} → {contractPlan.expiring_next_rollover}.
              {contractPlan.payrolls.length === 0
                ? " No team's payroll changes."
                : ` ${contractPlan.payrolls.length} team payroll(s) change.`}
              <div style={{ marginTop: 6, color: "var(--muted)" }}>
                Years remaining afterwards:{" "}
                {Object.entries(contractPlan.expiry_cohorts)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([y, n]) => `${y}y: ${n}`)
                  .join(" · ")}
                {contractPlan.seed != null && ` · draw #${contractPlan.seed}`}
              </div>
            </Alert>
          )}

          {contracts.restart_runnable !== false && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button
                disabled={busy != null}
                onClick={() => void runContractRepair(true)}
              >
                {busy === "/contracts/bulk:preview"
                  ? "Checking…"
                  : contractPlan ? "Preview again (new draw)" : "Preview repair"}
              </Button>
              {contractPlan && (
                <Button
                  variant="primary"
                  disabled={busy != null}
                  onClick={() => void runContractRepair(false)}
                >
                  {busy === "/contracts/bulk"
                    ? "Applying…"
                    : `${contractPlan.strategy === "stagger_expired" ? "Stagger" : "Restart"} ${contractPlan.changed} contract(s)`}
                </Button>
              )}
            </div>
          )}
        </>
      )}

      {/* -- free agency ---------------------------------------------------
          The phase controls only; the per-board actions (force a forfeit, award a
          deadlock) live on the Free Agents page, where the board is in front of you.
          One mutually-exclusive chain, like the run controls above. */}
      <h3 style={{ margin: "28px 0 10px" }}>Free agency</h3>
      {fa?.period == null ? (
        <>
          <Alert tone="info" style={{ marginBottom: 12 }}>
            No free-agency period is open. Open one after the rollover — teams offer contracts in a
            sealed round, then contested players go to auction. Season {season?.season} can't start
            while it's open.
          </Alert>
          <Button variant="primary" disabled={busy != null}
            onClick={() => act("/free-agency/periods", "Free agency opened.")}>
            {busy === "/free-agency/periods" ? "Opening…" : "Open free agency"}
          </Button>
        </>
      ) : fa.round?.status === "offers" ? (
        <>
          <Alert tone="info" style={{ marginBottom: 12 }}>
            Round {fa.round.round_number} is taking offers. They stay sealed until you close the
            round — then sole offers sign, restricted players open match windows, and anyone with
            two or more offers goes to bidding.
          </Alert>
          <Button variant="primary" disabled={busy != null}
            onClick={() => act("/free-agency/rounds/close", `Round ${fa.round!.round_number} closed.`)}>
            {busy === "/free-agency/rounds/close" ? "Closing…" : `Close round ${fa.round.round_number}`}
          </Button>
        </>
      ) : (fa.auctions ?? []).length > 0 ? (
        <Alert tone="warning" title={`${(fa.auctions ?? []).length} board(s) still live`}
          items={(fa.auctions ?? []).map((a) =>
            a.status === "awaiting_award"
              ? `${a.player_name} — deadlocked, needs your award`
              : a.status === "matching"
                ? `${a.player_name} — awaiting a match from ${a.rights_team ?? "the rights holder"}`
                : `${a.player_name} — on the clock: ${a.turn_team_name ?? "?"}`)}>
          Award deadlocks and force forfeits on the Free Agents page.
        </Alert>
      ) : fa.round && !fa.round.offers_count ? (
        <>
          <Alert tone="info" style={{ marginBottom: 12 }}>
            Round {fa.round.round_number} drew no offers — free agency is done. Everyone still
            unsigned goes back to the pool at the league minimum.
          </Alert>
          <Button variant="primary" disabled={busy != null}
            onClick={() => act("/free-agency/close", "Free agency closed.")}>
            {busy === "/free-agency/close" ? "Closing…" : "Close free agency"}
          </Button>
        </>
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary" disabled={busy != null}
            onClick={() => act("/free-agency/rounds", "Next round opened.")}>
            Open round {(fa.round?.round_number ?? 0) + 1}
          </Button>
          <Button disabled={busy != null}
            onClick={() => act("/free-agency/close", "Free agency closed.")}>
            Close free agency
          </Button>
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
