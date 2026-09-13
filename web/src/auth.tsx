import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./lib/supabase";

export interface OwnedTeam {
  id: string;
  slug: string;
  name: string;            // the city -- a key throughout the app, never edited
  nickname: string | null; // the team name after the city ("Aces"); manager-editable
  displayName: string;     // "Las Vegas Aces", or just the city when there is no nickname
  abbr: string;            // stored abbreviation, else derived from the city
  logoVersion: number;     // 0 = no logo; see teamLogoUrl
  wins: number;
  losses: number;
  ties: number;
}

interface AuthState {
  session: Session | null;
  role: string | null; // 'manager' | 'commissioner'
  isCommissioner: boolean;
  teams: OwnedTeam[]; // teams this manager owns
  activeTeam: OwnedTeam | null; // the TeamSwitcher selection
  setActiveTeam: (slug: string) => void;
  loading: boolean;
  /** Re-read the owned teams (after the Settings page changes a name, mark or logo). */
  refreshTeams: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

const ACTIVE_TEAM_KEY = "nha.activeTeam";

/** Abbreviation from a team name: initials of words, capped at 3 (matches the kit). */
function abbrev(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.map((w) => w[0]).join("").slice(0, 3).toUpperCase();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [teams, setTeams] = useState<OwnedTeam[]>([]);
  const [activeSlug, setActiveSlug] = useState<string | null>(
    () => localStorage.getItem(ACTIVE_TEAM_KEY)
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  const loadOwned = useCallback(async () => {
    if (!session) return;
    const { data: t } = await supabase
      .from("teams")
      .select("id, slug, name, nickname, abbr, logo_version, wins, losses, ties")
      .eq("owner_id", session.user.id)
      .order("name");
    const owned: OwnedTeam[] = (t ?? []).map((row: any) => ({
      id: row.id,
      slug: row.slug,
      name: row.name,
      nickname: row.nickname ?? null,
      displayName: row.nickname ? `${row.name} ${row.nickname}` : row.name,
      abbr: row.abbr ?? abbrev(row.name),
      logoVersion: row.logo_version ?? 0,
      wins: row.wins,
      losses: row.losses,
      ties: row.ties,
    }));
    setTeams(owned);
    return owned;
  }, [session]);

  // Resolve role + owned teams whenever the session changes.
  useEffect(() => {
    if (!session) {
      setRole(null);
      setTeams([]);
      setActiveSlug(null);
      return;
    }
    (async () => {
      const { data: m } = await supabase.from("managers").select("role").maybeSingle();
      setRole(m?.role ?? "manager");
      const owned = (await loadOwned()) ?? [];
      // Keep the persisted selection if it's still an owned team; otherwise
      // fall back to the first team (handles a stale slug from another user).
      // `cur` can be null here on a cold load (the no-session branch runs while
      // Supabase resolves the session), so fall back to localStorage too.
      setActiveSlug((cur) => {
        const want = cur ?? localStorage.getItem(ACTIVE_TEAM_KEY);
        return owned.some((t) => t.slug === want) ? want : owned[0]?.slug ?? null;
      });
    })();
  }, [session, loadOwned]);

  const activeTeam = useMemo(
    () => teams.find((t) => t.slug === activeSlug) ?? teams[0] ?? null,
    [teams, activeSlug]
  );

  const value: AuthState = {
    session,
    role,
    isCommissioner: role === "commissioner",
    teams,
    activeTeam,
    setActiveTeam: (slug) => {
      setActiveSlug(slug);
      localStorage.setItem(ACTIVE_TEAM_KEY, slug);
    },
    loading,
    refreshTeams: async () => { await loadOwned(); },
    signIn: async (email, password) => {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
    },
    signOut: async () => {
      localStorage.removeItem(ACTIVE_TEAM_KEY);
      await supabase.auth.signOut();
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
