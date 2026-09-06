"""Weekly standings. Contains DEMO BUG #5 (see fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Player:
    name: str
    points: int
    played: int = 0


def rank(players: list[Player]) -> list[Player]:
    """Highest points first. Players level on points are ordered by name."""
    by_name = sorted(players, key=lambda p: p.name)
    ordered = sorted(by_name, key=lambda p: p.points)
    # DEMO BUG #5: reversing the list to get "highest first" also reverses the
    # alphabetical tie-break established above, so players level on points come
    # out in the wrong order -- and in an order that changes with who else is
    # level that week. sorted(..., reverse=True) would have kept ties stable;
    # .reverse() on the finished list cannot tell a tie from a difference.
    ordered.reverse()
    return ordered


def position_of(players: list[Player], name: str) -> int:
    """1-based position on the board."""
    for index, player in enumerate(rank(players), start=1):
        if player.name == name:
            return index
    raise KeyError(name)


def top_n(players: list[Player], n: int) -> list[Player]:
    return rank(players)[:n]
