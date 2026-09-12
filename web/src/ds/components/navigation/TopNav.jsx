import React, { useEffect, useRef, useState } from "react";
import { NotificationBadge } from "./NotificationBadge.jsx";

/**
 * NHA TopNav — league app header. Brand mark, primary nav links, optional
 * trade-request bell with a count badge, signed-in email (commissioner star),
 * and Sign-out.
 *
 * links: [{ label, href, onClick, active?, scope?, group? }]
 *   scope "team" links sit in the pill with the TeamSwitcher; everything else is a
 *   league link. A league link with a `group` is folded into a dropdown of that
 *   name (groups appear in first-seen order, after the ungrouped league links).
 *
 * The bar never wraps or overflows. It has three layouts, chosen from the viewport
 * width (or forced with `mobile`):
 *   wide     (>= 1440px)  everything inline, email shown
 *   compact  (>= 1100px)  the ungrouped league links fold into the "League" menu,
 *                         the commissioner link shrinks to its star, and the email
 *                         collapses to an avatar
 *   mobile   (<  1100px)  brand + switcher + bell + a Menu button that opens a
 *                         full-width panel listing every link, grouped
 */
const WIDE_MIN = 1440;
const COMPACT_MIN = 1100;

function useViewportWidth() {
  const get = () => (typeof window === "undefined" ? WIDE_MIN : window.innerWidth);
  const [w, setW] = useState(get);
  useEffect(() => {
    const onResize = () => setW(get());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return w;
}

/** Close-on-outside-click / Escape for a popover rooted at `ref`. */
function useDismiss(ref, open, close) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [ref, open, close]);
}

const linkStyle = (active) => ({
  position: "relative",
  padding: "7px 12px",
  fontFamily: "var(--font-body)",
  fontSize: "var(--text-sm)",
  fontWeight: active ? "var(--weight-bold)" : "var(--weight-medium)",
  color: active ? "#fff" : "rgba(255,255,255,0.62)",
  borderRadius: "var(--radius-sm)",
  background: active ? "rgba(255,255,255,0.08)" : "transparent",
  textDecoration: "none",
  whiteSpace: "nowrap",
  flex: "none",
  transition: "color var(--dur-fast) var(--ease-out)",
});

const chromeButton = {
  display: "inline-flex", alignItems: "center", height: 32, padding: "0 12px",
  fontFamily: "var(--font-body)", fontSize: "var(--text-sm)", fontWeight: "var(--weight-semibold)",
  color: "rgba(255,255,255,0.85)", background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.14)", borderRadius: "var(--radius-sm)", cursor: "pointer",
  whiteSpace: "nowrap", flex: "none",
};

const panelStyle = {
  background: "var(--surface-card)",
  border: "1px solid var(--line)",
  borderRadius: "var(--radius-md)",
  boxShadow: "var(--shadow-lg)",
  zIndex: 60,
};

const sectionHead = {
  padding: "8px 12px 4px", fontSize: "var(--text-2xs)", fontWeight: 700,
  letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)",
};

function NavLink({ l, onNavigate, title }) {
  return (
    <a
      href={l.href || "#"}
      onClick={(e) => { l.onClick?.(e); onNavigate?.(); }}
      className="nha-navlink"
      aria-current={l.active ? "page" : undefined}
      title={title}
      aria-label={title}
      style={linkStyle(l.active)}
    >
      {l.label}
    </a>
  );
}

/** A light-surface row inside a dropdown / the mobile panel. */
function PanelLink({ l, onNavigate }) {
  return (
    <a
      href={l.href || "#"}
      onClick={(e) => { l.onClick?.(e); onNavigate?.(); }}
      className="nha-menuitem"
      aria-current={l.active ? "page" : undefined}
      style={{
        display: "block", padding: "8px 12px", borderRadius: "var(--radius-sm)",
        fontFamily: "var(--font-body)", fontSize: "var(--text-sm)",
        fontWeight: l.active ? "var(--weight-bold)" : "var(--weight-medium)",
        color: l.active ? "var(--green-700, var(--text-body))" : "var(--text-body)",
        background: l.active ? "var(--green-50)" : "transparent",
        textDecoration: "none", whiteSpace: "nowrap",
      }}
    >
      {l.label}
    </a>
  );
}

