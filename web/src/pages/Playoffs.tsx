import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, apiFetch } from "../lib/api";
import { Alert, EmptyState } from "../ds";
import { abbrev } from "../hooks";

/** One matchup. Scores are null until it has been played. */
export interface Series {
  round: number;
  conference: string | null;
  label: string;
  high: { slug: string; name: string; seed: number };
  low: { slug: string; name: string; seed: number };
  winner: string | null;
  played: boolean;
  high_score: number | null;
  low_score: number | null;
  went_to_overtime: boolean;
}

export interface Bracket {
  season: number;
  started: boolean;
  complete: boolean;
  champion: string | null;
  next_round: number | null;
  total_rounds: number;
  rounds_run: number;
  regular_season_complete: boolean;
  run_status: "idle" | "running" | "done" | "error";
  run_kind: "period" | "playoff";
  run_error: string | null;
  run_stale: boolean;
  series: Series[];
}

export default function Playoffs() {
  const [bracket, setBracket] = useState<Bracket | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();

  const load = useCallback(async () => {
    try {
      setBracket(await apiFetch<Bracket>("/playoffs/bracket", { method: "GET" }));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "could not load the bracket");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // A round takes minutes to simulate; poll it in so the bracket fills itself in.
  const running =
    bracket?.run_status === "running" && bracket.run_kind === "playoff" && !bracket.run_stale;
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(id);
  }, [running, load]);

  if (err) return <section><Alert tone="error">{err}</Alert></section>;
  if (!bracket) return <section><p style={{ color: "var(--muted)" }}>Loading…</p></section>;

  if (!bracket.started) {
    return (
      <section>
        <h2 style={{ marginBottom: 16 }}>Playoffs</h2>
        <EmptyState
          title="No bracket yet"
          message={
            bracket.regular_season_complete
              ? "The regular season is over — the commissioner seeds the bracket from the final standings."
              : "The top eight teams in each conference make the playoffs. The bracket is seeded once the regular season finishes."
          }
        />
      </section>
    );
  }

  const rounds = [...new Set(bracket.series.map((s) => s.round))].sort((a, b) => a - b);

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>Playoffs</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        Season {bracket.season} · single elimination · higher seed hosts and advances on a tie
      </p>

      {bracket.champion && <ChampionBanner name={bracket.champion} season={bracket.season} />}
      {running && (
        <Alert tone="info" style={{ marginBottom: 16 }}>
          Round {bracket.next_round} is being played… results appear automatically.
        </Alert>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
        {rounds.map((round) => (
          <Round
            key={round}
            series={bracket.series.filter((s) => s.round === round)}
            onPick={(slug) => nav(`/teams/${slug}`)}
          />
        ))}
      </div>
    </section>
  );
}

function ChampionBanner({ name, season }: { name: string; season: number }) {
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 14, padding: "18px 20px", marginBottom: 20,
        background: "var(--green-50)", border: "1px solid var(--green-200)",
        borderRadius: "var(--radius-lg)",
      }}
    >
      <span style={{ fontSize: 28, lineHeight: 1 }}>🏆</span>
      <div>
        <div style={{
          fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
          textTransform: "uppercase", color: "var(--green-700)",
        }}>
          {season} Champions
        </div>
        <div style={{ fontSize: "var(--text-xl)", fontWeight: "var(--weight-bold)", color: "var(--green-800)" }}>
          {name}
        </div>
      </div>
    </div>
  );
}

/** One round: its matchups, grouped by conference (the Final has none). */
function Round({ series, onPick }: { series: Series[]; onPick: (slug: string) => void }) {
  const conferences = [...new Set(series.map((s) => s.conference))];
  // Each conference's label is prefixed with its own name ("Eastern Semifinals");
  // the round heading is the shared remainder, with the conference as a subheading.
  const { conference, label } = series[0];
  const heading = conference ? label.slice(conference.length).trim() : label;

  return (
    <div>
      <h3 style={{ margin: "0 0 12px" }}>{heading}</h3>
      <div style={{
        display: "grid", gap: 16,
        gridTemplateColumns: `repeat(auto-fit, minmax(280px, 1fr))`,
      }}>
        {conferences.map((conference) => (
          <div key={conference ?? "final"}>
            {conference && (
              <div style={{
                fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)", color: "var(--muted)",
                textTransform: "uppercase", letterSpacing: "var(--tracking-wide)", marginBottom: 8,
              }}>
                {conference}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {series.filter((s) => s.conference === conference).map((s) => (
                <SeriesCard key={`${s.high.slug}-${s.low.slug}`} series={s} onPick={onPick} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SeriesCard({ series, onPick }: { series: Series; onPick: (slug: string) => void }) {
  return (
    <div style={{
      background: "var(--surface-card)", border: "1px solid var(--line)",
      borderRadius: "var(--radius-md)", overflow: "hidden",
    }}>
      <Side
        team={series.high} score={series.high_score} won={series.winner === series.high.slug}
        decided={series.played} onPick={onPick}
      />
      <div style={{ height: 1, background: "var(--line)" }} />
      <Side
        team={series.low} score={series.low_score} won={series.winner === series.low.slug}
        decided={series.played} onPick={onPick}
      />
      {series.went_to_overtime && (
        <div style={{
          padding: "4px 12px", fontSize: "var(--text-xs)", color: "var(--muted)",
          background: "var(--surface-2)", textAlign: "right",
        }}>
          OT
        </div>
      )}
    </div>
  );
}

function Side({
  team, score, won, decided, onPick,
}: {
  team: { slug: string; name: string; seed: number };
  score: number | null;
  won: boolean;
  decided: boolean;
  onPick: (slug: string) => void;
}) {
  // Before a game is played neither side is dimmed; after it, the loser is.
  const loser = decided && !won;
  return (
    <div
      onClick={() => onPick(team.slug)}
      style={{
        display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", cursor: "pointer",
        opacity: loser ? 0.55 : 1,
        fontWeight: won ? "var(--weight-bold)" : "var(--weight-medium)",
      }}
    >
      <span style={{
        minWidth: 20, fontSize: "var(--text-xs)", color: "var(--muted)", fontVariantNumeric: "tabular-nums",
      }}>
        {team.seed}
      </span>
      <span style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        minWidth: 34, padding: "2px 6px", borderRadius: "var(--radius-sm)",
        background: "var(--surface-3)", fontSize: "var(--text-xs)",
        fontWeight: "var(--weight-bold)", color: "var(--text-soft)",
      }}>
        {abbrev(team.name)}
      </span>
      <span style={{ flex: 1 }}>{team.name}</span>
      {won && <span style={{ color: "var(--green-700)" }}>▸</span>}
      <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 24, textAlign: "right" }}>
        {score ?? "—"}
      </span>
    </div>
  );
}
