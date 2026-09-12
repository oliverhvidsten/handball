import { useCallback, useEffect, useState } from "react";
import { Alert, Button, DataTable, Tag } from "../../ds";
import { ApiError, apiFetch } from "../../lib/api";
import { fetchVotingState, STATUS_LABEL, STATUS_TONE, type VotingState } from "../../lib/voting";

/**
 * Commissioner controls for voting: the status of each ballot kind, how many managers
 * have voted, the Tally button, and Play for the All-Star exhibition.
 *
 * Opening the polls is NOT a control here — both votes open themselves once enough
 * periods have run. Committing a result is the commissioner's, and both commits are
 * one-way: the tally closes the award vote, and playing the game closes the All-Star
 * one. Turnout is shown as a COUNT and never as contents; an open ballot is sealed.
 */
export interface VotingPanelProps {
  season: number;
  onToast: (msg: string) => void;
}

interface CountRow {
  category: string;
  ballots: number;
}

export default function VotingPanel({ season, onToast }: VotingPanelProps) {
  const [state, setState] = useState<VotingState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await fetchVotingState());
    } catch {
      setState(null); // voting is not the Commissioner page's main job; stay quiet
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, season]);

  if (!state) return null;

  const awardStatus = state.status.award ?? "closed";
  const allStarStatus = state.status.allstar ?? "closed";
  const counts = state.ballot_counts ?? { award: {}, allstar: {} };
  const awardRows: CountRow[] = state.awards.map((a) => ({
    category: a,
    ballots: counts.award?.[a] ?? 0,
  }));
  const allStarRows: CountRow[] = state.conferences.map((c) => ({
    category: c,
    ballots: counts.allstar?.[c] ?? 0,
  }));
  const awardBallots = awardRows.reduce((n, r) => n + r.ballots, 0);
  const allStarBallots = allStarRows.reduce((n, r) => n + r.ballots, 0);

  async function act(path: string, ok: string) {
    setErr(null);
    setBusy(path);
    try {
      await apiFetch(path, { method: "POST" });
      onToast(ok);
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
    }
  }

  const columns = [
    { key: "category", header: "Ballot" },
    { key: "ballots", header: "Voted", numeric: true },
  ];

  return (
    <>
      <h3 style={{ margin: "28px 0 10px" }}>Voting</h3>
      {err && <Alert tone="error" style={{ marginBottom: 12 }}>{err}</Alert>}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <div
          style={{
            background: "var(--surface-card)",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius-lg)",
            padding: 14,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <strong>Awards</strong>
            <Tag tone={STATUS_TONE[awardStatus]}>{STATUS_LABEL[awardStatus] ?? awardStatus}</Tag>
          </div>

          {awardStatus === "closed" && (
            <Alert tone="info" style={{ marginBottom: 10 }}>
              The polls open on their own once the regular season is complete.
            </Alert>
          )}
          {awardStatus === "tallied" && (
            <Alert tone="success" style={{ marginBottom: 10 }}>
              Counted. The winners and every tally behind them are on the Awards page.
            </Alert>
          )}
          {awardStatus === "open" && awardBallots === 0 && (
            <Alert tone="warning" style={{ marginBottom: 10 }}>
              No ballots yet. Tallying now would name no winners — and the vote cannot be
              reopened afterwards.
            </Alert>
          )}

          <DataTable columns={columns} rows={awardRows} getRowKey={(r: CountRow) => r.category} />

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            <Button
              variant="primary"
              disabled={awardStatus !== "open" || busy != null}
              onClick={() => void act("/voting/awards/tally", "Award ballots counted.")}
            >
              {busy === "/voting/awards/tally" ? "Counting…" : "Tally the awards"}
            </Button>
          </div>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginBottom: 0 }}>
            Required before advancing the season: the rollover zeroes the stats the vote
            was cast on.
          </p>
        </div>

        <div
          style={{
            background: "var(--surface-card)",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius-lg)",
            padding: 14,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <strong>All-Star</strong>
            <Tag tone={STATUS_TONE[allStarStatus]}>{STATUS_LABEL[allStarStatus] ?? allStarStatus}</Tag>
          </div>

          {allStarStatus === "closed" && (
            <Alert tone="info" style={{ marginBottom: 10 }}>
              The polls open on their own at the break, after the first half is played.
            </Alert>
          )}
          {state.all_star_game && (
            <Alert tone="success" style={{ marginBottom: 10 }}>
              {state.all_star_game.away_conference} {state.all_star_game.away_score} —{" "}
              {state.all_star_game.home_score} {state.all_star_game.home_conference}
              {state.all_star_game.went_to_overtime ? " (OT)" : ""}
            </Alert>
          )}

          <DataTable columns={columns} rows={allStarRows} getRowKey={(r: CountRow) => r.category} />

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            <Button
              variant="primary"
              disabled={allStarStatus !== "open" || allStarBallots === 0 || busy != null}
              onClick={() => void act("/voting/all-star/play", "All-Star game played.")}
            >
              {busy === "/voting/all-star/play" ? "Playing…" : "Play the All-Star game"}
            </Button>
          </div>
          <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", marginBottom: 0 }}>
            Required before period {"4"}: the break falls before the second half.
          </p>
        </div>
      </div>
    </>
  );
}
