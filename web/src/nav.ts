/**
 * The primary navigation, in one place so the app shell and the dev harness render
 * the same thing.
 *
 * `scope` partitions the nav: "team" pages re-render on the TeamSwitcher's
 * activeTeam selection (grouped with the switcher in the TopNav); "league" pages
 * are league-wide and ignore it.
 *
 * `group` folds a league link into a dropdown of that name. Fifteen top-level tabs
 * do not fit a 56px bar at laptop widths, so the ones you reach for daily (the team
 * pages, Standings, Schedule) stay flat and the reference / season-event pages sit
 * one click away under a header. A link with no group is always flat.
 */
export type NavScope = "team" | "league";
export type NavGroup = "League" | "Season";

export interface NavItem {
  label: string;
  to: string;
  scope: NavScope;
  group?: NavGroup;
}

export const NAV: NavItem[] = [
  { label: "Dashboard", to: "/dashboard", scope: "team" },
  { label: "Roster", to: "/roster", scope: "team" },
  { label: "Trades", to: "/trades", scope: "team" },
  { label: "Free Agents", to: "/free-agents", scope: "team" },
  { label: "Standings", to: "/standings", scope: "league" },
  { label: "Schedule", to: "/schedule", scope: "league" },
  { label: "Teams", to: "/teams", scope: "league", group: "League" },
  { label: "Players", to: "/players", scope: "league", group: "League" },
  { label: "Coaches", to: "/coaches", scope: "league", group: "League" },
  { label: "Leaders", to: "/leaderboard", scope: "league", group: "League" },
  { label: "Playoffs", to: "/playoffs", scope: "league", group: "Season" },
  { label: "Draft", to: "/draft", scope: "league", group: "Season" },
  { label: "Vote", to: "/vote", scope: "league", group: "Season" },
  { label: "Awards", to: "/awards", scope: "league", group: "Season" },
  { label: "Hall of Fame", to: "/hall-of-fame", scope: "league", group: "Season" },
];
