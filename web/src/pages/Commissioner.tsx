import { useCallback, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { TradeRow, EmptyState, Alert, Button, Toast } from "../ds";

interface TeamLite { id: string; name: string; }
interface TradeT { id: string; from_team_id: string; to_team_id: string; status: string; internal: boolean; }

export default function Commissioner() {
  const [teams, setTeams] = useState<TeamLite[]>([]);
  const [queue, setQueue] = useState<TradeT[]>([]);
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
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function act(path: string, ok: string) {
    setErr(null);
    try {
      await apiFetch(path, { method: "POST" });
      setToast(ok);
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");
    }
  }

  const queueClear = queue.length === 0;

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
      <Alert tone="info" style={{ marginBottom: 12 }}>
        Period simulation runs from the schedule, which isn't persisted yet — these controls are coming soon.
        {!queueClear && " The trade queue must also be cleared before a period can run."}
      </Alert>
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="primary" disabled>Run next period</Button>
        <Button disabled>Advance season</Button>
      </div>

      {toast && (
        <div style={{ position: "fixed", right: 20, bottom: 20, zIndex: 80 }}>
          <Toast tone="success" title={toast} onClose={() => setToast(null)} />
        </div>
      )}
    </section>
  );
}