/** A dropdown of league links under one header. Lit when a child is the page. */
function NavMenu({ label, links }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDismiss(ref, open, () => setOpen(false));
  const active = links.some((l) => l.active);
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex", flex: "none" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="nha-navlink"
        style={{
          ...linkStyle(active || open),
          border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5,
          background: open ? "rgba(255,255,255,0.12)" : linkStyle(active).background,
        }}
      >
        {label}
        <span aria-hidden style={{
          fontSize: 10, opacity: 0.7,
          transform: open ? "rotate(180deg)" : "none", transition: "transform var(--dur-fast)",
        }}>▾</span>
      </button>
      {open && (
        <div role="menu" style={{ ...panelStyle, position: "absolute", top: "calc(100% + 6px)", left: 0, minWidth: 180, padding: 4 }}>
          {links.map((l) => <PanelLink key={l.label} l={l} onNavigate={() => setOpen(false)} />)}
        </div>
      )}
    </div>
  );
}

const isCommish = (l) => /Commissioner/.test(l.label);

/**
 * Split the league links into what renders flat and what renders as menus.
 * `fold` (compact + mobile) moves the ungrouped links into a "League" group, first,
 * so the bar shortens without any page becoming harder to reach than one click.
 * The commissioner link is always kept apart: it is a role, not a section.
 */
