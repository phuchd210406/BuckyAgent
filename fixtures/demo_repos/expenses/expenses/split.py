"""Splitting a bill in whole pennies. Contains DEMO BUG #4 (fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.

Money is held as an integer number of pennies everywhere in this module. That
part is right, and it is why the defect below is a rounding bug rather than a
floating-point one.
"""
from __future__ import annotations


def format_money(cents: int) -> str:
    """Pennies as a displayable amount."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}£{cents // 100}.{cents % 100:02d}"


def split_evenly(total_cents: int, ways: int) -> list[int]:
    """Split `total_cents` between `ways` people, in whole pennies."""
    if ways < 1:
        raise ValueError("need at least one person to split between")
    if total_cents < 0:
        raise ValueError("total cannot be negative")
    # DEMO BUG #4: integer division discards the remainder, so any bill that
    # does not divide exactly loses pennies that belong to somebody. 1000p
    # between 3 is 333p each and a penny that is charged to nobody. The shares
    # no longer add up to the bill, and the gap grows one round at a time.
    share = total_cents // ways
    return [share] * ways


def settle_up(total_cents: int, people: list[str]) -> dict[str, int]:
    """What each named person owes."""
    shares = split_evenly(total_cents, len(people))
    return dict(zip(people, shares))
