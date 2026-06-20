import React from "react";

/**
 * NHA DataTable — standings & scoring-leaders table with zebra rows,
 * hover highlight, a rank column and right-aligned numeric stat columns.
 *
 * columns: [{ key, header, align?, numeric?, width?, render?(row, i) }]
 */
export function DataTable({
  columns = [],
  rows = [],
  rank = false,
  zebra = true,
  getRowKey = (_r, i) => i,
  onRowClick = null,
  style = {},
}) {
  const th = {
    textAlign: "left",
    padding: "9px 12px",
    fontFamily: "var(--font-body)",
    fontSize: "var(--text-xs)",
    fontWeight: "var(--weight-bold)",
    letterSpacing: "var(--tracking-wide)",
    textTransform: "uppercase",
    color: "var(--muted)",
    borderBottom: "1px solid var(--line-strong)",
    background: "var(--surface-card)",
    position: "sticky",
    top: 0,
  };
  const td = {
    padding: "10px 12px",
    fontSize: "var(--text-sm)",
    color: "var(--text-body)",
    borderBottom: "1px solid var(--line)",
    verticalAlign: "middle",
  };
  const numStyle = { textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: "var(--weight-semibold)" };
  const rankStyle = { width: 44, textAlign: "right", color: "var(--muted)", fontFamily: "var(--font-mono)", fontWeight: "var(--weight-bold)" };

  return (
    <table
      className="nha-table"
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontFeatureSettings: "var(--nums-tabular)",
        ...style,
      }}
    >
      <thead>
        <tr>
          {rank && <th style={{ ...th, ...rankStyle, color: "var(--muted)" }}>#</th>}
          {columns.map((c) => (
            <th key={c.key} style={{ ...th, textAlign: c.numeric || c.align === "right" ? "right" : "left", width: c.width }}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr
            key={getRowKey(row, i)}
            className="nha-row"
            onClick={onRowClick ? () => onRowClick(row, i) : undefined}
            style={{
              background: zebra && i % 2 === 1 ? "var(--surface-zebra)" : "var(--surface-card)",
              cursor: onRowClick ? "pointer" : "default",
              transition: "background var(--dur-fast) var(--ease-out)",
            }}
          >
            {rank && <td style={{ ...td, ...rankStyle }}>{i + 1}</td>}
            {columns.map((c) => (
              <td
                key={c.key}
                style={{ ...td, ...(c.numeric || c.align === "right" ? numStyle : null) }}
              >
                {c.render ? c.render(row, i) : row[c.key]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
