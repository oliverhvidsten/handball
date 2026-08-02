"""
Assign team ownership to managers from a mapping file. For each line, ensure a
`managers` row exists for the auth user (matched by email), then point each listed
team's `teams.owner_id` at that user.

    python -m scripts.seed_managers --file scripts/manager_assignments_2026.txt
    python -m scripts.seed_managers --file scripts/manager_assignments_2026.txt --dry-run

Mapping file format (see scripts/manager_assignments_2026.txt for the template):

    email | Display Name | Team, Team, ... | role

`role` is optional and defaults to 'manager'; set it to 'commissioner' for league
admins. The file is authoritative for role: a listed manager's role is written on
both insert AND update, so anyone you DON'T mark commissioner becomes a manager.

The whole run is ONE transaction: any error (unknown email, unknown team slug, a
team claimed by two managers, or a bad role) writes nothing. Idempotent -- re-running
with the same file is a no-op. A team listed here that is currently owned by someone
else is re-pointed to the new manager (with a warning); teams NOT listed are left
untouched.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from handball.db import get_engine


def parse_mapping(path: str) -> list[tuple[str, str, list[str], str]]:
    """Parse 'email | name | teams [| role]' lines into (email, name, [slugs], role)."""
    rows: list[tuple[str, str, list[str], str]] = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) not in (3, 4):
                raise ValueError(f"line {lineno}: expected 'email | name | teams [| role]', got {line!r}")
            email, name, teams_str = parts[0], parts[1], parts[2]
            role = (parts[3] if len(parts) == 4 else "") or "manager"
            if not email:
                raise ValueError(f"line {lineno}: missing email")
            if role not in ("manager", "commissioner"):
                raise ValueError(f"line {lineno}: role must be 'manager' or 'commissioner', got {role!r}")
            teams = [t.strip() for t in teams_str.split(",") if t.strip()]
            rows.append((email, name or email, teams, role))
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Assign team ownership to managers from a mapping file.")
    ap.add_argument("--file", required=True, help="mapping file (email | name | teams)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    args = ap.parse_args(argv)

    mapping = parse_mapping(args.file)

    # A team owned by exactly one manager: reject duplicates up front.
    claimed: dict[str, str] = {}
    for email, _name, teams, _role in mapping:
        for slug in teams:
            if slug in claimed and claimed[slug] != email:
                print(f"team {slug!r} is claimed by both {claimed[slug]} and {email}"); return 1
            claimed[slug] = email

    engine = get_engine()
    with engine.begin() as c:
        valid_slugs = {r[0] for r in c.execute(text("select slug from teams")).all()}
        unknown_teams = sorted(set(claimed) - valid_slugs)
        if unknown_teams:
            print(f"unknown team slugs: {unknown_teams}"); return 1

        plan: list[str] = []
        for email, name, teams, role in mapping:
            uid = c.execute(
                text("select id from auth.users where email = :e"), {"e": email}
            ).scalar_one_or_none()
            if uid is None:
                print(f"no auth user for {email!r} -- create the account first"); return 1

            if not args.dry_run:
                c.execute(
                    text("insert into managers (user_id, role, display_name) "
                         "values (:u, :r, :n) "
                         "on conflict (user_id) do update set "
                         "display_name = excluded.display_name, role = excluded.role"),
                    {"u": uid, "r": role, "n": name},
                )
            for slug in teams:
                prev = c.execute(
                    text("select u.email from teams t left join auth.users u on u.id = t.owner_id "
                         "where t.slug = :s"), {"s": slug},
                ).scalar_one_or_none()
                if prev and prev != email:
                    print(f"  WARNING: {slug!r} currently owned by {prev}, re-pointing to {email}")
                if not args.dry_run:
                    c.execute(
                        text("update teams set owner_id = :u where slug = :s"),
                        {"u": uid, "s": slug},
                    )
            tag = " [commissioner]" if role == "commissioner" else ""
            plan.append(f"{name} <{email}>{tag} -> {teams or '(no teams)'}")

        if args.dry_run:
            print("DRY RUN -- nothing written:")
            for p in plan:
                print(f"  {p}")
            raise _Rollback

    for p in plan:
        print(p)
    print(f"done: {len(mapping)} managers processed.")
    return 0


class _Rollback(Exception):
    """Raised to abort the transaction in --dry-run mode."""


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except _Rollback:
        raise SystemExit(0)
