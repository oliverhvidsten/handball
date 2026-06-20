import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { DraftOrderRow, EmptyState, Alert } from "../ds";
import { abbrev } from "../hooks";

interface PickRow {
  id: string;
  season: number;
  round: number;
  used: boolean;
  holder: { name: string } | null;
  original: { name: string } | null;
}

export default function Draft() {
  const [picks, setPicks] = useState<PickRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    supabase
      .from("draft_picks")
      .select("id, season, round, used, holder:holder_team_id(name), original:original_team_id(name)")
      .order("season", { ascending: true })
      .order("round", { ascending: true })
      .then(({ data, error }) => {
        if (error) setErr(error.message);
        else setPicks((data as any as PickRow[]) ?? []);
      });
  }, []);

  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Draft</h2>
      {err && <Alert tone="error">{err}</Alert>}
      {picks.length === 0 ? (
        <EmptyState
          title="No draft picks yet"
          message="Draft order is generated from standings at season's end; picks appear here once seeded, and ownership reflects any trades."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {picks.map((p, i) => {
            const holder = p.holder?.name ?? "—";
            const original = p.original?.name;
            const traded = original && original !== holder;
            return (
              <DraftOrderRow
                key={p.id}
                pick={{
                  overall: i + 1,
                  round: p.round,
                  inRound: i + 1,
                  team: holder,
                  abbr: abbrev(holder),
                  viaTeam: traded ? original : null,
                }}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}
