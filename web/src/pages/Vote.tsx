import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, EmptyState, Select, Tabs, Tag } from "../ds";
import { ApiError } from "../lib/api";
import {
  candidateLabel,
  fetchVotingState,
  filled,
  placeLabel,
  positions,
  STATUS_LABEL,
  submitAllStarBallot,
  submitAwardBallot,
  type Candidate,
  type VotingState,
} from "../lib/voting";

/**
 * The ballot box. Two votes live here and only the open one is actionable: the
 * ranked award ballots (10-7-5-3-1 over up to five names) and the positional
 * All-Star ballot, one per conference.
 *
 * Candidates from a team you own are shown but not selectable. The server refuses
 * them anyway — that check is the integrity of the whole count and cannot live in
 * the browser — but a manager should not have to submit a ballot to find out.
 */
export default function Vote() {
  const [state, setState] = useState<VotingState | null>(null);
  const [tab, setTab] = useState<"award" | "allstar">("award");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await fetchVotingState();
      setState(s);
      // Land on whichever vote is actually open, so the page opens on the thing
      // there is something to do about.
      setTab((t) => (s.status[t] === "open" ? t : s.status.allstar === "open" ? "allstar" : "award"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "could not load the ballot");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (err && !state) return <section><Alert tone="error">{err}</Alert></section>;
  if (!state) return <section><p style={{ color: "var(--muted)" }}>Loading…</p></section>;

  const anythingOpen = state.status.award === "open" || state.status.allstar === "open";

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>Vote</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        Season {state.season}. Awards are ranked {state.award_points.join("-")} over up to{" "}
        {state.award_ballot_size} names; the All-Star ballot is positional. You cannot vote
        for anyone on a team you own.
      </p>

      <Tabs
        items={[
          { value: "award", label: `Awards — ${STATUS_LABEL[state.status.award] ?? "—"}` },
          { value: "allstar", label: `All-Star — ${STATUS_LABEL[state.status.allstar] ?? "—"}` },
        ]}
        value={tab}
        onChange={(v: string) => { setTab(v as "award" | "allstar"); setErr(null); setNote(null); }}
        style={{ marginBottom: 16 }}
      />

      {err && <Alert tone="error" style={{ marginBottom: 12 }}>{err}</Alert>}
      {note && <Alert tone="success" style={{ marginBottom: 12 }}>{note}</Alert>}

      {!anythingOpen && (
        <EmptyState
          title="No ballot is open"
          message="All-Star voting opens after period 3, and award voting opens after period 5 once the regular season is complete. Your ballot will appear here."
        />
      )}

      {tab === "award" && anythingOpen && (
        <AwardBallots state={state} onErr={setErr} onNote={setNote} onSaved={load} />
      )}
      {tab === "allstar" && anythingOpen && (
        <AllStarBallots state={state} onErr={setErr} onNote={setNote} onSaved={load} />
      )}
    </section>
  );
}

interface SectionProps {
  state: VotingState;
  onErr: (m: string | null) => void;
  onNote: (m: string | null) => void;
  onSaved: () => Promise<void> | void;
}

// -- the ranked award ballots ------------------------------------------------
function AwardBallots({ state, onErr, onNote, onSaved }: SectionProps) {
  const open = state.status.award === "open";
  if (state.status.award === "tallied") {
    return (
      <EmptyState
        title="The award vote has been counted"
        message="The results, with every tally behind them, are on the Awards page."
      />
    );
  }
  if (!open) {
    return (
      <EmptyState
        title="Award voting is not open yet"
        message="The polls open once the regular season is complete."
      />
    );
  }
  return (
    <div style={{ display: "grid", gap: 16 }}>
      {state.awards.map((award) => (
        <AwardBallot
          key={award}
          award={award}
          size={state.award_ballot_size}
          points={state.award_points}
          candidates={state.candidates.awards[award] ?? []}
          saved={state.my_ballots.award[award] ?? []}
          onErr={onErr}
          onNote={onNote}
          onSaved={onSaved}
        />
      ))}
    </div>
  );
}

