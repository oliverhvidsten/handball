"""
Name: team_settings.py
Description: The parts of a team's identity its manager may edit -- nickname,
             abbreviation, logo -- over alembic 0018's columns and `team_logos`.

    What is NOT editable, and why: `teams.slug` is the domain TeamId and `teams.name`
    is the city. Both are keys (league_structure, the schedule, the rivals file, the
    Teams page's division layout, pg_repository), so a manager renaming their city
    would detach the team from its division and its schedule. Everything here is
    additive and display-only; the code path that identifies a team never reads it.

    Rules:
      - nickname: up to NICKNAME_MAX printable characters, or nothing (the city
        stands alone). Trimmed.
      - abbr: 2-4 letters/digits, stored upper-case, unique league-wide ignoring
        case -- the team switcher keys on it. Or nothing (the app derives one from
        the city, as it always has).
      - logo: any image Pillow can open (PNG, JPEG, WebP, GIF), at most LOGO_MAX_BYTES
        on the wire. It is RE-ENCODED to a square-bounded PNG of at most LOGO_SIZE px
        -- the manager's bytes are never stored or served verbatim, which is what
        makes serving it without auth safe (no SVG, no scripts, no metadata).
        `teams.logo_version` is > 0 exactly when the team has a logo -- the upload's
        Unix time, or one more than the last version if that is later -- so every
        upload is a NEW URL for the browser (which caches the image for a year).
        Removing a logo negates the version rather than zeroing it: <= 0 means "no
        logo" to every reader, and the magnitude survives so the next upload can
        still be strictly newer than anything a cache has seen.
Author: team identity
"""
from __future__ import annotations

import io
import re

from sqlalchemy import text
from sqlalchemy.engine import Engine

NICKNAME_MAX = 30
ABBR_RE = re.compile(r"^[A-Z0-9]{2,4}$")
LOGO_MAX_BYTES = 2 * 1024 * 1024        # what we accept on the wire
LOGO_SIZE = 256                          # longest side after re-encoding
LOGO_MIME = "image/png"                  # everything is stored as PNG


class TeamSettingsError(ValueError):
    """A setting the rules refuse. The message is written to be shown to the manager."""


class TeamSettingsConflict(TeamSettingsError):
    """Another team already holds this value (the abbreviation)."""


# -- validation (pure) ----------------------------------------------------------
def clean_nickname(value: str | None) -> str | None:
    if value is None:
        return None
    s = " ".join(value.split())            # trim + collapse whitespace
    if not s:
        return None
    if len(s) > NICKNAME_MAX:
        raise TeamSettingsError(f"the team name can be at most {NICKNAME_MAX} characters")
    if not s.isprintable():
        raise TeamSettingsError("the team name has characters that can't be displayed")
    return s


def clean_abbr(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip().upper()
    if not s:
        return None
    if not ABBR_RE.match(s):
        raise TeamSettingsError("the abbreviation must be 2 to 4 letters or digits")
    return s


def derive_abbr(city: str) -> str:
    """The fallback the frontend has always used: initials, capped at 3."""
    words = [w for w in city.split() if w]
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words)[:3].upper()


def display_name(city: str, nickname: str | None) -> str:
    return f"{city} {nickname}" if nickname else city


# -- the image ----------------------------------------------------------------------
def normalize_logo(data: bytes) -> bytes:
    """Re-encode an uploaded image as a bounded PNG, or raise TeamSettingsError.

    Pillow decodes it (so it must really be an image), it is converted to RGBA,
    fitted inside LOGO_SIZE x LOGO_SIZE preserving aspect ratio, and written out as a
    fresh PNG. Nothing from the upload survives but the pixels."""
    if len(data) > LOGO_MAX_BYTES:
        raise TeamSettingsError(f"the logo must be under {LOGO_MAX_BYTES // (1024 * 1024)} MB")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as e:                                 # pragma: no cover
        raise TeamSettingsError("image support is not installed on the server") from e
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()                                       # structural check; invalidates `probe`
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise TeamSettingsError("that file isn't an image we can read (use PNG, JPEG, WebP or GIF)") from e
    if getattr(im, "is_animated", False):
        im.seek(0)                                           # first frame of a GIF
    im = im.convert("RGBA")
    im.thumbnail((LOGO_SIZE, LOGO_SIZE))
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


