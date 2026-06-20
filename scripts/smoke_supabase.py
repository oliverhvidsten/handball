"""
End-to-end smoke test against LIVE Supabase: mint a real Supabase access token
(password grant) and drive the FastAPI write endpoints with it, proving the full
chain -- real JWT -> JWKS (ES256) verification -> manager lookup -> authorization
-> DB write. Reads creds from .env (SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY,
SMOKE_EMAIL, SMOKE_PASSWORD).

Non-destructive: the lineup edit is reordered then restored; the trade is
proposed -> accepted -> rejected (never committed) and then deleted. Run:

    python -m scripts.smoke_supabase
"""
from __future__ import annotations

import os
import sys

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball.db import get_engine
from handball.pg_repository import PostgresTeamRepository

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def mint_token() -> str:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    r = httpx.post(
        f"{url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": os.environ["SUPABASE_PUBLISHABLE_KEY"], "Content-Type": "application/json"},
        json={"email": os.environ["SMOKE_EMAIL"], "password": os.environ["SMOKE_PASSWORD"]},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    engine = get_engine()
    repo = PostgresTeamRepository(engine)

    from api.main import app
    client = TestClient(app)

    token = mint_token()
    auth = {"Authorization": f"Bearer {token}"}

    # --- auth gate ---------------------------------------------------------
    check("health (no auth) -> 200", client.get("/health").status_code == 200)
    check("no token -> 401", client.put("/teams/Atlanta/arrangement", json={}).status_code == 401)
    check("garbage token -> 401",
          client.put("/teams/Atlanta/arrangement", json={},
                     headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401)

    # --- lineup edit on an OWNED team (reorder then restore) ---------------
    # the smoke user is commissioner and owns Boston/Seattle/Houston/Minneapolis.
    team = repo.load("Boston")
    arr = team.arrangement()

    def body(a):
        return {"starters": {p: list(i) for p, i in a.starters.items()},
                "bench": {p: list(i) for p, i in a.bench.items()},
                "reserves": list(a.reserves)}

    original = body(arr)
    reordered = body(arr)
    reordered["starters"]["Forward"] = list(reversed(reordered["starters"]["Forward"]))

    r1 = client.put("/teams/Boston/arrangement", json=reordered, headers=auth)
    check("real JWT: edit lineup -> 200", r1.status_code == 200, r1.text[:120])
    after = [p.id for p in repo.load("Boston").starters["Forward"]]
    check("lineup change persisted", after == reordered["starters"]["Forward"])
    r2 = client.put("/teams/Boston/arrangement", json=original, headers=auth)
    check("restore original lineup -> 200", r2.status_code == 200)

    def a_player(slug):
        with engine.connect() as c:
            return c.execute(text("select legacy_id from players where team_id="
                                  "(select id from teams where slug=:s) limit 1"),
                             {"s": slug}).scalar_one()

    def cleanup(trade_id):
        with engine.begin() as c:
            c.execute(text("delete from trades where id = cast(:t as uuid)"), {"t": trade_id})

    # --- EXTERNAL trade: Boston(owned) -> Atlanta(not owned) => proposed ----
    rp = client.post("/trades", headers=auth, json={
        "from_team": "Boston", "to_team": "Atlanta",
        "players_out": [a_player("Boston")], "players_in": [a_player("Atlanta")]})
    ok = rp.status_code == 200 and rp.json()["status"] == "proposed" and rp.json()["internal"] is False
    check("external trade -> proposed (not internal)", ok, rp.text[:140])
    if rp.status_code == 200:
        tid = rp.json()["trade_id"]
        ra = client.post(f"/trades/{tid}/accept", headers=auth)   # commissioner bypass
        check("external accept -> accepted", ra.status_code == 200 and ra.json()["status"] == "accepted", ra.text[:120])
        cleanup(tid)

    # --- INTERNAL trade: Boston -> Seattle (both owned) => auto-accepted ----
    # We verify auto-accept then CANCEL (no commit) so the live rosters stay
    # pristine; the approve->committed path is covered by pytest on a disposable DB.
    rp = client.post("/trades", headers=auth, json={
        "from_team": "Boston", "to_team": "Seattle",
        "players_out": [a_player("Boston")], "players_in": [a_player("Seattle")]})
    ok = rp.status_code == 200 and rp.json()["status"] == "accepted" and rp.json()["internal"] is True
    check("internal trade -> accepted (skips counterparty)", ok, rp.text[:140])
    if rp.status_code == 200 and rp.json().get("internal"):
        tid = rp.json()["trade_id"]
        rc = client.post(f"/trades/{tid}/cancel", headers=auth)
        check("internal cancel -> cancelled", rc.status_code == 200 and rc.json()["status"] == "cancelled", rc.text[:120])
        cleanup(tid)

    # --- report ------------------------------------------------------------
    print()
    for status, name, detail in results:
        print(f"  [{status}] {name}" + (f"   {detail}" if status == FAIL and detail else ""))
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
