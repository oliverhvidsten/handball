import { useState } from "react";
import { Button, Input, Select, Tag, Alert } from "../ds";
import { beatsLeader, leadingOffer, type FAAuction, type FATeam } from "../lib/freeAgency";

const money = (m: number) => `$${m}M`;
const deal = (term: number, value: number) => `${term}yr / ${money(value)}`;

function hoursSince(iso: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000);
}

/**
 * One player's board. Read-only for everyone watching; the action row appears only
 * for the team actually on the clock (`acting`), and the commissioner row only for a
 * commissioner. Deliberately one component for both cases so a board looks the same
 * wherever it is rendered.
 */
export function AuctionBoard({
  auction,
  acting,
  commissioner,
  problems,
  onAct,
  busy,
}: {
  auction: FAAuction;
  acting?: FATeam | null;
  commissioner?: boolean;
  problems?: string[];
  onAct?: (verb: string, body: Record<string, unknown>) => void;
  busy?: boolean;
}) {
  const lead = leadingOffer(auction);
  const [term, setTerm] = useState(lead?.term ?? 1);
  const [value, setValue] = useState((lead?.value ?? 0) + 1);
  const [raising, setRaising] = useState(false);
  const [awardTo, setAwardTo] = useState("");

  const waiting = hoursSince(auction.waiting_since);
  const stillIn = auction.seats.filter((s) => s.state === "active");
  const myTurn =
    !!acting &&
    ((auction.status === "bidding" && auction.turn_team === acting.slug) ||
      (auction.status === "matching" && auction.rights_team === acting.slug));
  const ceiling = acting
    ? (auction.rights_team === acting.slug ? acting.cap.max_own_offer : acting.cap.max_outside_offer)
    : 0;
  const raiseOk = !!lead && beatsLeader(term, value, lead) && value <= ceiling;

  return (
    <div
      style={{
        background: "var(--surface-card)",
        border: `1px solid ${myTurn ? "var(--amber-600)" : "var(--line)"}`,
        borderRadius: "var(--radius-lg)",
        padding: 16,
        marginBottom: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>{auction.player_name}</h3>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{auction.position}</span>
        {auction.restricted && <Tag tone="purple" size="sm">Restricted</Tag>}
        {auction.status === "awaiting_award" && <Tag tone="amber" size="sm">Deadlocked</Tag>}
        {auction.status === "matching" && <Tag tone="blue" size="sm">Match window</Tag>}
        {myTurn && <Tag tone="amber" size="sm">Your turn</Tag>}
      </div>

      <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "6px 0 10px" }}>
        {auction.status === "matching" ? (
          <>
            Offer sheet: <strong>{lead ? deal(lead.term, lead.value) : "—"}</strong> from{" "}
            {lead?.team_name}. The rights holder may match it exactly.
          </>
        ) : auction.status === "awaiting_award" ? (
          <>
            Everyone matched and nobody raised at{" "}
            <strong>{lead ? deal(lead.term, lead.value) : "—"}</strong>. The commissioner decides.
          </>
        ) : (
          <>
            Leading: <strong>{lead ? deal(lead.term, lead.value) : "—"}</strong> ({lead?.team_name}) ·{" "}
            {stillIn.length} team{stillIn.length === 1 ? "" : "s"} still in
            {auction.turn_team_name && (
              <> · on the clock: {auction.turn_team_name}
                {waiting != null && ` · ${waiting}h`}</>
            )}
          </>
        )}
      </p>

      {/* The rotation, in bidding order. A forfeited seat stays visible: who dropped
          out and at what price is the most interesting part of the history. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10 }}>
        {auction.seats.map((s) => {
          const offer = auction.offers.find((o) => o.team === s.team);
          const out = s.state !== "active";
          return (
            <div
              key={s.team}
              style={{
                display: "flex", gap: 10, alignItems: "baseline",
                fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)",
                opacity: out ? 0.45 : 1,
                textDecoration: out ? "line-through" : "none",
              }}
            >
              <span style={{ width: 22, color: "var(--muted)" }}>{s.turn_order + 1}.</span>
              <span style={{ minWidth: 110, fontWeight: "var(--weight-semibold)" }}>{s.team_name}</span>
              <span>{offer ? deal(offer.term, offer.value) : "—"}</span>
              {lead?.team === s.team && !out && <Tag tone="green" size="sm">leading</Tag>}
            </div>
          );
        })}
      </div>

      {problems?.length ? <Alert tone="error" items={problems} style={{ marginBottom: 10 }} /> : null}

      {myTurn && onAct && auction.status === "bidding" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          {!raising ? (
            <>
              <Button variant="primary" disabled={busy || (lead?.value ?? 0) > ceiling}
                onClick={() => onAct("bid", { team: acting!.slug, action: "match" })}
                title={(lead?.value ?? 0) > ceiling ? `Over your ${money(ceiling)} ceiling` : undefined}>
                {lead?.team === acting!.slug ? `Stand pat as ${acting!.slug}` : `Match ${lead ? money(lead.value) : ""} as ${acting!.slug}`}
              </Button>
              <Button onClick={() => setRaising(true)} disabled={busy}>Raise…</Button>
              <Button variant="danger" disabled={busy}
                onClick={() => onAct("bid", { team: acting!.slug, action: "forfeit" })}>
                Forfeit
              </Button>
            </>
          ) : (
            <>
              <div style={{ width: 130 }}>
                <Select label="Term" value={String(term)}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTerm(Number(e.target.value))}
                  options={Array.from({ length: acting!.cap.limits.max_contract_years }, (_, i) => ({
                    value: String(i + 1), label: `${i + 1} ${i === 0 ? "year" : "years"}`,
                  }))} />
              </div>
              <div style={{ width: 150 }}>
                <Input label={`Salary (max ${money(ceiling)})`} type="number" min={0} max={ceiling}
                  invalid={!raiseOk} value={value}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setValue(Number(e.target.value))} />
              </div>
              <Button variant="primary" disabled={busy || !raiseOk}
                onClick={() => { onAct("bid", { team: acting!.slug, action: "raise", term, value }); setRaising(false); }}>
                Raise to {deal(term, value)}
              </Button>
              <Button onClick={() => setRaising(false)} disabled={busy}>Cancel</Button>
            </>
          )}
        </div>
      )}

      {myTurn && onAct && auction.status === "matching" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary" disabled={busy || (lead?.value ?? 0) > ceiling}
            onClick={() => onAct("match", { team: acting!.slug })}
            title={(lead?.value ?? 0) > ceiling ? `You can't fit ${money(lead?.value ?? 0)} — decline or make room` : undefined}>
            Match {lead ? deal(lead.term, lead.value) : ""} and keep them
          </Button>
          <Button variant="danger" disabled={busy}
            onClick={() => onAct("decline", { team: acting!.slug })}>
            Let them go
          </Button>
        </div>
      )}

      {commissioner && onAct && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end",
                      marginTop: 10, paddingTop: 10, borderTop: "1px dashed var(--line)" }}>
          {auction.status === "awaiting_award" ? (
            <>
              <div style={{ width: 190 }}>
                <Select label="Award to" value={awardTo}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setAwardTo(e.target.value)}
                  options={[{ value: "", label: "Choose a team…" },
                            ...stillIn.map((s) => ({ value: s.team, label: s.team_name }))]} />
              </div>
              <Button variant="primary" disabled={busy || !awardTo}
                onClick={() => onAct("award", { team: awardTo, reason: "commissioner decision" })}>
                Award
              </Button>
            </>
          ) : (
            <>
              <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                Commissioner{waiting != null && waiting >= 24 ? ` · waiting ${waiting}h` : ""}:
              </span>
              {stillIn
                .filter((s) => auction.status !== "bidding" || s.team === auction.turn_team)
                .map((s) => (
                  <Button key={s.team} variant="danger" disabled={busy}
                    onClick={() => onAct("force-forfeit", { team: s.team, reason: "no response" })}>
                    Force {s.team_name} out
                  </Button>
                ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
