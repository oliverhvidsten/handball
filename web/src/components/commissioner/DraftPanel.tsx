/**
 * Commissioner controls for the draft: upload the prospect class, run the lottery
 * (and show the draw), open the room, and force a pick for the team on the clock.
 *
 * The panel is deliberately a phase machine rather than four always-visible buttons:
 * the draft runs in one order, and showing "Open the draft" next to "Draw the
 * lottery" invites the click that is about to be refused. Whatever comes next is the
 * primary button; everything already done is stated as a fact.
 *
 * It renders nothing at all when the season has no draft — leagues that predate the
 * draft, and any season the rollover has not reached yet.
 */
import { useMemo, useRef, useState } from "react";
import { Alert, Button, EmptyState, Tag } from "../../ds";
import { apiFetch } from "../../lib/api";
import { formatTimeLeft, problemsOf, useDraftState } from "../../lib/draft";

export interface DraftPanelProps {
  season: number;
  onToast: (msg: string) => void;
}

export default function DraftPanel({ season, onToast }: DraftPanelProps) {
  const { draft, refresh } = useDraftState();
  const [busy, setBusy] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [names, setNames] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const clock = draft?.on_the_clock ?? null;
  const enough = useMemo(
    () => !!draft && draft.board.length >= draft.picks,
    [draft],
  );

  async function act(label: string, path: string, init: RequestInit, done: (r: any) => string) {
    setBusy(label);
    setProblems([]);
    try {
      const res = await apiFetch<any>(path, init);
      onToast(done(res));
      await refresh();
    } catch (e) {
      setProblems(problemsOf(e));
    } finally {
      setBusy(null);
    }
  }

  const runLottery = () =>
    act("lottery", "/draft/lottery", { method: "POST", body: JSON.stringify({}) },
      (r) => `Lottery drawn (seed ${r.seed}). ${r.results[0].team_name} picks first`
        + (r.protections.length
          ? `; ${r.protections.length} protection(s) resolved.` : "."));

  const uploadText = () =>
    act("prospects", "/draft/prospects",
      { method: "POST", body: JSON.stringify({ text: names, filename: "class.txt" }) },
      (r) => `${r.prospects} prospects on the board for ${r.picks} picks.`);

  /** A picked file is read here and sent as the JSON text body rather than as a
   *  multipart upload: apiFetch sets a JSON Content-Type, and overriding it to let
   *  the browser pick a multipart boundary is more moving parts than a names file
   *  is worth. The endpoint accepts both; this is the same bytes either way. */
  async function uploadFile(file: File) {
    setBusy("prospects");
    setProblems([]);
    try {
      const text = await file.text();
      const res = await apiFetch<any>("/draft/prospects", {
        method: "POST",
        body: JSON.stringify({ text, filename: file.name }),
      });
      onToast(`${res.prospects} prospects on the board for ${res.picks} picks.`);
      await refresh();
    } catch (e) {
      setProblems(problemsOf(e));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const openRoom = () =>
    act("open", "/draft/open", { method: "POST" },
      (r) => `The draft is open — ${r.on_the_clock.team_name} is on the clock.`);

  const forcePick = () =>
    act("pick", "/draft/pick", { method: "POST", body: JSON.stringify({ force: true }) },
      (r) => `${r.team_name} takes ${r.player_name} at ${r.overall} overall.`);

  if (!draft || draft.status == null) return null;

  const { status } = draft;
  return (
    <>
      <h3 style={{ margin: "28px 0 10px" }}>Draft</h3>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        The {draft.season} draft: draw the lottery for the first{" "}
        {draft.lottery?.pool_size ?? 16} picks, put the prospect class on the board, then
        open the room. Managers pick in order with a {draft.turn_limit_hours}-hour clock;
        a turn that runs out is taken automatically. Free agency and the next season
        both wait on it finishing.
      </p>

      {problems.length > 0 && (
        <Alert tone="error" items={problems} style={{ marginBottom: 12 }} />
      )}

      {status === "complete" ? (
        <Alert tone="success" style={{ marginBottom: 12 }}>
          The {draft.season} draft is complete — {draft.picks} picks made
          {draft.auto_picks > 0 && <>, {draft.auto_picks} of them by the clock</>}. Every
          undrafted prospect is now an unrestricted free agent.
        </Alert>
      ) : status === "open" ? (
        <Alert tone="info" style={{ marginBottom: 12 }}>
          Pick {draft.picks_made + 1} of {draft.picks}.{" "}
          {clock && (
            <><strong>{clock.team_name}</strong> is on the clock
              {formatTimeLeft(clock.turn_seconds_left)
                ? ` — ${formatTimeLeft(clock.turn_seconds_left)}` : ""}.</>
          )}{" "}
          The room itself is on the Draft page.
        </Alert>
      ) : null}

      {/* -- the lottery ------------------------------------------------------ */}
      {status === "pending" ? (
        <div style={{ marginBottom: 12 }}>
          <Button variant="primary" disabled={busy != null} onClick={runLottery}>
            {busy === "lottery" ? "Drawing…" : "Draw the lottery"}
          </Button>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-xs)", marginBottom: 0 }}>
            Weighted, one slot at a time, worst record first. The seed is recorded with
            the result so the draw can be replayed. Protections on traded first-round
            picks are settled by the same draw.
          </p>
        </div>
      ) : draft.lottery && draft.lottery.results.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12,
                      alignItems: "center" }}>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>
            Lottery (seed {draft.lottery.seed}):
          </span>
          {draft.lottery.results.slice(0, 5).map((r) => (
            <Tag key={r.slot} size="sm" tone={r.slot === 1 ? "green" : "neutral"}>
              {r.slot}. {r.team_name}
            </Tag>
          ))}
          {draft.lottery.results.length > 5 && (
            <span style={{ color: "var(--muted)", fontSize: "var(--text-xs)" }}>
              +{draft.lottery.results.length - 5} more on the Draft page
            </span>
          )}
        </div>
      ) : null}

      {/* -- the prospect class ----------------------------------------------- */}
      {(status === "pending" || status === "lottery_drawn") && (
        <div style={{ marginBottom: 12 }}>
          <h4 style={{ margin: "12px 0 6px" }}>
            Prospect class{" "}
            <span style={{ color: "var(--muted)", fontWeight: "normal",
                           fontSize: "var(--text-sm)" }}>
              ({draft.board.length} on the board, {draft.picks} needed)
            </span>
          </h4>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-xs)", marginTop: 0 }}>
            One name per line, or a CSV with a <code>Name</code> column and an optional{" "}
            <code>Position</code>. Each prospect is generated once, on upload, so the
            ratings on the board are the ratings the drafted player has. Uploading again
            replaces the board.
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
            <textarea
              value={names}
              onChange={(e) => setNames(e.target.value)}
              placeholder={"Marcus Webb\nJo Ling\n…"}
              rows={4}
              style={{ flex: "1 1 260px", minWidth: 240, padding: 8,
                       fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)",
                       background: "var(--surface-card)", color: "var(--text-body)",
                       border: "1px solid var(--line)", borderRadius: "var(--radius-sm)" }}
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <Button disabled={busy != null || !names.trim()} onClick={uploadText}>
                {busy === "prospects" ? "Uploading…" : "Upload names"}
              </Button>
              <input ref={fileRef} type="file" accept=".txt,.csv"
                     style={{ fontSize: "var(--text-xs)" }}
                     onChange={(e) => {
                       const f = e.target.files?.[0];
                       if (f) void uploadFile(f);
                     }} />
            </div>
          </div>
        </div>
      )}

      {/* -- opening the room -------------------------------------------------- */}
      {status === "lottery_drawn" && (
        <div style={{ marginBottom: 12 }}>
          {!enough && (
            <Alert tone="warning" style={{ marginBottom: 8 }}>
              The board has {draft.board.length} prospect(s) for {draft.picks} picks. Upload
              at least {draft.picks} before opening the room — a draft that runs out of
              players mid-round has to be unwound by hand.
            </Alert>
          )}
          <Button variant="primary" disabled={busy != null || !enough} onClick={openRoom}>
            {busy === "open" ? "Opening…" : "Open the draft"}
          </Button>
        </div>
      )}

      {/* -- forcing a pick ---------------------------------------------------- */}
      {status === "open" && clock && (
        <div style={{ marginBottom: 12 }}>
          <Button variant="danger" disabled={busy != null} onClick={forcePick}>
            {busy === "pick" ? "Picking…" : `Pick for ${clock.team_name}`}
          </Button>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-xs)", marginBottom: 0 }}>
            Takes the best available for the team on the clock, the same choice the
            expiry clock would make. Use it when a manager is unreachable and the draft
            should not wait the full {draft.turn_limit_hours} hours.
          </p>
        </div>
      )}

      {status === "pending" && draft.picks === 0 && (
        <EmptyState compact title="No picks seeded"
                    message={`The ${season} rollover has not written a draft order yet.`} />
      )}
    </>
  );
}
