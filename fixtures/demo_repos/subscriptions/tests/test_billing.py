"""The suite that already existed. The agent must never break these."""
from datetime import date

from subscriptions.billing import Subscription, add_months, next_renewal, renewals_in


def test_monthly_advances_one_month():
    assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)


def test_quarterly_advances_three_months():
    assert add_months(date(2026, 1, 15), 3) == date(2026, 4, 15)


def test_term_rolls_into_the_next_year():
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_annual_advances_twelve_months():
    assert add_months(date(2026, 3, 1), 12) == date(2027, 3, 1)


def test_plan_term_lookup():
    sub = Subscription("acme", "quarterly", date(2026, 1, 10))
    assert sub.term_months == 3


def test_next_renewal_skips_dates_already_past():
    sub = Subscription("acme", "monthly", date(2026, 1, 10))
    assert next_renewal(sub, date(2026, 3, 15)) == date(2026, 4, 10)


def test_renewals_in_a_calendar_year():
    sub = Subscription("acme", "monthly", date(2026, 1, 10))
    dates = renewals_in(sub, 2026)
    assert len(dates) == 11
    assert dates[0] == date(2026, 2, 10)
    assert dates[-1] == date(2026, 12, 10)
