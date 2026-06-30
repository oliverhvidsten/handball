import { useCallback, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { TradeRow, EmptyState, Alert, Button, Toast } from "../ds";

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
  run_error: string | null;
  run_stale: boolean;
  regular_season_complete: boolean;
}
interface Candidate { legacy_id: string; name: string; age: number; position: string; team_name: string | null; }

export default function Commissioner() {
  const [teams, setTeams] = useState<TeamLite[]>([]);
  const [queue, setQueue] = useState<TradeT[]>([]);
  const [season, setSeason] = useState<SeasonState | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
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
      await load();
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

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const queueClear = queue.length === 0;
  const scheduled = season?.schedule_generated ?? false;
  const seasonComplete = season != null && season.next_period > season.total_periods;
  const canRun =
    scheduled && queueClear && !seasonComplete && busy == null && !activelyRunning && !needsReset;
  const canAdvance = seasonComplete && queueClear && busy == null && !activelyRunning && !needsReset;

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
      {activelyRunning ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          Simulating period {season?.run_period ?? season?.next_period}… this can take a few minutes. Results appear automatically when it finishes — you can leave this tab open.
        </Alert>
      ) : needsReset ? (
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
      ) : seasonComplete ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          Every regular-season period has been played — see the Offseason section below to advance.
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
          ) : needsReset ? (
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

      {seasonComplete && (
        <>
          <h3 style={{ margin: "28px 0 10px" }}>Offseason</h3>
          <p style={{ color: "var(--muted)", marginTop: 0 }}>
            Review potential retirees, then advance to season {season!.season + 1}. Advancing assigns
            awards, seeds the draft order, ages every player, and opens the new season. This can't be undone.
          </p>

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
        </>
      )}

      {toast && (
        <div style={{ position: "fixed", right: 20, bottom: 20, zIndex: 80 }}>
          <Toast tone="success" title={toast} onClose={() => setToast(null)} />
        </div>
      )}
    </section>
  );
}
