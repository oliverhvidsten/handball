import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { TeamCard, EmptyState } from "../ds";

export default function MyTeams() {
  const { teams } = useAuth();
  const nav = useNavigate();

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>My Teams</h2>
      {teams.length === 0 ? (
        <EmptyState
          title="You don't own any teams yet"
          message="A commissioner assigns teams to managers. Once you own one, it appears here."
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
          {teams.map((t) => (
            <TeamCard
              key={t.slug}
              team={{ name: t.name, abbr: t.abbr, wins: t.wins, losses: t.losses, ties: t.ties }}
              yours
              onClick={() => nav(`/teams/${t.slug}`)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
