"""
Auth dependency: resolve the calling manager from a Supabase-issued JWT.

This project signs auth tokens with ASYMMETRIC keys, so we verify them against the
project's public JWKS endpoint ($SUPABASE_URL/auth/v1/.well-known/jwks.json) rather
than a shared secret -- the server never holds signing material. The verified
`sub` (the auth.users id) is looked up in the managers table. Tests override the
get_current_manager dependency with a stub, so no Supabase is needed to exercise
the endpoints.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import text

from handball.db import get_engine


@dataclass
class Manager:
    user_id: str
    owned_team_ids: list[str]   # team uuids this manager owns (teams.owner_id)
    role: str                   # 'manager' | 'commissioner'

    @property
    def is_commissioner(self) -> bool:
        return self.role == "commissioner"

    def owns(self, team_id: str | None) -> bool:
        return team_id is not None and team_id in self.owned_team_ids


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    base = os.environ.get("SUPABASE_URL")
    if not base:
        raise HTTPException(status_code=503, detail="auth not configured (SUPABASE_URL unset)")
    # PyJWKClient caches fetched keys and refreshes on unknown kid.
    return PyJWKClient(f"{base.rstrip('/')}/auth/v1/.well-known/jwks.json")


def _decode_sub(token: str) -> str:
    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token, signing_key.key, algorithms=["ES256", "RS256"], audience="authenticated"
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="token has no subject")
    return sub


def get_current_manager(authorization: str | None = Header(default=None)) -> Manager:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    user_id = _decode_sub(authorization.split(" ", 1)[1])
    with get_engine().connect() as conn:
        row = conn.execute(
            text("select user_id, role from managers where user_id = cast(:u as uuid)"),
            {"u": user_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=403, detail="not a registered manager")
        owned = conn.execute(
            text("select id from teams where owner_id = cast(:u as uuid)"),
            {"u": user_id},
        ).scalars().all()
    return Manager(
        user_id=str(row["user_id"]),
        owned_team_ids=[str(t) for t in owned],
        role=row["role"],
    )