function AwardBallot({
  award, size, points, candidates, saved, onErr, onNote, onSaved,
}: {
  award: string;
  size: number;
  points: number[];
  candidates: Candidate[];
  saved: string[];
  onErr: (m: string | null) => void;
  onNote: (m: string | null) => void;
  onSaved: () => Promise<void> | void;
}) {
  const [picks, setPicks] = useState<(string | null)[]>(
    Array.from({ length: size }, (_, i) => saved[i] ?? null)
  );
  const [busy, setBusy] = useState(false);

  const byId = useMemo(() => {
    const m: Record<string, Candidate> = {};
    for (const c of candidates) m[c.id] = c;
    return m;
  }, [candidates]);

  function set(slot: number, value: string) {
    setPicks((p) => p.map((v, i) => (i === slot ? (value || null) : v)));
  }

  async function save() {
    const ids = filled(picks);
    if (ids.length === 0) {
      onErr(`Name at least one candidate for ${award}.`);
      return;
    }
    onErr(null);
    setBusy(true);
    try {
      await submitAwardBallot(award, ids);
      onNote(`${award} ballot saved.`);
      await onSaved();
    } catch (e) {
      onErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "could not save the ballot");
    } finally {
      setBusy(false);
    }
  }

  const chosen = filled(picks);

  return (
    <div
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        padding: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>{award}</h3>
        {saved.length > 0 && <Tag tone="green">Ballot in</Tag>}
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
          {candidates.length} eligible
        </span>
      </div>

      {candidates.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>Nobody is eligible for this award yet.</p>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 10,
              margin: "12px 0",
            }}
          >
            {picks.map((value, i) => (
              <Select
                key={i}
                label={`${placeLabel(i)} — ${points[i] ?? 0} pts`}
                value={value ?? ""}
                onChange={(e: React.ChangeEvent<HTMLSelectElement>) => set(i, e.target.value)}
              >
                <option value="">— nobody —</option>
                {candidates.map((c) => (
                  <option
                    key={c.id}
                    value={c.id}
                    disabled={c.own_team || (chosen.includes(c.id) && value !== c.id)}
                  >
                    {candidateLabel(c)}
                    {c.own_team ? " — your team" : ""}
                  </option>
                ))}
              </Select>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Button variant="primary" disabled={busy} onClick={() => void save()}>
              {busy ? "Saving…" : saved.length > 0 ? "Replace ballot" : "Submit ballot"}
            </Button>
            {saved.length > 0 && (
              <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
                Saved: {saved.map((id) => byId[id]?.name ?? "—").join(", ")}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// -- the positional All-Star ballot -----------------------------------------
function AllStarBallots({ state, onErr, onNote, onSaved }: SectionProps) {
  const [conf, setConf] = useState(state.conferences[0] ?? "");

  if (state.status.allstar === "tallied") {
    return (
      <EmptyState
        title="The All-Star game has been played"
        message="The squads, the score and the box score are on the Awards page."
      />
    );
  }
  if (state.status.allstar !== "open") {
    return (
      <EmptyState
        title="All-Star voting is not open yet"
        message="Voting opens automatically once period 3 of 5 has been simulated, and closes when the commissioner plays the game before period 4."
      />
    );
  }

  return (
    <>
      <Tabs
        items={state.conferences.map((c) => ({ value: c, label: c }))}
        value={conf}
        onChange={(v: string) => setConf(v)}
        style={{ marginBottom: 16 }}
      />
      <ConferenceBallot
        key={conf}
        conference={conf}
        ballot={state.all_star_ballot}
        pools={state.candidates.all_star[conf] ?? {}}
        saved={state.my_ballots.allstar[conf] ?? {}}
        onErr={onErr}
        onNote={onNote}
        onSaved={onSaved}
      />
    </>
  );
}

function ConferenceBallot({
  conference, ballot, pools, saved, onErr, onNote, onSaved,
}: {
  conference: string;
  ballot: Record<string, number>;
  pools: Record<string, Candidate[]>;
  saved: Record<string, string[]>;
  onErr: (m: string | null) => void;
  onNote: (m: string | null) => void;
  onSaved: () => Promise<void> | void;
}) {
  const pos = positions(ballot);
  const [picks, setPicks] = useState<Record<string, (string | null)[]>>(() => {
    const init: Record<string, (string | null)[]> = {};
    for (const p of pos) {
      init[p] = Array.from({ length: ballot[p] }, (_, i) => saved[p]?.[i] ?? null);
    }
    return init;
  });
  const [busy, setBusy] = useState(false);

  function set(position: string, slot: number, value: string) {
    setPicks((prev) => ({
      ...prev,
      [position]: prev[position].map((v, i) => (i === slot ? (value || null) : v)),
    }));
  }

  const short = pos.filter((p) => filled(picks[p]).length !== ballot[p]);

  async function save() {
    if (short.length > 0) {
      onErr(`Fill every slot before submitting: ${short.join(", ")}.`);
      return;
    }
    onErr(null);
    setBusy(true);
    try {
      const body: Record<string, string[]> = {};
      for (const p of pos) body[p] = filled(picks[p]);
      await submitAllStarBallot(conference, body);
      onNote(`${conference} All-Star ballot saved.`);
      await onSaved();
    } catch (e) {
      onErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "could not save the ballot");
    } finally {
      setBusy(false);
    }
  }

  const hasSaved = pos.some((p) => (saved[p] ?? []).length > 0);

  return (
    <div
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        padding: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>{conference} squad</h3>
        {hasSaved && <Tag tone="green">Ballot in</Tag>}
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
          {pos.map((p) => `${ballot[p]} ${p}`).join(" · ")} — the leaders start
        </span>
      </div>

      {pos.map((p) => (
        <div key={p} style={{ marginTop: 14 }}>
          <h4 style={{ margin: "0 0 8px" }}>{p}</h4>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 10,
            }}
          >
            {picks[p].map((value, i) => {
              const taken = filled(picks[p]);
              return (
                <Select
                  key={i}
                  value={value ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => set(p, i, e.target.value)}
                >
                  <option value="">— pick a {p.toLowerCase()} —</option>
                  {(pools[p] ?? []).map((c) => (
                    <option
                      key={c.id}
                      value={c.id}
                      disabled={c.own_team || (taken.includes(c.id) && value !== c.id)}
                    >
                      {candidateLabel(c)}
                      {c.own_team ? " — your team" : ""}
                    </option>
                  ))}
                </Select>
              );
            })}
          </div>
        </div>
      ))}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 16, flexWrap: "wrap" }}>
        <Button variant="primary" disabled={busy || short.length > 0} onClick={() => void save()}>
          {busy ? "Saving…" : hasSaved ? "Replace ballot" : "Submit ballot"}
        </Button>
        {short.length > 0 && (
          <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
            Still to fill: {short.join(", ")}
          </span>
        )}
      </div>
    </div>
  );
}
