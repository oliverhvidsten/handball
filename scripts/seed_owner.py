"""
Assign team ownership (teams.owner_id) to the commissioner user, so the
multi-team UI has data. Idempotent. Reads SMOKE_EMAIL from .env to find the
auth user; defaults to the kit's four teams (Boston/Seattle/Houston/Minneapolis).

    python -m scripts.seed_owner                       # default 4 teams
    python -m scripts.seed_owner Boston Denver Phoenix  # explicit teams
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import text

from handball.db import get_engine

DEFAULT_TEAMS = ["Boston", "Seattle", "Houston", "Minneapolis"]


def main(argv: list[str]) -> int:
    teams = argv or DEFAULT_TEAMS
    email = os.environ.get("SMOKE_EMAIL")
    if not email:
        print("SMOKE_EMAIL not set in .env"); return 1

    engine = get_engine()
    with engine.begin() as c:
        uid = c.execute(
            text("select id from auth.users where email = :e"), {"e": email}
        ).scalar_one_or_none()
        if uid is None:
            print(f"no auth user for {email!r}"); return 1
        # ensure the manager row exists and is commissioner
        c.execute(
            text("insert into managers (user_id, role, display_name) "
                 "values (:u, 'commissioner', 'Commissioner') "
                 "on conflict (user_id) do update set role='commissioner'"),
            {"u": uid},
        )
        owned = []
        for slug in teams:
            res = c.execute(
                text("update teams set owner_id = :u where slug = :s returning slug"),
                {"u": uid, "s": slug},
            ).scalar_one_or_none()
            if res:
                owned.append(res)
            else:
                print(f"  (no team {slug!r}, skipped)")
    print(f"commissioner {email} now owns: {owned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
