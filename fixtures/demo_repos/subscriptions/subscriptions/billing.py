"""Renewal dates for monthly plans. Contains DEMO BUG #2 (see fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: How long one term of each plan lasts, in months.
PLAN_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


@dataclass
class Subscription:
    customer: str
    plan: str
    started_on: date

    @property
    def term_months(self) -> int:
        return PLAN_MONTHS[self.plan]


def add_months(start: date, months: int) -> date:
    """The same calendar day, `months` later."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # DEMO BUG #2: the target month may not HAVE that day. A customer who
    # signed up on the 29th, 30th or 31st renews into a shorter month and
    # date() raises before the renewal is ever written -- so they are simply
    # not billed, and nothing in the ledger says why.
    return date(year, month, start.day)


def next_renewal(sub: Subscription, today: date) -> date:
    """The first renewal falling strictly after `today`."""
    due = add_months(sub.started_on, sub.term_months)
    while due <= today:
        due = add_months(due, sub.term_months)
    return due


def renewals_in(sub: Subscription, year: int) -> list[date]:
    """Every renewal date that lands in `year`."""
    out: list[date] = []
    due = add_months(sub.started_on, sub.term_months)
    while due.year <= year:
        if due.year == year:
            out.append(due)
        due = add_months(due, sub.term_months)
    return out
