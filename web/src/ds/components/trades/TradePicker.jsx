import React from "react";
import { Select } from "../forms/Select.jsx";
import { Checkbox } from "../forms/Checkbox.jsx";
import { Button } from "../forms/Button.jsx";
import { Tag } from "../forms/Tag.jsx";

const POS_TONE = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

function AssetList({ items = [], selected = [], onToggle, empty, renderLabel, renderTag }) {
  if (items.length === 0) {
    return <div style={{ padding: "20px 12px", textAlign: "center", fontSize: "var(--text-sm)", color: "var(--muted)" }}>{empty}</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {items.map((item) => {
        const on = selected.includes(item.id);
        return (
          <label
            key={item.id}
            style={{
              display: "flex", alignItems: "center", gap: 9, padding: "7px 4px", cursor: "pointer",
              borderBottom: "1px solid var(--line)",
            }}
          >
            <Checkbox checked={on} onChange={() => onToggle(item.id)} />
            <span style={{ flex: 1, fontSize: "var(--text-sm)", fontWeight: on ? 600 : 400, color: "var(--text-body)" }}>
              {renderLabel(item)}
            </span>
            {renderTag && renderTag(item)}
          </label>
        );
      })}
    </div>
  );
}

const subHead = {
  fontFamily: "var(--font-body)", fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)",
  letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)",
  margin: "12px 0 4px",
};

/**
 * NHA TradePicker — the propose-a-trade builder. Team selector + two
 * side-by-side checkbox lists ("You send" / "You receive"), each covering
 * both players and draft picks + Propose.
 */
export function TradePicker({
  myTeam = "Your team",
  teamOptions = [],
  toTeam = "",
  onToTeam,
  myPlayers = [],
  theirPlayers = [],
  out = [],
  inn = [],
  onToggleOut,
  onToggleIn,
  myPicks = [],
  theirPicks = [],
  picksOut = [],
  picksIn = [],
  onTogglePickOut,
  onTogglePickIn,
  onPropose,
  busy = false,
  proposeLabel = "Propose trade",
  style = {},
}) {
  const canPropose = toTeam && (out.length > 0 || inn.length > 0 || picksOut.length > 0 || picksIn.length > 0) && !busy;
  const colHead = {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "0 0 8px", marginBottom: 4, borderBottom: "2px solid var(--line-strong)",
  };
  const colTitle = {
    fontFamily: "var(--font-body)", fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)",
    letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)",
  };
  const pickLabel = (p) => `${p.originalTeam} Round ${p.round}`;
  const pickTag = (p) => <Tag tone="neutral" size="sm">{p.season}</Tag>;
  return (
    <div
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        padding: 18,
        boxShadow: "var(--shadow-sm)",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-end", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 220 }}>
          <Select label="Trade with" value={toTeam} onChange={onToTeam ? (e) => onToTeam(e.target.value) : undefined}
            options={[{ value: "", label: "— select team —" }, ...teamOptions]} />
        </div>
      </div>

      {toTeam ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <div>
            <div style={colHead}>
              <span style={colTitle}>You send · {myTeam}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-2xs)", color: "var(--green-700)" }}>
                {out.length + picksOut.length} selected
              </span>
            </div>
            <AssetList items={myPlayers} selected={out} onToggle={onToggleOut} empty="No players."
              renderLabel={(p) => p.name} renderTag={(p) => <Tag tone={POS_TONE[p.position] || "neutral"} size="sm">{p.position}</Tag>} />
            <h5 style={subHead}>Draft picks</h5>
            <AssetList items={myPicks} selected={picksOut} onToggle={onTogglePickOut} empty="No draft picks."
              renderLabel={pickLabel} renderTag={pickTag} />
          </div>
          <div>
            <div style={colHead}>
              <span style={colTitle}>You receive</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-2xs)", color: "var(--blue-text)" }}>
                {inn.length + picksIn.length} selected
              </span>
            </div>
            <AssetList items={theirPlayers} selected={inn} onToggle={onToggleIn} empty="Pick a team first."
              renderLabel={(p) => p.name} renderTag={(p) => <Tag tone={POS_TONE[p.position] || "neutral"} size="sm">{p.position}</Tag>} />
            <h5 style={subHead}>Draft picks</h5>
            <AssetList items={theirPicks} selected={picksIn} onToggle={onTogglePickIn} empty="No draft picks."
              renderLabel={pickLabel} renderTag={pickTag} />
          </div>
        </div>
      ) : (
        <div style={{ padding: "28px 12px", textAlign: "center", fontSize: "var(--text-sm)", color: "var(--muted)",
          background: "var(--surface-2)", border: "1px dashed var(--line-strong)", borderRadius: "var(--radius-md)" }}>
          Select a team to start building a trade.
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
        <Button variant="primary" disabled={!canPropose} onClick={onPropose}>
          {busy ? "Proposing…" : proposeLabel}
        </Button>
      </div>
    </div>
  );
}
