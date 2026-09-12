import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { ApiError, apiFetch } from "../lib/api";
import { useAuth } from "../auth";
import { DataTable, Input, Select, Tag, StatCard, StatChip, Button, Alert, EmptyState, Toast } from "../ds";
import { AuctionBoard } from "../components/AuctionBoard";
import {
  problemsOf, useFreeAgencyHistory, useFreeAgencyState,
  type FAHistoryBoard, type FATeam,
} from "../lib/freeAgency";

// The free-agent pool is just "not retired, no team" (see handball/offseason.py) --
// there is no separate pool table. rights_team_id is the team the player's last
// contract expired off: that team may re-sign them above the soft cap (Bird rights),
// which is why the ceiling below depends on who's asking.
interface FA {
  legacy_id: string;
  name: string;
  position: string;
  age: number;
  offense: number;
  defense: number;
  goalie_skill: number;
  contract_term: number;
  contract_value: number;
  rights_team_id: string | null;
}

// Mirrors handball/signing_service.py:team_cap_report.
interface CapReport {
  team_name: string;
  payroll: number;
  cap_room: number;
  over_cap: boolean;
  over_first_threshold: boolean;
  over_second_threshold: boolean;
  mid_level_exception: number;
  hard_cap_room: number;
  roster_size: number;
  max_roster: number;
  roster_spots: number;
  max_outside_offer: number;
  max_own_offer: number;
  limits: {
    salary_cap: number;
    first_luxury_threshold: number;
    second_luxury_threshold: number;
    hard_cap: number;
    max_contract_years: number;
    max_contract_value: number;
    min_contract_value: number;
  };
}

const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"] as const;
const POS_TONE: Record<string, string> = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };
const POS_OPTIONS = [{ value: "all", label: "All positions" }, ...POSITIONS.map((p) => ({ value: p, label: p }))];

const money = (m: number) => `$${m}M`;

// How each offer ended, in words. 'superseded' is the one worth spelling out: an
// offer is never edited, it is replaced, so the chain is the negotiation.
const OFFER_FATE: Record<string, { label: string; tone: string }> = {
  won: { label: "signed", tone: "green" },
  lost: { label: "outbid", tone: "neutral" },
  forfeited: { label: "walked away", tone: "neutral" },
  superseded: { label: "raised", tone: "blue" },
  withdrawn: { label: "withdrawn", tone: "neutral" },
  void: { label: "void", tone: "neutral" },
  open: { label: "standing", tone: "amber" },
};

