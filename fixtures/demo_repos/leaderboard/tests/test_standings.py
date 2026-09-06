"""The suite that already existed. The agent must never break these."""
import pytest

from leaderboard.standings import Player, position_of, rank, top_n

BOARD = [
    Player("ana", 30),
    Player("ben", 50),
    Player("caz", 10),
    Player("dev", 40),
]


def test_highest_points_first():
    assert [p.name for p in rank(BOARD)] == ["ben", "dev", "ana", "caz"]


def test_an_empty_board_ranks_to_nothing():
    assert rank([]) == []


def test_one_player_is_first():
    assert [p.name for p in rank([Player("solo", 7)])] == ["solo"]


def test_top_n_takes_from_the_front():
    assert [p.name for p in top_n(BOARD, 2)] == ["ben", "dev"]


def test_position_is_one_based():
    assert position_of(BOARD, "ben") == 1
    assert position_of(BOARD, "caz") == 4


def test_an_unknown_player_has_no_position():
    with pytest.raises(KeyError):
        position_of(BOARD, "nobody")
