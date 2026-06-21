import React, { useEffect, useRef, useState } from "react";

/**
 * NHA Menu — a compact popover anchored to a trigger, used for per-row actions.
 * `trigger` is rendered as the toggle; `items` is a list of
 * { label, onClick, disabled?, tone?, divider? }. Closes on outside-click,
 * Escape, or after an enabled item runs.
 */
export function Menu({ trigger, items = [], align = "right", style = {} }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = (item) => {
    if (item.disabled) return;
    item.onClick?.();
    setOpen(false);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-flex", ...style }}>
      <span
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        style={{ display: "inline-flex" }}
      >
        {trigger}
      </span>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            [align]: 0,
            zIndex: 60,
            minWidth: 168,
            padding: 4,
            background: "var(--surface-card)",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          {items.map((item, i) =>
            item.divider ? (
              <div key={`d${i}`} style={{ height: 1, background: "var(--line)", margin: "4px 0" }} />
            ) : (
              <button
                key={i}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => run(item)}
                className="nha-menuitem"
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "7px 10px",
                  background: "none",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--text-sm)",
                  fontWeight: "var(--weight-medium)",
                  color: item.disabled
                    ? "var(--muted)"
                    : item.tone === "danger"
                    ? "var(--red-text)"
                    : "var(--text-body)",
                  cursor: item.disabled ? "not-allowed" : "pointer",
                  opacity: item.disabled ? 0.55 : 1,
                  whiteSpace: "nowrap",
                }}
              >
                {item.label}
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}
