import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "../../lib/api";
import { byClassYear, useHallOfFame, type Inductee } from "../../lib/hallOfFame";
import { Button, Input, Alert, EmptyState, Tag } from "../../ds";

/**
 * Commissioner controls for the Hall of Fame: pick a retired player (this season's
 * retirees first, then a search over all of them), write a citation, induct — and
 * rescind from the list of existing inductees.
 */
export interface HallOfFamePanelProps {
  season: number;
  onToast: (msg: string) => void;
}

interface Retiree { legacy_id: string; name: string; position: string; age: number | null; retired_season: number | null; }

const POS_TONE: Record<string, any> = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

export default function HallOfFamePanel({ season, onToast }: HallOfFamePanelProps) {
  const [retirees, setRetirees] = useState<Retiree[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [citation, setCitation] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const { hof, refresh } = useHallOfFame(refreshSignal);

  const loadRetirees = useCallback(async () => {
    try {
      const params = new URLSearchParams({ season: String(season) });
      if (q.trim()) params.set("q", q.trim());
      const r = await apiFetch<{ retirees: Retiree[] }>(`/hall-of-fame/eligible?${params}`, { method: "GET" });
      setRetirees(r.retirees);
    } catch {
      setRetirees([]);
    }
  }, [season, q]);

  useEffect(() => { void loadRetirees(); }, [loadRetirees]);

  async function induct() {
    if (!selected) return;
    setBusy(true);
    setErr(null);
    try {
      await apiFetch("/hall-of-fame", {
        method: "POST",
        body: JSON.stringify({ player_id: selected, season, citation: citation.trim() || null }),
      });
      onToast(`Inducted into the Class of ${season}.`);
      setSelected(null);
      setCitation("");
      setQ("");
      await Promise.all([loadRetirees(), refresh()]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "induction failed");
    } finally {
      setBusy(false);
    }
  }

  async function doRescind(legacyId: string) {
    setBusy(true);
    setErr(null);
    try {
      await apiFetch(`/hall-of-fame/${encodeURIComponent(legacyId)}`, { method: "DELETE" });
      onToast("Induction rescinded.");
      setRefreshSignal((n) => n + 1);
      await loadRetirees();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "rescind failed");
    } finally {
      setBusy(false);
    }
  }

  const thisSeason = retirees.filter((r) => r.retired_season === season);
  const others = retirees.filter((r) => r.retired_season !== season);
  const classes = byClassYear(hof?.inductees ?? []);

  return (
    <>
      <h3 style={{ margin: "28px 0 10px" }}>Hall of Fame</h3>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        Induct a retired player into the Class of {season}. Career totals are pulled from the
        record books automatically — only the class and the citation are yours to write.
      </p>
      {err && <Alert tone="error" style={{ marginBottom: 12 }}>{err}</Alert>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginBottom: 16 }}>
        <div>
          <h4 style={{ margin: "0 0 8px" }}>Retired this season ({thisSeason.length})</h4>
          <RetireeList items={thisSeason} selected={selected} onSelect={setSelected} empty="Nobody retired this season." />

          <h4 style={{ margin: "16px 0 8px" }}>Search all retirees</h4>
          <Input
            size="sm"
            placeholder="Search by name…"
            value={q}
            onChange={(e: any) => setQ(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <RetireeList items={others} selected={selected} onSelect={setSelected} empty={q ? "No match." : "No other retirees."} />
        </div>

        <div>
          <h4 style={{ margin: "0 0 8px" }}>Induct</h4>
          {selected ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <p style={{ margin: 0 }}>
                <strong>{retirees.find((r) => r.legacy_id === selected)?.name ?? selected}</strong> — Class of {season}
              </p>
              <Input
                label="Citation"
                placeholder="What earns this player the honor…"
                value={citation}
                onChange={(e: any) => setCitation(e.target.value)}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <Button variant="primary" disabled={busy} onClick={() => void induct()}>
                  {busy ? "Inducting…" : "Induct"}
                </Button>
                <Button disabled={busy} onClick={() => { setSelected(null); setCitation(""); }}>Cancel</Button>
              </div>
            </div>
          ) : (
            <p style={{ color: "var(--muted)" }}>Pick a retired player to induct them.</p>
          )}
        </div>
      </div>

      <h4 style={{ margin: "20px 0 8px" }}>Inducted</h4>
      {classes.length === 0 ? (
        <EmptyState compact title="Empty so far" message="Nobody has been inducted yet." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {classes.map(([year, members]) => (
            <div key={year}>
              <div style={{ fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)", color: "var(--muted)", marginBottom: 4 }}>
                Class of {year}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {members.map((m: Inductee) => (
                  <div
                    key={m.legacy_id}
                    style={{
                      display: "flex", alignItems: "center", gap: 10, padding: "6px 10px",
                      background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)",
                    }}
                  >
                    <span style={{ fontWeight: 600, flex: 1 }}>{m.name}</span>
                    <Tag tone={POS_TONE[m.position] || "neutral"} size="sm">{m.position}</Tag>
                    <Button size="sm" variant="danger" disabled={busy} onClick={() => void doRescind(m.legacy_id)}>
                      Rescind
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function RetireeList({
  items, selected, onSelect, empty,
}: { items: Retiree[]; selected: string | null; onSelect: (id: string) => void; empty: string }) {
  if (items.length === 0) {
    return <EmptyState compact title="" message={empty} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto" }}>
      {items.map((r) => (
        <label
          key={r.legacy_id}
          style={{
            display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", cursor: "pointer",
            background: selected === r.legacy_id ? "var(--surface-2)" : "var(--surface-card)",
            border: `1px solid ${selected === r.legacy_id ? "var(--action)" : "var(--line)"}`,
            borderRadius: "var(--radius-md)",
          }}
        >
          <input type="radio" name="hof-pick" checked={selected === r.legacy_id} onChange={() => onSelect(r.legacy_id)} />
          <span style={{ flex: 1 }}>{r.name}</span>
          <Tag tone={POS_TONE[r.position] || "neutral"} size="sm">{r.position}</Tag>
          {r.retired_season != null && <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>ret. {r.retired_season}</span>}
        </label>
      ))}
    </div>
  );
}
