import { Link } from "react-router-dom";
import { Tag, Alert, EmptyState } from "../ds";
import { byClassYear, useHallOfFame, type Inductee } from "../lib/hallOfFame";

const POS_TONE: Record<string, any> = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

function CareerLine({ inductee }: { inductee: Inductee }) {
  const rs = inductee.regular_season;
  const po = inductee.playoff;
  const isG = inductee.position === "Goalie";
  const stat = (line: typeof rs) => (isG ? `${line.saves} SV` : `${line.goals} G`);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--text-sm)" }}>
      <span>
        <span style={{ color: "var(--muted)" }}>Regular season</span>{" "}
        {rs.games} GP · {stat(rs)} · {rs.shots} shots · {rs.goals_allowed} GA
      </span>
      {po.games > 0 && (
        <span>
          <span style={{ color: "var(--muted)" }}>Playoffs</span>{" "}
          {po.games} GP · {stat(po)} · {po.shots} shots · {po.goals_allowed} GA
        </span>
      )}
    </div>
  );
}

/**
 * The Hall: inductees grouped by class year, with their citation and career line
 * (aggregated server-side, regular season and playoffs kept apart).
 */
export default function HallOfFame() {
  const { hof, loading } = useHallOfFame();
  const inductees = hof?.inductees ?? [];
  const classes = byClassYear(inductees);

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Hall of Fame</h2>

      {loading ? (
        <div className="center">Loading…</div>
      ) : hof == null ? (
        <Alert tone="error">Couldn't load the Hall of Fame.</Alert>
      ) : inductees.length === 0 ? (
        <EmptyState
          title="Nobody has been inducted yet"
          message="The commissioner inducts retired players; each class appears here with its citations and career totals."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          {classes.map(([year, members]) => (
            <div key={year}>
              <h3 style={{ margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Class of {year}</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {members.map((m) => (
                  <div
                    key={m.legacy_id}
                    style={{
                      background: "var(--surface-card)", border: "1px solid var(--line)",
                      borderRadius: "var(--radius-lg)", padding: 16,
                      display: "flex", flexDirection: "column", gap: 8,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <Link to={`/players/${m.legacy_id}`} style={{ fontWeight: 700, fontSize: "var(--text-md)" }}>
                        {m.name}
                      </Link>
                      <Tag tone={POS_TONE[m.position] || "neutral"} size="sm">{m.position}</Tag>
                    </div>
                    {m.citation && (
                      <p style={{ margin: 0, color: "var(--text-soft)", fontStyle: "italic" }}>&ldquo;{m.citation}&rdquo;</p>
                    )}
                    <CareerLine inductee={m} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
