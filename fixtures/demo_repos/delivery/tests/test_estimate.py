"""The suite that already existed. The agent must never break these.

These tests are the specification: the estimate is in WORKING days, and the
Friday case the client complained about is asserted here on purpose.
"""
import pytest

from datetime import date

from delivery.estimate import add_business_days, estimate_arrival, is_working_day

FRIDAY = date(2026, 9, 4)
MONDAY = date(2026, 9, 7)


def test_a_weekday_is_a_working_day():
    assert is_working_day(MONDAY)


def test_a_saturday_is_not_a_working_day():
    assert not is_working_day(date(2026, 9, 5))


def test_a_bank_holiday_is_not_a_working_day():
    assert not is_working_day(date(2026, 12, 25))


def test_standard_from_a_monday_lands_on_thursday():
    assert add_business_days(MONDAY, 3) == date(2026, 9, 10)


def test_standard_from_a_friday_lands_on_wednesday():
    # The weekend is not counted. This is the case the client reports as a bug.
    assert add_business_days(FRIDAY, 3) == date(2026, 9, 9)


def test_express_from_a_friday_is_the_next_monday():
    assert add_business_days(FRIDAY, 1) == MONDAY


def test_a_bank_holiday_pushes_the_estimate_out():
    assert add_business_days(date(2026, 12, 24), 1) == date(2026, 12, 29)


def test_estimate_uses_the_service_promise():
    assert estimate_arrival(FRIDAY, "economy") == date(2026, 9, 11)


def test_an_unknown_service_is_rejected():
    with pytest.raises(ValueError):
        estimate_arrival(FRIDAY, "teleport")
