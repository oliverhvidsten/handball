"""
Team identity a manager may edit: handball/team_settings.py (nickname, abbreviation,
logo) plus api/teams.py, against the dev Postgres. Skips when Postgres is
unavailable. Local DB only (truncates).
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball import team_settings as ts
from handball.db import get_engine, is_local_db

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

if _PG_OK:
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = "teams players managers team_logos"


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def teams():
    with _engine.begin() as c:
        ids = {s: str(c.execute(text("insert into teams (slug, name) values (:s, :s) returning id"),
                                {"s": s}).scalar_one())
               for s in ("Las Vegas", "Seattle")}
    return ids


def _as_manager(*owned: str, role: str = "manager"):
    with _engine.connect() as c:
        ids = [str(c.execute(text("select id from teams where slug = :s"), {"s": s}).scalar_one())
               for s in owned]
    mgr = Manager(user_id=str(uuid.uuid4()), owned_team_ids=ids, role=role)
    app.dependency_overrides[get_current_manager] = lambda: mgr
    return mgr


@pytest.fixture
def client():
    return TestClient(app)


def _png(w=600, h=300, color=(200, 30, 30, 255)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


# -- the rules ------------------------------------------------------------------
def test_defaults_are_the_city_and_a_derived_abbreviation(teams):
    s = ts.get_settings(_engine, "Las Vegas")
    assert s["city"] == "Las Vegas" and s["nickname"] is None
    assert s["abbr"] == "LV" and s["abbr_is_custom"] is False
    assert s["display_name"] == "Las Vegas" and s["has_logo"] is False


def test_nickname_and_abbreviation_are_set_together_and_normalized(teams):
    s = ts.update_settings(_engine, "Las Vegas", nickname="  Aces  ", abbr="lva")
    assert (s["nickname"], s["abbr"], s["abbr_is_custom"]) == ("Aces", "LVA", True)
    assert s["display_name"] == "Las Vegas Aces"
    with _engine.connect() as c:
        assert c.execute(text("select name, slug from teams where slug = 'Las Vegas'")).first() == ("Las Vegas", "Las Vegas")


def test_blank_clears_back_to_the_defaults(teams):
    ts.update_settings(_engine, "Las Vegas", nickname="Aces", abbr="LVA")
    s = ts.update_settings(_engine, "Las Vegas", nickname="", abbr=None)
    assert s["nickname"] is None and s["abbr"] == "LV" and s["abbr_is_custom"] is False


@pytest.mark.parametrize("bad", ["A", "ABCDE", "L-V", "l v"])
def test_an_abbreviation_must_be_two_to_four_letters_or_digits(teams, bad):
    with pytest.raises(ts.TeamSettingsError, match="2 to 4"):
        ts.update_settings(_engine, "Las Vegas", nickname=None, abbr=bad)


def test_a_nickname_has_a_length_limit(teams):
    with pytest.raises(ts.TeamSettingsError, match="at most"):
        ts.update_settings(_engine, "Las Vegas", nickname="x" * (ts.NICKNAME_MAX + 1), abbr=None)


def test_an_abbreviation_is_unique_league_wide_ignoring_case(teams):
    ts.update_settings(_engine, "Las Vegas", nickname=None, abbr="SEA")
    with pytest.raises(ts.TeamSettingsConflict, match="Las Vegas already uses"):
        ts.update_settings(_engine, "Seattle", nickname=None, abbr="sea")
    # a team may keep its own
    assert ts.update_settings(_engine, "Las Vegas", nickname="Aces", abbr="SEA")["abbr"] == "SEA"


# -- the logo -------------------------------------------------------------------------
def test_a_logo_is_re_encoded_to_a_bounded_png_and_bumps_the_version(teams):
    from PIL import Image
    s = ts.set_logo(_engine, "Las Vegas", _png(600, 300))
    assert s["has_logo"] is True and s["logo_version"] > 0
    mime, data, version = ts.get_logo(_engine, "Las Vegas")
    assert mime == "image/png" and version == s["logo_version"]
    im = Image.open(io.BytesIO(data))
    assert im.format == "PNG" and im.size == (256, 128)          # aspect kept, bounded
    ts.set_logo(_engine, "Las Vegas", _png(50, 50))
    assert ts.get_settings(_engine, "Las Vegas")["logo_version"] > version   # a new URL


def test_something_that_is_not_an_image_is_refused(teams):
    with pytest.raises(ts.TeamSettingsError, match="isn't an image"):
        ts.set_logo(_engine, "Las Vegas", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
    assert ts.get_logo(_engine, "Las Vegas") is None


def test_removing_a_logo_zeroes_the_version_and_a_new_upload_gets_a_new_url(teams):
    first = ts.set_logo(_engine, "Las Vegas", _png())["logo_version"]
    s = ts.clear_logo(_engine, "Las Vegas")
    assert s["has_logo"] is False and s["logo_version"] <= 0      # no logo -> no image URL
    assert ts.get_logo(_engine, "Las Vegas") is None
    again = ts.set_logo(_engine, "Las Vegas", _png())["logo_version"]
    assert again > first                                         # never a reused, cached URL


# -- the API --------------------------------------------------------------------------
def test_only_the_owner_may_change_settings(client, teams):
    _as_manager("Seattle")
    r = client.put("/teams/Las Vegas/settings", json={"nickname": "Aces", "abbr": "LVA"})
    assert r.status_code == 403
    r = client.get("/teams/Las Vegas/settings")                  # reading is open to any manager
    assert r.status_code == 200 and r.json()["display_name"] == "Las Vegas"


def test_the_owner_sets_name_and_mark_and_conflicts_are_409(client, teams):
    _as_manager("Las Vegas", "Seattle")
    r = client.put("/teams/Seattle/settings", json={"abbr": "LVA"})
    assert r.status_code == 200
    r = client.put("/teams/Las Vegas/settings", json={"nickname": "Aces", "abbr": "lva"})
    assert r.status_code == 409 and "Seattle already uses" in r.json()["detail"]
    r = client.put("/teams/Las Vegas/settings", json={"nickname": "Aces", "abbr": "LV"})
    assert r.status_code == 200 and r.json()["display_name"] == "Las Vegas Aces"
    r = client.put("/teams/Las Vegas/settings", json={"abbr": "toolong"})
    assert r.status_code == 400


def test_logo_upload_is_owner_only_and_the_image_is_served_without_auth(client, teams):
    _as_manager("Seattle")
    r = client.put("/teams/Las Vegas/logo", files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 403

    _as_manager("Las Vegas")
    r = client.put("/teams/Las Vegas/logo", files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["logo_version"] > 0
    v = r.json()["logo_version"]
    r = client.put("/teams/Las Vegas/logo", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400

    app.dependency_overrides.clear()                             # no token at all
    r = client.get(f"/teams/Las Vegas/logo?v={v}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/teams/Seattle/logo").status_code == 404

    _as_manager("Las Vegas")
    r = client.delete("/teams/Las Vegas/logo")
    assert r.status_code == 200 and r.json()["has_logo"] is False
    app.dependency_overrides.clear()
    assert client.get("/teams/Las Vegas/logo").status_code == 404
