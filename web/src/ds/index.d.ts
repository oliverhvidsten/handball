// Type surface for the vendored NHA design-system barrel. The components are
// authored in JS with default-valued props, which TS would otherwise infer as
// `never[]`/`null`; these permissive declarations let the strict TS pages use
// them. Props are intentionally loose (the kit is the source of truth).
import * as React from "react";

type AnyProps = Record<string, any>;
type C = React.FC<AnyProps>;

export const Button: C;
export const Input: C;
export const Select: C;
export const Tabs: C;
export const Tag: C;
export const Checkbox: C;
export const IconButton: C;

export const TopNav: C;
export const TeamSwitcher: C;
export const NotificationBadge: C;

export const StatCard: C;
export const StatChip: C;
export const StatusPill: C;
export const DataTable: C;
export const BoxScore: C;

export const TeamCard: C;
export const RosterColumns: C;
export const PlayerRow: C;

export const TradeRow: C;
export const TradePicker: C;

export const Alert: C;
export const EmptyState: C;
export const Toast: C;

export const DraftOrderRow: C;
export const AuthCard: C;
