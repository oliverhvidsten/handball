"""
Name: cap_compliance_swaps.py
Description: Bring every team under the hard cap by pairing the over-cap teams with
             the lowest-payroll teams and swapping same-position players so that
             salary flows from the expensive roster to the cheap one.

             Pairing: over-cap teams sorted by payroll (highest first) are matched
             one-to-one with the same number of lowest-payroll teams sorted
             ascending (lowest first). Highest meets lowest, and so on.

             Swaps: within a pair, a swap is one player from the high team for one
             player of the SAME position from the low team who earns less. Position
             is held fixed so both rosters stay arrangeable (3/3/3/1 + 2/2/2/1). The
             picker is greedy on money: if any single swap clears the remaining
             excess, take the one that overshoots least; otherwise take the biggest
             saving available and go again. Players move at most once. The low team
             must stay under the hard cap after every swap.

             Each pair's swaps are ONE trade through handball/trade_service
             (propose -> accept -> approve), so the move is logged in `trades`, both
             lineups are rebuilt by the canonical rules, and the hard-cap rule is
             re-checked on the real rows at approve time. Dry run by default.
Author: commissioner tooling

Usage:
    python scripts/cap_compliance_swaps.py             # plan only, writes nothing
    python scripts/cap_compliance_swaps.py --apply     # execute the trades
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text

from handball import trade_service as ts
from handball.db import db_url, get_engine
from handball.simulation_vars import HARD_CAP


@dataclass
class P:
    legacy_id: str
    name: str
    position: str
    salary: int
    term: int
    years: int
    rating: float


@dataclass
class T:
    slug: str
    name: str
    payroll: int
    players: list[P]


def load(engine) -> list[T]:
    sql = text(
        "select t.slug, t.name, p.legacy_id, p.name as pname, p.position::text as pos, "
        "p.contract_value, p.contract_term, p.years_remaining, "
        "case when p.position = 'Goalie' then p.goalie_skill "
        "     else (p.offense + p.defense) / 2.0 end as rating "
        "from teams t join players p on p.team_id = t.id and p.retired = false "
        "order by t.slug, p.position, p.contract_value desc")
    by_team: dict[str, T] = {}
    with engine.connect() as c:
        for r in c.execute(sql).mappings():
            t = by_team.setdefault(r["slug"], T(r["slug"], r["name"], 0, []))
            t.players.append(P(r["legacy_id"], r["pname"], r["pos"], int(r["contract_value"]),
                               int(r["contract_term"]), int(r["years_remaining"]),
                               float(r["rating"] or 0)))
            t.payroll += int(r["contract_value"])
    return list(by_team.values())


def pair_up(teams: list[T]) -> list[tuple[T, T]]:
    over = sorted((t for t in teams if t.payroll > HARD_CAP), key=lambda t: (-t.payroll, t.name))
    low = sorted(teams, key=lambda t: (t.payroll, t.name))[: len(over)]
    return list(zip(over, low))


def plan_pair(high: T, low: T) -> list[tuple[P, P]]:
    """Same-position swaps (high_player -> low team, low_player -> high team) until
    `high` is at or under the hard cap. Greedy on money, see the module docstring."""
    swaps: list[tuple[P, P]] = []
    used: set[str] = set()
    hp, lp = high.payroll, low.payroll
    by_pos_low: dict[str, list[P]] = defaultdict(list)
    for p in low.players:
        by_pos_low[p.position].append(p)

    while hp > HARD_CAP:
        excess = hp - HARD_CAP
        candidates: list[tuple[int, P, P]] = []
        for a in high.players:
            if a.legacy_id in used:
                continue
            for b in by_pos_low[a.position]:
                if b.legacy_id in used:
                    continue
                delta = a.salary - b.salary
                if delta <= 0 or lp + delta > HARD_CAP:
                    continue
                candidates.append((delta, a, b))
        if not candidates:
            raise SystemExit(f"{high.name}: no legal same-position swap left with {low.name} "
                             f"(still ${excess}M over)")
        enough = [c for c in candidates if c[0] >= excess]
        if enough:
            # finish now, overshooting as little as possible; among equal deltas,
            # prefer the pair closest in ability so the swap is least lopsided
            delta, a, b = min(enough, key=lambda c: (c[0], abs(c[1].rating - c[2].rating)))
        else:
            delta, a, b = max(candidates, key=lambda c: (c[0], -abs(c[1].rating - c[2].rating)))
        swaps.append((a, b))
        used.update({a.legacy_id, b.legacy_id})
        hp -= delta
        lp += delta
    return swaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute the trades (default: plan only)")
    args = ap.parse_args()

    engine = get_engine()
    print(f"database: {db_url().split('@')[-1]}")
    teams = load(engine)
    pairs = pair_up(teams)
    if not pairs:
        print("no team is over the hard cap; nothing to do")
        return 0

    plans = []
    for high, low in pairs:
        swaps = plan_pair(high, low)
        moved = sum(a.salary - b.salary for a, b in swaps)
        plans.append((high, low, swaps, moved))
        print(f"\n{high.name} (${high.payroll}M) <-> {low.name} (${low.payroll}M): "
              f"{len(swaps)} swap(s), ${moved}M moves -> "
              f"{high.name} ${high.payroll - moved}M, {low.name} ${low.payroll + moved}M")
        for a, b in swaps:
            print(f"  {a.position:<10} {a.name:<24} ${a.salary:>2}M {a.term}y/{a.years}left  rating {a.rating:4.1f}"
                  f"   <->   {b.name:<24} ${b.salary:>2}M {b.term}y/{b.years}left  rating {b.rating:4.1f}")

    if not args.apply:
        print("\ndry run: nothing written. Re-run with --apply to execute.")
        return 0

    print("\napplying...")
    for high, low, swaps, moved in plans:
        trade_id = ts.propose_trade(
            engine, high.slug, low.slug,
            players_out=[a.legacy_id for a, _ in swaps],
            players_in=[b.legacy_id for _, b in swaps],
            proposed_by=None, internal=False,
        )
        ts.accept_trade(engine, trade_id)
        ts.approve_trade(engine, trade_id)
        print(f"  trade {trade_id}: {high.name} -> {low.name} approved (${moved}M)")

    with engine.connect() as c:
        rows = c.execute(text(
            "select t.name, sum(p.contract_value) from teams t join players p "
            "on p.team_id = t.id and p.retired = false group by 1 having sum(p.contract_value) > :cap"),
            {"cap": HARD_CAP}).all()
    print("\nteams still over the hard cap:", rows or "none")
    return 0 if not rows else 1


if __name__ == "__main__":
    sys.exit(main())
