"""The suite that already existed. The agent must never break these."""
import pytest

from expenses.split import format_money, settle_up, split_evenly


def test_splits_between_two():
    assert split_evenly(1000, 2) == [500, 500]


def test_splits_between_four():
    assert split_evenly(2000, 4) == [500, 500, 500, 500]


def test_one_person_owes_the_whole_bill():
    assert split_evenly(999, 1) == [999]


def test_a_zero_bill_is_zero_each():
    assert split_evenly(0, 3) == [0, 0, 0]


def test_needs_someone_to_split_between():
    with pytest.raises(ValueError):
        split_evenly(1000, 0)


def test_a_negative_bill_is_rejected():
    with pytest.raises(ValueError):
        split_evenly(-1, 2)


def test_money_is_formatted_in_pounds():
    assert format_money(1234) == "£12.34"
    assert format_money(5) == "£0.05"


def test_settle_up_names_the_shares():
    assert settle_up(900, ["ana", "ben", "caz"]) == {"ana": 300, "ben": 300, "caz": 300}