# -- the database side ---------------------------------------------------------------
def _team(conn, slug: str, *, for_update: bool = False):
    row = conn.execute(
        text("select t.id::text as id, t.slug, t.name, t.nickname, t.abbr, t.logo_version "
             "from teams t where t.slug = :s" + (" for update of t" if for_update else "")),
        {"s": slug},
    ).mappings().first()
    if row is None:
        raise TeamSettingsError(f"no team {slug!r}")
    return row


def _report(row) -> dict:
    abbr = row["abbr"] or derive_abbr(row["name"])
    return {
        "slug": row["slug"],
        "city": row["name"],
        "nickname": row["nickname"],
        "abbr": abbr,
        "abbr_is_custom": row["abbr"] is not None,
        "display_name": display_name(row["name"], row["nickname"]),
        # > 0 = has a logo (and is the URL's cache key); <= 0 = none.
        "logo_version": int(row["logo_version"]),
        "has_logo": int(row["logo_version"]) > 0,
    }


def get_settings(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        return _report(_team(conn, slug))


def update_settings(engine: Engine, slug: str, *, nickname: str | None, abbr: str | None) -> dict:
    """Set the nickname and abbreviation together (either may be None to clear it).
    Validated first, then the abbreviation is checked against every other team under
    the team's own row lock, so two managers cannot both take "LV"."""
    nick = clean_nickname(nickname)
    ab = clean_abbr(abbr)
    with engine.begin() as conn:
        row = _team(conn, slug, for_update=True)
        if ab is not None:
            taken = conn.execute(
                text("select name from teams where upper(abbr) = :a and slug <> :s"),
                {"a": ab, "s": slug},
            ).first()
            if taken:
                raise TeamSettingsConflict(f"{taken[0]} already uses the abbreviation {ab}")
        conn.execute(
            text("update teams set nickname = :n, abbr = :a where id = cast(:id as uuid)"),
            {"n": nick, "a": ab, "id": row["id"]},
        )
        return _report(_team(conn, slug))


def set_logo(engine: Engine, slug: str, data: bytes) -> dict:
    png = normalize_logo(data)
    with engine.begin() as conn:
        row = _team(conn, slug, for_update=True)
        conn.execute(
            text("insert into team_logos (team_id, mime, bytes, updated_at) "
                 "values (cast(:id as uuid), :m, :b, now()) "
                 "on conflict (team_id) do update set mime = excluded.mime, "
                 "bytes = excluded.bytes, updated_at = now()"),
            {"id": row["id"], "m": LOGO_MIME, "b": png},
        )
        # Upload time as the version: strictly newer than any earlier upload, so a
        # browser holding the previous image (cached for a year, by URL) fetches anew.
        conn.execute(
            text("update teams set logo_version = greatest(abs(logo_version) + 1, "
                 "cast(extract(epoch from now()) as int)) where id = cast(:id as uuid)"),
            {"id": row["id"]},
        )
        return _report(_team(conn, slug))


def clear_logo(engine: Engine, slug: str) -> dict:
    with engine.begin() as conn:
        row = _team(conn, slug, for_update=True)
        gone = conn.execute(
            text("delete from team_logos where team_id = cast(:id as uuid)"), {"id": row["id"]}
        ).rowcount
        if gone:
            # <= 0 = no logo: the app renders the abbreviation and requests no image.
            # The magnitude is kept so the next upload is strictly newer than any URL
            # a browser has cached (two uploads in one second would otherwise tie).
            conn.execute(
                text("update teams set logo_version = -abs(logo_version) where id = cast(:id as uuid)"),
                {"id": row["id"]},
            )
        return _report(_team(conn, slug))


def get_logo(engine: Engine, slug: str) -> tuple[str, bytes, int] | None:
    """(mime, bytes, version) or None when the team has no logo."""
    with engine.connect() as conn:
        row = conn.execute(
            text("select l.mime, l.bytes, t.logo_version from teams t "
                 "join team_logos l on l.team_id = t.id where t.slug = :s"),
            {"s": slug},
        ).first()
    if row is None:
        return None
    return row[0], bytes(row[1]), int(row[2])
