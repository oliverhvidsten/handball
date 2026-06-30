import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { ROLE_LABEL, ROLE_TONE, seasonRange } from "../lib/coaches";
import { DataTable, Tag, Alert, EmptyState } from "../ds";

interface Stint {
  coach_name: string;
  team_slug: string;
  team_name: string;
  role: string;
  start_season: number;
  end_season: number | null;
  ord: number;
}

interface Profile {
  coach_name: string;
  age: number | null;
  pool_role: string;
  cur_role: string | null;
  current_team_name: string | null;
}

export default function CoachDetail() {
  const { legacyId = "" } = useParams();
  const [stints, setStints] = useState<Stint[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const nav = useNavigate();

  useEffect(() => {
    (async () => {
      const [{ data: prof }, { data: career, error }] = await Promise.all([
        supabase
          .from("coach_public")
          .select("coach_name, age, pool_role, cur_role, current_team_name")
          .eq("coach_legacy_id", legacyId)
          .maybeSingle(),
        supabase
          .from("coach_career")
          .select("coach_name, team_slug, team_name, role, start_season, end_season, ord")
          .eq("coach_legacy_id", legacyId)
          .order("ord", { ascending: false }),
      ]);
      if (error) setErr(error.message);
      else { setProfile((prof as Profile) ?? null); setStints((career as Stint[]) ?? []); }
      setLoading(false);
    })();
  }, [legacyId]);

  if (err) return <Alert tone="error">{err}</Alert>;
  if (loading) return <div className="center">Loading…</div>;
  if (!profile) return <EmptyState title="Coach not found" message={`No record for "${legacyId}".`} />;

  const name = profile.coach_name;
  const current = stints.find((s) => s.end_season === null);
  const role = current?.role ?? profile.pool_role;

  const columns = [
    {
      key: "team",
      header: "Team",
      render: (s: Stint) => (
        <a
          href={`#/teams/${s.team_slug}`}
          onClick={(e) => { e.preventDefault(); nav(`/teams/${s.team_slug}`); }}
          className="nha-navlink"
          style={{ color: "var(--action)", textDecoration: "none", fontWeight: "var(--weight-semibold)" }}
        >
          {s.team_name}
        </a>
      ),
    },
    {
      key: "role",
      header: "Role",
      render: (s: Stint) => <Tag tone={ROLE_TONE[s.role] || "neutral"} size="sm">{ROLE_LABEL[s.role] || s.role}</Tag>,
    },
    {
      key: "seasons",
      header: "Seasons",
      render: (s: Stint) => seasonRange(s.start_season, s.end_season),
    },
  ];

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <h2>{name}</h2>
        <Tag tone={ROLE_TONE[role] || "neutral"}>{ROLE_LABEL[role] || role}</Tag>
      </div>
      <div style={{ color: "var(--muted)", marginTop: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <span>
          <span style={{ fontWeight: "var(--weight-bold)", color: "var(--muted)" }}>Age</span>{" "}
          {profile.age ?? "—"}
        </span>
        <span>
          {current
            ? `Currently ${ROLE_LABEL[current.role]} · ${current.team_name}`
            : "Free agent — not currently coaching"}
        </span>
      </div>

      <h3 style={{ margin: "18px 0 10px" }}>Career history</h3>
      {stints.length === 0 ? (
        <EmptyState compact title="No career history" message="This coach has no recorded tenures yet." />
      ) : (
        <div style={{ background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
          <DataTable columns={columns} rows={stints} getRowKey={(s: Stint) => s.ord} />
        </div>
      )}
    </section>
  );
}
