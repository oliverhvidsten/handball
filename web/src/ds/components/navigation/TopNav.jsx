import React from "react";
import { NotificationBadge } from "./NotificationBadge.jsx";

/**
 * NHA TopNav — league app header. Brand mark, primary nav links, optional
 * trade-request bell with a count badge, signed-in email (commissioner star),
 * and Sign-out. links: [{ label, href, active? }]
 */
export function TopNav({
  brand = "NHA",
  links = [],
  email = "",
  commissioner = false,
  tradeRequests = 0,
  teamSwitcher = null,
  onSignOut,
  onBrandClick,
  onBell,
  mobile = false,
  style = {},
}) {
  const teamLinks = links.filter((l) => l.scope === "team");
  const leagueLinks = links.filter((l) => l.scope !== "team");

  const renderLink = (l) => (
    <a
      key={l.label}
      href={l.href || "#"}
      onClick={l.onClick}
      className="nha-navlink"
      style={{
        position: "relative",
        padding: "7px 12px",
        fontFamily: "var(--font-body)",
        fontSize: "var(--text-sm)",
        fontWeight: l.active ? "var(--weight-bold)" : "var(--weight-medium)",
        color: l.active ? "#fff" : "rgba(255,255,255,0.62)",
        borderRadius: "var(--radius-sm)",
        background: l.active ? "rgba(255,255,255,0.08)" : "transparent",
        textDecoration: "none",
        transition: "color var(--dur-fast) var(--ease-out)",
      }}
    >
      {l.label}
    </a>
  );

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 18,
        height: "var(--topbar-h)",
        padding: mobile ? "0 14px" : "0 22px",
        background: "var(--ink-950)",
        borderBottom: "1px solid #000",
        color: "#fff",
        ...style,
      }}
    >
      <span
        onClick={onBrandClick}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 9,
          cursor: onBrandClick ? "pointer" : "default",
          flex: "none",
        }}
      >
        <span style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 30, height: 30, borderRadius: "var(--radius-sm)",
          background: "var(--green-500)", color: "var(--ink-950)",
          fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)",
          fontSize: "13px", letterSpacing: "-0.02em",
        }}>
          ◓
        </span>
        <span style={{
          fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)",
          fontSize: "var(--text-lg)", letterSpacing: "0.06em", color: "#fff",
        }}>
          {brand}
        </span>
      </span>

      {!mobile && (teamSwitcher || teamLinks.length > 0) && (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: teamSwitcher ? 6 : 4,
            padding: "4px 6px",
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.10)",
            borderRadius: "var(--radius-md)",
            flex: "none",
          }}
        >
          {teamSwitcher}
          {teamLinks.map(renderLink)}
        </div>
      )}

      {!mobile && (teamSwitcher || teamLinks.length > 0) && leagueLinks.length > 0 && (
        <span style={{ width: 1, height: 26, background: "rgba(255,255,255,0.14)", flex: "none" }} />
      )}

      {!mobile && leagueLinks.length > 0 && (
        <nav style={{ display: "flex", gap: 4 }}>
          {leagueLinks.map(renderLink)}
        </nav>
      )}

      <span style={{ flex: 1 }} />

      <NotificationBadge count={tradeRequests} tone="green">
        <span
          title="Trade requests"
          onClick={onBell}
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 34, height: 34, borderRadius: "var(--radius-sm)",
            background: "rgba(255,255,255,0.06)", color: "rgba(255,255,255,0.85)", cursor: "pointer",
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 1l4 4-4 4" /><path d="M3 11V9a4 4 0 0 1 4-4h14" />
            <path d="M7 23l-4-4 4-4" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
          </svg>
        </span>
      </NotificationBadge>

      {!mobile && email && (
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          fontSize: "var(--text-sm)", color: "rgba(255,255,255,0.62)", maxWidth: 220,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {commissioner && <span title="Commissioner" style={{ color: "var(--amber-600)" }}>★</span>}
          {email}
        </span>
      )}

      <button
        onClick={onSignOut}
        className="nha-btn"
        data-variant="ghost"
        style={{
          display: "inline-flex", alignItems: "center", height: 32, padding: "0 12px",
          fontFamily: "var(--font-body)", fontSize: "var(--text-sm)", fontWeight: "var(--weight-semibold)",
          color: "rgba(255,255,255,0.85)", background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.14)", borderRadius: "var(--radius-sm)", cursor: "pointer",
        }}
      >
        {mobile ? "Out" : "Sign out"}
      </button>
    </header>
  );
}
