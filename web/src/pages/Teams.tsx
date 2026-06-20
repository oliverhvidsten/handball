import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { useAuth } from "../auth";
import { abbrev } from "../hooks";
import { TeamCard, Alert } from "../ds";

interface Row {
  slug: string;
  name: string;
  wins: number;
  losses: number;
  ties: number;
}

export default function Teams() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const { teams } = useAuth();
  const nav = useNavigate();
  const ownedSlugs = new Set(teams.map((t) => t.slug));

  useEffect(() => {
    supabase
      .from("teams")
      .select("slug, name, wins, losses, ties")
      .order("wins", { ascending: false })
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setRows((data as Row[]) ?? []);
      });
  }, []);

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Teams</h2>
      {err && <Alert tone="error">{err}</Alert>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {rows.map((t, i) => (
          <TeamCard
            key={t.slug}
            team={{ name: t.name, abbr: abbrev(t.name), wins: t.wins, losses: t.losses, ties: t.ties, rank: i + 1 }}
            yours={ownedSlugs.has(t.slug)}
            onClick={() => nav(`/teams/${t.slug}`)}
          />
        ))}
      </div>
    </section>
  );
}
