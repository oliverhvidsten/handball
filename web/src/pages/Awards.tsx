import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, DataTable, EmptyState, Select, StatCard, Tabs, Tag } from "../ds";
import { ApiError } from "../lib/api";
import {
  fetchResults,
  type AllStarGame,
  type AllStarLine,
  type AwardResult,
  type TallyRow,
  type VotingResults,
} from "../lib/voting";

const CARD: React.CSSProperties = {
  background: "var(--surface-card)",
  border: "1px solid var(--line)",
  borderRadius: "var(--radius-lg)",
  overflow: "hidden",
};

/**
 * Results: every voted award with the full count behind it, plus the All-Star game
 * and its box score.
 *
 * The tally is shown in full, not just the winner. award_tallies exists precisely so
 * a result is evidence rather than an announcement — second place, and the margin,
 * are the interesting part of a 32-manager vote.
 */
export default function Awards() {
  const [data, setData] = useState<VotingResults | null>(null);
  const [season, setSeason] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchResults(season ?? undefined));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "could not load the results");
    }
  }, [season]);

  useEffect(() => {
    void load();
  }, [load]);

  if (err && !data) return <section><Alert tone="error">{err}</Alert></section>;
  if (!data) return <section><p style={{ color: "var(--muted)" }}>Loading…</p></section>;

  const voted = data.awards.filter((a) => a.tally.length > 0);
  const nothing = voted.length === 0 && !data.all_star_game;

  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ marginBottom: 4 }}>Awards</h2>
        {data.seasons.length > 1 && (
          <div style={{ width: 140 }}>
            <Select
              value={String(data.season)}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSeason(Number(e.target.value))}
              options={data.seasons.map((s) => ({ value: String(s), label: String(s) }))}
            />
          </div>
        )}
      </div>
      {err && <Alert tone="error" style={{ marginBottom: 12 }}>{err}</Alert>}

      {nothing ? (
        <EmptyState
          title="No awards yet"
          message="Award winners and the vote behind them appear here once the commissioner tallies the ballots."
        />
      ) : (
        <>
          {voted.map((a) => (
            <AwardCard key={a.award} result={a} />
          ))}
          {data.all_star_game && <AllStar game={data.all_star_game} />}
        </>
      )}
    </section>
  );
}

function AwardCard({ result }: { result: AwardResult }) {
  const nav = useNavigate();
  const winner = result.winner;
  const columns = [
    { key: "rank", header: "#", numeric: true, render: (r: TallyRow) => r.rank ?? "—" },
    { key: "name", header: "Name", render: (r: TallyRow) => r.name },
    {
      key: "team",
      header: "Team",
      render: (r: TallyRow) => r.team_name ?? (r.entity_kind === "coach" ? "—" : "—"),
    },
    { key: "points", header: "Pts", numeric: true },
    { key: "first_place_votes", header: "1st", numeric: true },
  ];

  return (
    <>
      <h3 style={{ margin: "28px 0 10px" }}>{result.award}</h3>
      {winner && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
          <StatCard
            label="Winner"
            value={winner.name}
            sub={`${winner.points} pts · ${winner.first_place_votes} first-place`}
          />
          {winner.team_name && <StatCard label="Team" value={winner.team_name} />}
          {winner.entity_kind === "coach" && <StatCard label="Voted on" value="Coaches" />}
        </div>
      )}
      <div style={CARD}>
        <DataTable
          columns={columns}
          rows={result.tally}
          getRowKey={(r: TallyRow) => r.entity_id}
          onRowClick={(r: TallyRow) =>
            r.entity_kind === "player" && r.legacy_id ? nav(`/players/${r.legacy_id}`) : undefined
          }
        />
      </div>
    </>
  );
}

function AllStar({ game }: { game: AllStarGame }) {
  const [side, setSide] = useState<"home" | "away">("home");
  const box = game.box_score[side];
  const conf = side === "home" ? game.home_conference : game.away_conference;

  return (
    <>
      <h3 style={{ margin: "28px 0 10px" }}>All-Star game</h3>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <StatCard
          label={`${game.away_conference} (away)`}
          value={game.away_score}
          sub={game.away_score > game.home_score ? "Winner" : null}
        />
        <StatCard
          label={`${game.home_conference} (home)`}
          value={game.home_score}
          sub={game.home_score > game.away_score ? "Winner" : null}
        />
        {game.went_to_overtime && <StatCard label="Finish" value="Overtime" />}
      </div>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        An exhibition: it counts toward nothing — not the standings, not the leaderboards,
        not the award race.
      </p>

      <Tabs
        items={[
          { value: "home", label: `${game.home_conference} roster` },
          { value: "away", label: `${game.away_conference} roster` },
        ]}
        value={side}
        onChange={(v: string) => setSide(v as "home" | "away")}
        style={{ marginBottom: 12 }}
      />
      <div style={CARD}>
        <DataTable
          columns={[
            {
              key: "slot",
              header: "",
              render: (r: AllStarLine) =>
                r.slot === "starter" ? <Tag tone="green" size="sm">Starter</Tag> : <Tag size="sm">Bench</Tag>,
            },
            { key: "name", header: `${conf} All-Star` },
            { key: "position", header: "Pos" },
            { key: "votes", header: "Votes", numeric: true },
            { key: "goals", header: "G", numeric: true },
            { key: "shots", header: "SH", numeric: true },
            { key: "saves", header: "SV", numeric: true },
            { key: "goals_allowed", header: "GA", numeric: true },
          ]}
          rows={box.players}
          getRowKey={(r: AllStarLine) => r.legacy_id}
        />
      </div>
    </>
  );
}