/** One finished board: what it settled at, and every offer that was on it. */
function ClosedBoard({ board }: { board: FAHistoryBoard }) {
  const won = board.status === "resolved";
  return (
    <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)",
                  borderRadius: "var(--radius-md)", padding: "10px 14px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <strong>{board.player_name}</strong>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>{board.position}</span>
        {board.restricted && <Tag tone="purple" size="sm">Restricted</Tag>}
        <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>round {board.round_number}</span>
        <span style={{ marginLeft: "auto", fontSize: "var(--text-sm)" }}>
          {won ? (
            <>→ <strong>{board.winning_team_name}</strong>{" "}
              {board.signed_term}yr / {money(board.signed_value ?? 0)}</>
          ) : (
            <span style={{ color: "var(--muted)" }}>
              unsigned ({(board.outcome ?? "void").replace(/_/g, " ")})
            </span>
          )}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 6,
                    fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
        {board.offers.map((o) => {
          const fate = OFFER_FATE[o.status] ?? { label: o.status, tone: "gray" };
          return (
            <div key={o.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
                                     opacity: o.status === "won" ? 1 : 0.7 }}>
              <span style={{ minWidth: 120 }}>{o.team_name}</span>
              <span style={{ minWidth: 90 }}>{o.term}yr / {money(o.value)}</span>
              <Tag tone={fate.tone} size="sm">{fate.label}</Tag>
              {o.is_rfa_match && <Tag tone="purple" size="sm">match</Tag>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function FreeAgents() {
  const { activeTeam } = useAuth();
  const nav = useNavigate();

  const [pool, setPool] = useState<FA[]>([]);
  const [teamNames, setTeamNames] = useState<Map<string, string>>(new Map());
  const [cap, setCap] = useState<CapReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [pos, setPos] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The offseason market. `fa.period === null` means no market is open, and the page
  // renders exactly as it did before free agency existed: browse the pool, sign
  // anyone at the fixed 1yr/$0 deal.
  const { fa, refresh } = useFreeAgencyState();
  const [actingId, setActingId] = useState<string | null>(null);
  const [problems, setProblems] = useState<Record<string, string[]>>({});
  const [offerTerm, setOfferTerm] = useState(3);
  const [offerValue, setOfferValue] = useState(1);

  const marketOpen = !!fa?.period;
  const offersOpen = fa?.round?.status === "offers";
  // Page-local acting team -- never written back to the TeamSwitcher, because jumping
  // to a bid should not silently re-scope Roster and Dashboard.
  const acting: FATeam | null =
    fa?.teams.find((t) => t.id === actingId)
    ?? fa?.teams.find((t) => t.id === activeTeam?.id)
    ?? fa?.teams[0]
    ?? null;
  const myBoards = (fa?.teams ?? []).flatMap((t) =>
    t.action_required.map((a) => ({ ...a, team: t })));
  const boardById = (id: number) => (fa?.auctions ?? []).find((x) => x.id === id);
  // Refetch the record when the live board count changes -- i.e. exactly when a board
  // finishes and joins it.
  const history = useFreeAgencyHistory((fa?.auctions ?? []).length);

  const load = useCallback(async () => {
    setErr(null);
    const [{ data: fas, error: fe }, { data: ts, error: te }] = await Promise.all([
      supabase
        .from("player_public")
        .select("legacy_id, name, position, age, offense, defense, goalie_skill, contract_term, contract_value, rights_team_id")
        .is("team_id", null)
        .eq("retired", false)
        .order("name"),
      supabase.from("teams").select("id, name"),
    ]);
    if (fe || te) { setErr((fe ?? te)!.message); return; }
    setPool((fas as FA[]) ?? []);
    setTeamNames(new Map(((ts as { id: string; name: string }[]) ?? []).map((t) => [t.id, t.name])));
  }, []);

  const loadCap = useCallback(async () => {
    if (!activeTeam) { setCap(null); return; }
    try {
      setCap(await apiFetch<CapReport>(`/teams/${activeTeam.slug}/cap`, { method: "GET" }));
    } catch {
      setCap(null);          // non-fatal: the pool is still browsable, signing isn't
    }
  }, [activeTeam]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadCap(); }, [loadCap]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return pool.filter((p) => {
      if (q && !p.name.toLowerCase().includes(q)) return false;
      if (pos !== "all" && p.position !== pos) return false;
      return true;
    });
  }, [pool, query, pos]);

  const selected = useMemo(() => pool.find((p) => p.legacy_id === selectedId) ?? null, [pool, selectedId]);
  const rosterFull = !!cap && cap.roster_spots <= 0;

  async function sign() {
    if (!activeTeam || !selected) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await apiFetch<{ player_name: string; term: number; value: number; placed: boolean }>(
        "/signings",
        {
          method: "POST",
          body: JSON.stringify({ team: activeTeam.slug, player_id: selected.legacy_id }),
        }
      );
      setToast(
        `${res.player_name} signed for ${res.term} ${res.term === 1 ? "year" : "years"} at ${money(res.value)}/yr` +
          // The server re-derives a canonical lineup around the new player (same as a
          // trade), so say so -- a hand-tuned lineup will have been reshuffled.
          (res.placed
            ? " — lineup re-derived; adjust it on the Roster page."
            : " — the roster is still incomplete, so they're unplaced; fix the lineup on the Roster page.")
      );
      setSelectedId(null);
      await Promise.all([load(), loadCap()]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "signing failed");
    } finally {
      setBusy(false);
    }
  }

  /** Run a free-agency write and refresh. Errors are scoped to the card that failed --
   *  during bidding several cards are actionable at once, so one page-wide banner
   *  would leave you guessing which action was rejected. */
  async function run(path: string, body: Record<string, unknown>, scope: string) {
    setProblems((p) => ({ ...p, [scope]: [] }));
    setBusy(true);
    try {
      await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      await Promise.all([refresh(), load(), loadCap()]);
    } catch (e) {
      setProblems((p) => ({ ...p, [scope]: problemsOf(e) }));
    } finally {
      setBusy(false);
    }
  }

  const columns = [
    { key: "name", header: "Player", render: (r: FA) => r.name },
    {
      key: "position",
      header: "Pos",
      render: (r: FA) => <Tag tone={POS_TONE[r.position] || "neutral"} size="sm">{r.position}</Tag>,
    },
    { key: "age", header: "Age", render: (r: FA) => r.age },
    {
      key: "stats",
      header: "Stats",
      render: (r: FA) =>
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
      key: "last",
      header: "Last deal",
      render: (r: FA) =>
        r.contract_term > 0 ? (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
            {r.contract_term}yr / {money(r.contract_value)}
          </span>
        ) : (
          <span style={{ color: "var(--muted)" }}>—</span>
        ),
    },
    {
      key: "rights",
      header: "Rights",
      render: (r: FA) =>
        r.rights_team_id == null ? (
          <span style={{ color: "var(--muted)" }}>—</span>
        ) : r.rights_team_id === activeTeam?.id ? (
          <Tag tone="green" size="sm">Yours (Bird)</Tag>
        ) : (
          <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>
            {teamNames.get(r.rights_team_id) ?? "?"}
          </span>
        ),
    },
  ];

  // During an offer round the "Rights" column is joined by what YOU have offered.
  const offerColumn = {
    key: "your_offer",
    header: "Your offer",
    render: (r: FA) => {
      const mine = acting?.offers.find((o) => o.player_id === r.legacy_id);
      return mine ? (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", fontWeight: "var(--weight-semibold)" }}>
          {mine.term}yr / {money(mine.value)}
        </span>
      ) : (
        <span style={{ color: "var(--muted)" }}>—</span>
      );
    },
  };
  const tableColumns = offersOpen ? [...columns, offerColumn] : columns;

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Free Agents</h2>

      {/* -- the offseason market, when one is running ------------------------ */}
      {marketOpen && (
        <Alert tone="info" style={{ marginBottom: 14 }}>
          <strong>Free agency {fa!.period!.season} — round {fa!.round?.round_number}.</strong>{" "}
          {offersOpen
            ? "Offers are open and sealed: nobody sees anyone else's until the commissioner closes the round."
            : "Offers are closed. Contested players go to sequential bidding, worst initial offer first."}
        </Alert>
      )}

      {marketOpen && fa!.teams.length > 1 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
          {fa!.teams.map((t) => (
            <Button key={t.id} variant={t.id === acting?.id ? "primary" : undefined}
              onClick={() => setActingId(t.id)}>
              {t.name}
              {t.action_required.length > 0 ? ` · ${t.action_required.length} waiting` : ""}
            </Button>
          ))}
        </div>
      )}

      {marketOpen && acting && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 14,
                      fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", color: "var(--muted)" }}>
          <span>Acting as <strong>{acting.name}</strong></span>
          <span>{acting.offers.length} live offer{acting.offers.length === 1 ? "" : "s"}</span>
          <span>{money(acting.offers.reduce((s, o) => s + o.value, 0))} promised</span>
          <span>{acting.cap.roster_spots} roster spot{acting.cap.roster_spots === 1 ? "" : "s"}</span>
        </div>
      )}

      {myBoards.length > 0 && (
        <>
          <h3 style={{ margin: "18px 0 8px" }}>Waiting on you</h3>
          {myBoards.map((a) => {
            const board = boardById(a.auction_id);
            return board ? (
              <AuctionBoard key={a.auction_id} auction={board} acting={a.team} busy={busy}
                problems={problems[`auction:${a.auction_id}`]}
                onAct={(verb, body) =>
                  run(verb === "bid" ? `/free-agency/auctions/${a.auction_id}/bid`
                                     : `/free-agency/auctions/${a.auction_id}/${verb}`,
                      body, `auction:${a.auction_id}`)} />
            ) : null;
          })}
        </>
      )}

      {(fa?.auctions ?? []).some((x) => !myBoards.some((m) => m.auction_id === x.id)) && (
        <>
          <h3 style={{ margin: "18px 0 8px" }}>Live auctions</h3>
          {(fa?.auctions ?? [])
            .filter((x) => !myBoards.some((m) => m.auction_id === x.id))
            .map((x) => (
              <AuctionBoard key={x.id} auction={x} commissioner={fa?.is_commissioner} busy={busy}
                problems={problems[`auction:${x.id}`]}
                onAct={(verb, body) =>
                  run(`/free-agency/auctions/${x.id}/${verb}`, body, `auction:${x.id}`)} />
            ))}
        </>
      )}

      {(fa?.signings ?? []).length > 0 && (
        <>
          <h3 style={{ margin: "18px 0 8px" }}>Signed this offseason</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 18,
                        fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
            {(fa?.signings ?? []).map((s) => (
              <div key={s.player_id}>
                {s.player_name} → <strong>{s.team}</strong> · {s.term}yr / {money(s.value)}{" "}
                <span style={{ color: "var(--muted)" }}>({s.outcome.replace(/_/g, " ")}, round {s.round_number})</span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Who bid what, once a board is finished. The signings list above says who won;
          this says what everyone else was willing to pay, which is the part managers
          actually argue about. Sealed rounds never appear here -- a board only enters
          the record once it has resolved or been voided. */}
      {(history?.boards ?? []).length > 0 && (
        <>
          <h3 style={{ margin: "18px 0 8px" }}>
            Closed rounds — who bid what
            <span style={{ color: "var(--muted)", fontWeight: "var(--weight-regular)",
                           fontSize: "var(--text-sm)" }}>
              {" "}· {history!.period?.season} offseason
            </span>
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 18 }}>
            {(history?.boards ?? []).map((b) => <ClosedBoard key={b.id} board={b} />)}
          </div>
        </>
      )}

      {cap && !marketOpen && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10, marginBottom: 18 }}>
          <StatCard
            label="Payroll"
            value={money(cap.payroll)}
            sub={cap.over_cap ? `over the ${money(cap.limits.salary_cap)} cap` : `${money(cap.cap_room)} under the cap`}
            accent={cap.over_second_threshold ? "var(--red-600)" : cap.over_first_threshold ? "var(--amber-600)" : "var(--green-500)"}
          />
          <StatCard label="Cap room" value={money(cap.cap_room)} sub={`hard cap: ${money(cap.hard_cap_room)} left`} />
          {/* Roster room, not money, is what limits signings here: the deal is $0. */}
          <StatCard
            label="Roster"
            value={`${cap.roster_size}/${cap.max_roster}`}
            sub={cap.roster_spots > 0 ? `${cap.roster_spots} spot${cap.roster_spots === 1 ? "" : "s"} open` : "full"}
            accent={cap.roster_spots > 0 ? "var(--green-500)" : "var(--red-600)"}
          />
        </div>
      )}

      {!activeTeam && (
        <Alert tone="info" style={{ marginBottom: 16 }}>
          You don't own a team, so you can browse the pool but not sign anyone.
        </Alert>
      )}
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}

      {/* During an offer round the pool is a market, not a shop: you bid, you don't
          buy. The server refuses a plain signing too, so this isn't the only guard. */}
      {selected && offersOpen && acting && (() => {
        const existing = acting.offers.find((o) => o.player_id === selected.legacy_id);
        const own = selected.rights_team_id === acting.id;
        const ceiling = own ? acting.cap.max_own_offer : acting.cap.max_outside_offer;
        const scope = `offer:${selected.legacy_id}`;
        return (
          <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", padding: 16, marginBottom: 18 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>Offer {selected.name} a contract</h3>
              {own && <Tag tone="green" size="sm">Bird rights</Tag>}
            </div>
            <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 12px" }}>
              {own
                ? `Your own expiring player: only the hard cap limits what you can offer.`
                : `Outside signing: limited to cap room plus one mid-level exception.`}{" "}
              Most you can offer here: <strong>{money(ceiling)}</strong>/yr.
              {existing && <> You currently offer {existing.term}yr / {money(existing.value)}.</>}
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
              <div style={{ width: 150 }}>
                <Select label="Term" value={String(offerTerm)}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setOfferTerm(Number(e.target.value))}
                  options={Array.from({ length: acting.cap.limits.max_contract_years }, (_, i) => ({
                    value: String(i + 1), label: `${i + 1} ${i === 0 ? "year" : "years"}`,
                  }))} />
              </div>
              <div style={{ width: 170 }}>
                <Input label="Salary ($M / yr)" type="number"
                  min={acting.cap.limits.min_contract_value} max={acting.cap.limits.max_contract_value}
                  invalid={offerValue > ceiling} value={offerValue}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOfferValue(Number(e.target.value))} />
              </div>
              <Button variant="primary" disabled={busy || offerValue > ceiling}
                onClick={() => run("/free-agency/offers",
                  { team: acting.slug, player_id: selected.legacy_id, term: offerTerm, value: offerValue },
                  scope)}>
                {existing ? "Replace offer" : `Offer as ${acting.slug}`}
              </Button>
              {existing && (
                <Button variant="danger" disabled={busy}
                  onClick={() => run("/free-agency/offers/withdraw",
                    { team: acting.slug, player_id: selected.legacy_id }, scope)}>
                  Withdraw
                </Button>
              )}
              <Button onClick={() => setSelectedId(null)} disabled={busy}>Done</Button>
            </div>
            {problems[scope]?.length ? (
              <Alert tone="error" items={problems[scope]} style={{ marginTop: 12 }} />
            ) : null}
          </div>
        );
      })()}

      {selected && !marketOpen && activeTeam && cap && (
        <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", padding: 16, marginBottom: 18 }}>
          <h3 style={{ margin: "0 0 4px" }}>Sign {selected.name}</h3>
          {/* Nothing to negotiate: every free-agent deal is the same fixed
              league-minimum contract (signing_service.FREE_AGENT_CONTRACT_*). Term and
              salary are only in play when re-signing your own expiring players. */}
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 12px" }}>
            Free agents sign a standard <strong>1 year, $0M</strong> contract — it costs no cap space,
            just a roster spot. They become a free agent again at the end of the season.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            <Button variant="primary" onClick={sign} disabled={busy || rosterFull}>
              {busy ? "Signing…" : `Sign ${selected.name}`}
            </Button>
            <Button onClick={() => setSelectedId(null)} disabled={busy}>Cancel</Button>
          </div>
          {rosterFull && (
            <Alert tone="warning" style={{ marginTop: 12 }}>
              The roster is full ({cap.roster_size}/{cap.max_roster}). A player under contract can't be dropped, so
              you'll need to trade someone away first.
            </Alert>
          )}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end", marginBottom: 16 }}>
        <div style={{ flex: "1 1 220px", minWidth: 200 }}>
          <Input
            label="Search"
            placeholder="Find a free agent by name…"
            value={query}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setQuery(e.target.value)}
          />
        </div>
        <div style={{ width: 170 }}>
          <Select label="Position" options={POS_OPTIONS} value={pos} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setPos(e.target.value)} />
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No free agents"
          message={
            pool.length === 0
              ? "The pool fills up at the offseason rollover, when expiring contracts run out."
              : "Try clearing the search or filters."
          }
        />
      ) : (
        <>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 8px" }}>
            {rows.length} available{activeTeam ? " — pick one to make an offer" : ""}
          </p>
          <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
            <DataTable
              columns={tableColumns}
              rows={rows}
              getRowKey={(r: FA) => r.legacy_id}
              onRowClick={(r: FA) => {
                if (!activeTeam) { nav(`/players/${r.legacy_id}`); return; }
                setSelectedId(r.legacy_id);
                setErr(null);
              }}
            />
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
