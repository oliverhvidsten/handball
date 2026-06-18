"""
Name: draft_simulator.py
Description: Generate new draft-class players with randomly assigned stats.

Player ability is not derived from any name-based model or rating tier. Each
prospect's overall skill is sampled directly from a normal distribution inside
``Player.create_new_player`` (see ``NEW_PLAYER_MEAN`` / ``NEW_PLAYER_STD`` in
simulation_vars), then split into concrete offense / defense / goalie stats.

Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 11:07AM PST
"""
import csv
import random
from pathlib import Path

import numpy as np

from handball.domain import Player


# Valid player positions (must match the cases in Player.create_new_player).
POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"]


def load_prospect_names(path):
    """
    Load draft prospect names from a file.

    Supported formats:
      - ``.csv`` with a ``Name`` column (and an optional ``Position`` column)
      - any other extension: plain text, one name per line

    Returns a list of ``(name, position_or_None)`` tuples. Blank lines /
    nameless rows are skipped. Raises FileNotFoundError if the file is missing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No draft names file found at '{path}'. Provide a names file "
            "(one name per line, or a CSV with a 'Name' column)."
        )

    prospects = []
    if p.suffix.lower() == ".csv":
        with open(p, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("Name") or "").strip()
                if not name:
                    continue
                position = (row.get("Position") or "").strip() or None
                prospects.append((name, position))
    else:
        with open(p) as f:
            for line in f:
                name = line.strip()
                if name:
                    prospects.append((name, None))

    return prospects

# Default probability weights per position for an auto-generated draft class.
# Field positions are common; goalies are comparatively rare.
DEFAULT_POSITION_WEIGHTS = {
    "Forward": 0.30,
    "Midfielder": 0.30,
    "Defense": 0.30,
    "Goalie": 0.10,
}


def assign_random_position(position_weights=None):
    """
    Randomly choose a player position.

    Input:
        1. position_weights (dict | None): position -> probability weight.
           Defaults to ``DEFAULT_POSITION_WEIGHTS``.

    Output:
        (str): the chosen position.
    """
    weights = position_weights or DEFAULT_POSITION_WEIGHTS
    positions = list(weights.keys())
    probs = list(weights.values())
    return random.choices(positions, weights=probs, k=1)[0]


def _slug(text: str) -> str:
    """Lowercase, hyphenated id fragment from a name."""
    return "-".join(str(text).lower().split())


def create_draft_player(name, position, id=None):
    """
    Create a single new draft-class Player with randomly assigned stats.

    Input:
        1. name (str): the player's name.
        2. position (str): one of ``POSITIONS``.
        3. id (str | None): stable domain id. Defaults to a slug of the name;
           callers that own canonical ids (e.g. the draft service, which keys
           by holding team) pass their own.

    Output:
        (Player): a newly generated domain.Player object.
    """
    if position not in POSITIONS:
        raise ValueError(
            f"Unknown position '{position}'. Expected one of {POSITIONS}."
        )
    return Player.create_new_player(id=id or _slug(name), name=name, position=position)


def player_generation(names_list, position_list):
    """
    Generate Player objects from parallel lists of names and positions.

    Input:
        1. names_list (list[str]): the new draft class names.
        2. position_list (list[str]): each prospect's position (same length as
           names_list). Every entry must be one of ``POSITIONS``.

    Output:
        (list[Player]): new Player objects with randomly assigned stats.
    """
    if len(names_list) != len(position_list):
        raise ValueError(
            "names_list and position_list must be the same length "
            f"(got {len(names_list)} and {len(position_list)})."
        )
    return [
        create_draft_player(name, position, id=f"{_slug(name)}-{i}")
        for i, (name, position) in enumerate(zip(names_list, position_list))
    ]


def generate_draft_class(
    names_list,
    position_list=None,
    position_weights=None,
    seed=None,
):
    """
    Generate a full draft class of Player objects with randomly assigned stats.

    Input:
        1. names_list (list[str]): the new draft class names.
        2. position_list (list[str] | None): each prospect's position. If None,
           positions are drawn at random using ``position_weights``.
        3. position_weights (dict | None): override the position distribution
           used when ``position_list`` is None.
        4. seed (int | None): if provided, seed both ``random`` and
           ``numpy.random`` for reproducible draft classes.

    Output:
        (list[Player]): the generated draft class.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    if position_list is None:
        position_list = [
            assign_random_position(position_weights) for _ in names_list
        ]

    return player_generation(names_list, position_list)
