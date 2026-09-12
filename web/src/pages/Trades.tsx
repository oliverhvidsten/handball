import { useCallback, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import { TradeRow, TradePicker, EmptyState, Alert, Toast, Tag, Input } from "../ds";
import { useContractWindows } from "../lib/contracts";

interface TeamLite { id: string; slug: string; name: string; }
interface TradeT {
  id: string; from_team_id: string; to_team_id: string; status: string; internal: boolean; created_at: string;
}
interface PlayerOpt { id: string; name: string; position: string; }
interface PickOpt {
  id: string; season: number; round: number; originalTeam: string;
  // A protection already agreed on a PAST trade for this pick (handball/
  // trade_service.py). null on a pick nobody has ever protected.
  protectionTopN: number | null;
  protectionOutcome: string | null;
}

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
  const [myPicks, setMyPicks] = useState<PickOpt[]>([]);
  const [theirPicks, setTheirPicks] = useState<PickOpt[]>([]);
  const [picksOut, setPicksOut] = useState<string[]>([]);
  const [picksIn, setPicksIn] = useState<string[]>([]);
  // Round-1-only protection a proposer is asking for THIS trade (pick id -> "top N"
  // as typed; "" means unprotected). Existing protections on a pick already
  // traded are shown separately, below -- they aren't this trade's terms until
  // restated here (trade_service resets protection_top_n to whatever this trade
  // says, null included).
  const [protections, setProtections] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // The trade deadline (handball/extensions.py, via GET /contracts/windows). Once it
  // has passed, nothing may be proposed or accepted until the next league year --
  // approvals still go through, so the commissioner can clear the queue.
  const windows = useContractWindows();
  const deadlinePassed = windows?.trade_deadline_passed ?? false;

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

  // load draft-pick options for the propose form -- undrafted (used=false) rows a
  // team currently holds, own or acquired (see handball/offseason.py's rolling
  // 10-year future-pick window).
  const loadPicks = useCallback(async (teamId: string | undefined, set: (p: PickOpt[]) => void) => {
    if (!teamId) { set([]); return; }
    const { data } = await supabase
      .from("draft_picks")
      .select("id, season, round, protection_top_n, protection_outcome, original:original_team_id(name)")
      .eq("holder_team_id", teamId)
      .eq("used", false)
      .order("season", { ascending: true })
      .order("round", { ascending: true });
    set(((data as any[]) ?? []).map((p) => ({
      id: p.id, season: p.season, round: p.round, originalTeam: p.original?.name ?? "?",
      protectionTopN: p.protection_top_n, protectionOutcome: p.protection_outcome,
    })));
  }, []);

  useEffect(() => { void loadPicks(activeTeam?.id, setMyPicks); }, [activeTeam, loadPicks]);
  useEffect(() => {
    const t = allTeams.find((x) => x.slug === toSlug);
    void loadPicks(t?.id, setTheirPicks);
  }, [toSlug, allTeams, loadPicks]);

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

  // A pick id becomes {pick_id, protection_top_n} when the proposer typed a
  // protection for it; otherwise it stays the plain id trade_service always
  // accepted. Only round-1 picks show the input at all (see the picker below).
  function pickAsset(id: string): string | { pick_id: string; protection_top_n: number } {
    const raw = protections[id];
    if (raw == null || raw.trim() === "") return id;
    return { pick_id: id, protection_top_n: Number(raw) };
  }

  async function propose() {
    if (!activeTeam || !toSlug || deadlinePassed) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await apiFetch<{ internal: boolean }>("/trades", {
        method: "POST",
        body: JSON.stringify({
          from_team: activeTeam.slug, to_team: toSlug,
          players_out: out, players_in: inn,
          picks_out: picksOut.map(pickAsset), picks_in: picksIn.map(pickAsset),
        }),
      });
      setToast(res.internal ? "Internal trade created (awaiting commissioner)." : "Trade proposed.");
      setToSlug(""); setOut([]); setInn([]); setPicksOut([]); setPicksIn([]); setProtections({});
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
    if (t.status === "proposed" && iOwnTo && !deadlinePassed) {
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

  // Every pick either side has offered, by id -- for the protection input (which
  // needs a pick's round) and for the "existing protections" readout below.
  const allPicksById = new Map([...myPicks, ...theirPicks].map((p) => [p.id, p]));
  const selectedRound1Picks = [...picksOut, ...picksIn]
    .map((id) => allPicksById.get(id))
    .filter((p): p is PickOpt => p != null && p.round === 1);
  const alreadyProtected = [...myPicks, ...theirPicks].filter((p) => p.protectionTopN != null);
  const subHead: React.CSSProperties = {
    margin: "0 0 8px", fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)",
    textTransform: "uppercase", letterSpacing: "var(--tracking-wide)", color: "var(--muted)",
  };

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Trades</h2>
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}

      {deadlinePassed && (
        <Alert tone="warning" title="The trade deadline has passed" style={{ marginBottom: 14 }}>
          No trade can be proposed or accepted until the commissioner advances the season.
          Trades already accepted can still be approved.
        </Alert>
      )}

            {deadlinePassed ? null : activeTeam ? (
        <>
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
            myPicks={myPicks}
            theirPicks={theirPicks}
            picksOut={picksOut}
            picksIn={picksIn}
            onTogglePickOut={(id: string) => setPicksOut((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
            onTogglePickIn={(id: string) => setPicksIn((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
            onPropose={propose}
            busy={busy}
            style={{ marginBottom: 14 }}
          />

          {/* A protection is this trade's own term, agreed fresh (handball/
              trade_service.py resets protection_top_n to whatever's typed here,
              null included) -- so the input only appears for round-1 picks
              actually in the trade, never as an edit to a pick sitting untouched. */}
          {selectedRound1Picks.length > 0 && (
            <div style={{
              background: "var(--surface-card)", border: "1px solid var(--line)",
              borderRadius: "var(--radius-lg)", padding: 14, marginBottom: 14,
            }}>
              <h5 style={subHead}>Pick protections (round 1 only)</h5>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {selectedRound1Picks.map((p) => (
                  <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ flex: 1, fontSize: "var(--text-sm)" }}>{p.originalTeam} {p.season} Round {p.round}</span>
                    <Input
                      size="sm"
                      type="number"
                      min={1}
                      max={32}
                      placeholder="Unprotected"
                      value={protections[p.id] ?? ""}
                      onChange={(e: any) => setProtections((s) => ({ ...s, [p.id]: e.target.value }))}
                      style={{ width: 130 }}
                    />
                    <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>top N protected</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {alreadyProtected.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <h5 style={subHead}>Existing protections</h5>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {alreadyProtected.map((p) => (
                  <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "var(--text-sm)" }}>
                    <Tag tone="amber" size="sm">Top {p.protectionTopN}</Tag>
                    <span>{p.originalTeam} {p.season} Round {p.round}</span>
                    {p.protectionOutcome && <span style={{ color: "var(--muted)" }}>· {p.protectionOutcome}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
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