function partition(leagueLinks, fold) {
  const flat = [];
  const groups = [];
  let commish = null;
  for (const l of leagueLinks) {
    if (isCommish(l)) { commish = l; continue; }
    if (!l.group) { flat.push(l); continue; }
    let g = groups.find((x) => x.label === l.group);
    if (!g) { g = { label: l.group, links: [] }; groups.push(g); }
    g.links.push(l);
  }
  if (fold && flat.length) {
    let g = groups.find((x) => x.label === "League");
    if (!g) { g = { label: "League", links: [] }; groups.unshift(g); }
    g.links.unshift(...flat);
    return { flat: [], groups, commish };
  }
  return { flat, groups, commish };
}

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
  onAccount,
  mobile = false,
  style = {},
}) {
  const width = useViewportWidth();
  const layout = mobile || width < COMPACT_MIN ? "mobile" : width < WIDE_MIN ? "compact" : "wide";
  const isMobile = layout === "mobile";

  const teamLinks = links.filter((l) => l.scope === "team");
  const leagueLinks = links.filter((l) => l.scope !== "team");
  const { flat, groups, commish } = partition(leagueLinks, layout !== "wide");

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  useDismiss(menuRef, menuOpen, () => setMenuOpen(false));
  useEffect(() => { if (!isMobile) setMenuOpen(false); }, [isMobile]);

  const accountEl = email && (
    <span
      onClick={onAccount}
      title={onAccount ? `${email} — account settings` : email}
      className={onAccount ? "nha-navlink" : undefined}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5, flex: "none",
        padding: onAccount ? "5px 9px" : 0,
        borderRadius: "var(--radius-sm)",
        fontSize: "var(--text-sm)", color: "rgba(255,255,255,0.62)",
        maxWidth: layout === "wide" ? 220 : "none",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        cursor: onAccount ? "pointer" : "default",
      }}
    >
      {layout === "wide" ? (
        <>
          {commissioner && <span title="Commissioner" style={{ color: "var(--amber-600)" }}>★</span>}
          {email}
        </>
      ) : (
        // Compact: the initial in a ring. A commissioner's ring is amber, which is
        // the same signal the star carries at full width.
        <span aria-label={email} style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 26, height: 26, borderRadius: "50%", background: "rgba(255,255,255,0.14)",
          border: commissioner ? "2px solid var(--amber-600)" : "2px solid transparent",
          color: "#fff", fontWeight: 700, fontSize: 11, textTransform: "uppercase",
        }}>{email.trim().charAt(0)}</span>
      )}
    </span>
  );

  const bell = (
    <NotificationBadge count={tradeRequests} tone="green">
      <span
        title="Trade requests"
        onClick={onBell}
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "none",
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
  );

  return (
    <div ref={menuRef} style={{ position: "relative", zIndex: 50 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "nowrap",
          gap: isMobile ? 10 : 14,
          height: "var(--topbar-h)",
          padding: isMobile ? "0 14px" : "0 22px",
          background: "var(--ink-950)",
          borderBottom: "1px solid #000",
          color: "#fff",
          minWidth: 0,
          ...style,
        }}
      >
        <span
          onClick={onBrandClick}
          style={{ display: "inline-flex", alignItems: "center", gap: 9, cursor: onBrandClick ? "pointer" : "default", flex: "none" }}
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
          {!isMobile && (
            <span style={{
              fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)",
              fontSize: "var(--text-lg)", letterSpacing: "0.06em", color: "#fff",
            }}>
              {brand}
            </span>
          )}
        </span>

        {(teamSwitcher || (!isMobile && teamLinks.length > 0)) && (
          <div
            style={{
              display: "inline-flex", alignItems: "center", flex: "none", minWidth: 0,
              gap: teamSwitcher ? 6 : 4,
              padding: "4px 6px",
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.10)",
              borderRadius: "var(--radius-md)",
            }}
          >
            {teamSwitcher}
            {!isMobile && teamLinks.map((l) => <NavLink key={l.label} l={l} />)}
          </div>
        )}

        {!isMobile && (teamSwitcher || teamLinks.length > 0) && leagueLinks.length > 0 && (
          <span style={{ width: 1, height: 26, background: "rgba(255,255,255,0.14)", flex: "none" }} />
        )}

        {!isMobile && leagueLinks.length > 0 && (
          <nav aria-label="League" style={{ display: "flex", alignItems: "center", gap: 4, flex: "none" }}>
            {flat.map((l) => <NavLink key={l.label} l={l} />)}
            {groups.map((g) => <NavMenu key={g.label} label={g.label} links={g.links} />)}
            {commish && (layout === "wide"
              ? <NavLink l={commish} />
              : <NavLink l={{ ...commish, label: "★" }} title="Commissioner" />)}
          </nav>
        )}

        <span style={{ flex: 1, minWidth: 0 }} />

        {bell}

        {!isMobile && accountEl}

        {isMobile ? (
          <button
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Menu"
            className="nha-btn"
            data-variant="ghost"
            style={{ ...chromeButton, gap: 7, padding: "0 10px" }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              {menuOpen ? <><path d="M6 6l12 12" /><path d="M18 6L6 18" /></> : <><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></>}
            </svg>
            Menu
          </button>
        ) : (
          <button onClick={onSignOut} className="nha-btn" data-variant="ghost" style={chromeButton}>
            Sign out
          </button>
        )}
      </header>

      {isMobile && menuOpen && (
        <div
          role="menu"
          style={{
            ...panelStyle,
            position: "absolute", left: 10, right: 10, top: "calc(100% + 6px)",
            padding: "6px 6px 8px", maxHeight: "calc(100vh - var(--topbar-h) - 16px)", overflowY: "auto",
          }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "4px 12px" }}>
            {teamLinks.length > 0 && (
              <div>
                <div style={sectionHead}>Your team</div>
                {teamLinks.map((l) => <PanelLink key={l.label} l={l} onNavigate={() => setMenuOpen(false)} />)}
              </div>
            )}
            {groups.map((g) => (
              <div key={g.label}>
                <div style={sectionHead}>{g.label}</div>
                {g.links.map((l) => <PanelLink key={l.label} l={l} onNavigate={() => setMenuOpen(false)} />)}
              </div>
            ))}
          </div>
          {commish && (
            <>
              <div style={{ height: 1, background: "var(--line)", margin: "6px 4px" }} />
              <PanelLink l={commish} onNavigate={() => setMenuOpen(false)} />
            </>
          )}
          <div style={{ height: 1, background: "var(--line)", margin: "6px 4px" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 6px 0" }}>
            {email && (
              <button
                onClick={() => { onAccount?.(); setMenuOpen(false); }}
                className="nha-menuitem"
                style={{
                  flex: 1, minWidth: 0, textAlign: "left", padding: "8px 10px", border: "none",
                  background: "none", borderRadius: "var(--radius-sm)", cursor: onAccount ? "pointer" : "default",
                  fontFamily: "var(--font-body)", fontSize: "var(--text-sm)", color: "var(--text-body)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}
              >
                {commissioner && <span title="Commissioner" style={{ color: "var(--amber-600)", marginRight: 5 }}>★</span>}
                {email}
              </button>
            )}
            <button
              onClick={() => { onSignOut?.(); setMenuOpen(false); }}
              className="nha-btn"
              data-variant="ghost"
              style={{
                ...chromeButton, height: 32, color: "var(--text-body)",
                background: "var(--surface-hover, transparent)", border: "1px solid var(--line)",
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
