import { useCallback, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import { TradeRow, TradePicker, EmptyState, Alert, Toast } from "../ds";

interface TeamLite { id: string; slug: string; name: string; }
interface TradeT {
  id: string; from_team_id: string; to_team_id: string; status: string; internal: boolean; created_at: string;
}
interface PlayerOpt { id: string; name: string; position: string; }

export default function Trades() {
  const { teams, activeTeam, isCommissioner, session } = useAuth();
  const [allTeams, setAllTeams] = useState<TeamLite[]>([]);
  const [trades, setTrades] = useState<TradeT[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // propose form state
  const [toSlug, setToSlug] = useState("");
  const [mine, setMine] = useState<PlayerOpt[]>([]);
  const [theirs, setTheirs] = useState<PlayerOpt[]>([]);
  const [out, setOut] = useState<string[]>([]);
  const [inn, setInn] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const ownedIds = new Set(teams.map((t) => t.id));
  const teamById = (id: string) => allTeams.find((t) => t.id === id);

  const load = useCallback(async () => {
    const { data: ts } = await supabase.from("teams").select("id, slug, name").order("name");
    setAllTeams((ts as TeamLite[]) ?? []);
    const { data: tr } = await supabase
      .from("trades")
      .select("id, from_team_id, to_team_id, status, internal, created_at")
      .order("created_at", { ascending: false });
    setTrades((tr as TradeT[]) ?? []);
  }, []);

  useEffect(() => { void load(); }, [load, session]);

  // load player options for the propose form
  const loadPlayers = useCallback(async (teamId: string | undefined, set: (p: PlayerOpt[]) => void) => {
    if (!teamId) { set([]); return; }
    const { data } = await supabase.from("player_public").select("legacy_id, name, position").eq("team_id", teamId);
    set((data ?? []).map((p: any) => ({ id: p.legacy_id, name: p.name, position: p.position })));
  }, []);

  useEffect(() => { void loadPlayers(activeTeam?.id, setMine); }, [activeTeam, loadPlayers]);
  useEffect(() => {
    const t = allTeams.find((x) => x.slug === toSlug);
    void loadPlayers(t?.id, setTheirs);
  }, [toSlug, allTeams, loadPlayers]);

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

  async function propose() {
    if (!activeTeam || !toSlug) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await apiFetch<{ internal: boolean }>("/trades", {
        method: "POST",
        body: JSON.stringify({ from_team: activeTeam.slug, to_team: toSlug, players_out: out, players_in: inn }),
      });
      setToast(res.internal ? "Internal trade created (awaiting commissioner)." : "Trade proposed.");
      setToSlug(""); setOut([]); setInn([]);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "propose failed");
    } finally {
      setBusy(false);
    }
  }

  function actionsFor(t: TradeT) {
    const acts: { label: string; variant?: string; onClick: () => void }[] = [];
    const iOwnTo = ownedIds.has(t.to_team_id) || isCommissioner;
    const iOwnFrom = ownedIds.has(t.from_team_id) || isCommissioner;
    if (t.status === "proposed" && iOwnTo) {
      acts.push({ label: "Accept", variant: "primary", onClick: () => act(`/trades/${t.id}/accept`, "Accepted.") });
      acts.push({ label: "Reject", variant: "danger", onClick: () => act(`/trades/${t.id}/reject`, "Rejected.") });
    }
    if (t.status === "accepted" && isCommissioner) {
      acts.push({ label: "Approve", variant: "primary", onClick: () => act(`/trades/${t.id}/approve`, "Committed.") });
    }
    if ((t.status === "proposed" || t.status === "accepted") && iOwnFrom) {
      acts.push({ label: "Cancel", onClick: () => act(`/trades/${t.id}/cancel`, "Cancelled.") });
    }
    return acts;
  }

  const teamOptions = allTeams
    .filter((t) => t.slug !== activeTeam?.slug)
    .map((t) => ({ value: t.slug, label: ownedIds.has(t.id) ? `${t.name} (your team — internal)` : t.name }));

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Trades</h2>
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}

      {activeTeam ? (
        <TradePicker
          myTeam={activeTeam.name}
          teamOptions={teamOptions}
          toTeam={toSlug}
          onToTeam={setToSlug}
          myPlayers={mine}
          theirPlayers={theirs}
          out={out}
          inn={inn}
          onToggleOut={(id: string) => setOut((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
          onToggleIn={(id: string) => setInn((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
          onPropose={propose}
          busy={busy}
          style={{ marginBottom: 24 }}
        />
      ) : (
        <Alert tone="info" style={{ marginBottom: 24 }}>You don't own a team, so you can't propose trades.</Alert>
      )}

      <h3 style={{ marginBottom: 10 }}>Your trades</h3>
      {trades.length === 0 ? (
        <EmptyState compact title="No trades" message="Trades you're a party to show up here." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {trades.map((t) => (
            <TradeRow
              key={t.id}
              trade={{
                fromTeam: teamById(t.from_team_id)?.name ?? "?",
                toTeam: teamById(t.to_team_id)?.name ?? "?",
                status: t.status,
                internal: t.internal,
              }}
              actions={actionsFor(t)}
            />
          ))}
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
