"""
Proof of the full domain.Player model: progression/aging/injury behavior and
the nested InjuryReport surviving persistence. Offline.

`pytest tests/test_domain_model.py` or `python tests/test_domain_model.py`.
"""
from handball.domain import Player, Team
from handball.players import InjuryReport
from handball.repository import InMemoryTeamRepository, team_from_dict, team_to_dict
from handball.simulation_vars import MAJOR_INJURIES


def _team() -> Team:
    def p(pid, name, pos, **kw):
        return Player(id=pid, name=name, position=pos, **kw)

    return Team(
        id="Boston", name="Boston Foxes", coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "Ada", "Forward"), p("f2", "Ben", "Forward"), p("f3", "Cy", "Forward")],
            "Midfielder": [p("m1", "Dee", "Midfielder"), p("m2", "Eli", "Midfielder"), p("m3", "Fin", "Midfielder")],
            "Defense": [p("d1", "Gus", "Defense"), p("d2", "Hal", "Defense"), p("d3", "Ike", "Defense")],
            "Goalie": [p("g1", "Jo", "Goalie", goalie_skill=7.0, max_goalie_skill=8.0)],
        },
        bench={
            "Forward": [p("f4", "Kai", "Forward"), p("f5", "Lou", "Forward")],
            "Midfielder": [p("m4", "Mo", "Midfielder"), p("m5", "Ned", "Midfielder")],
            "Defense": [p("d4", "Oz", "Defense"), p("d5", "Pat", "Defense")],
            "Goalie": [p("g2", "Quin", "Goalie", goalie_skill=6.0, max_goalie_skill=7.0)],
        },
        reserves=[p("r1", "Ray", "Forward"), p("r2", "Sam", "Defense")],
        record=(10, 4, 1),
    )


def test_young_player_progresses_toward_ceiling():
    p = Player(id="x", name="Rook", position="Forward", age=20, offense=4.0, max_offense=9.0, peak_age=27)
    before = p.offense
    p.update_stats(years=1, rate_scale=1.0)
    assert before < p.offense <= 9.0


def test_advance_year_ages_and_resets_log():
    p = Player(id="x", name="Vet", position="Forward", age=24, years_remaining=3)
    p.current_season_log["goals"] = [1, 2, 3]
    p.advance_year()
    assert p.age == 25 and p.years_remaining == 2
    assert p.total_season_goals == 0          # season log reset


def test_injury_lifecycle():
    p = Player(id="x", name="Hurt", position="Forward")
    dur = p.injure(2026, MAJOR_INJURIES[0])
    assert p.is_injured and dur >= 1
    assert p.injury_log.games_remaining == dur
    for _ in range(dur):
        p.tick_injury()
    assert not p.is_injured                    # healed after `dur` games


def test_public_view_formats_contract_and_hides_internals():
    p = Player(id="x", name="Star", position="Forward", contract_term=4, contract_value=20, rookie_contract=False)
    pv = p.public_view()
    assert pv.contract == "4/$20"
    assert not hasattr(pv, "max_offense")      # ceilings stay hidden
    assert not hasattr(pv, "injury_risk")


def test_save_percentage():
    g = Player(id="g", name="Keep", position="Goalie")
    g.current_season_log["saves"] = [8, 7]
    g.current_season_log["goals_allowed"] = [2, 3]
    assert g.save_percentage == 15 / 20


def test_injury_report_survives_persistence():
    team = _team()
    team.get("r1").injure(2026, MAJOR_INJURIES[0])
    remaining = team.get("r1").injury_log.games_remaining

    repo = InMemoryTeamRepository()
    repo.save(team)
    loaded = repo.load("Boston")

    r1 = loaded.get("r1")
    assert isinstance(r1.injury_log, InjuryReport)     # typed object, not a dict
    assert r1.is_injured
    assert r1.injury_log.games_remaining == remaining
    r1.tick_injury()                                    # behavior intact after reload
    assert r1.injury_log.games_remaining == remaining - 1


def test_full_player_roundtrips_equal():
    team = _team()
    team.get("f1").current_season_log["goals"] = [2, 1]
    team.get("f1").awards_won = ["MVP"]
    back = team_from_dict(team_to_dict(team))
    assert back == team                                  # deep equality incl. logs


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
