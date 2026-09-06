"""Ticket list paging. Contains DEMO BUG #3 (see fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PER_PAGE = 20


@dataclass
class Ticket:
    ref: str
    subject: str
    status: str = "open"


def open_tickets(tickets: list[Ticket]) -> list[Ticket]:
    """Only the tickets still awaiting a reply."""
    return [t for t in tickets if t.status == "open"]


def total_pages(count: int, per_page: int = DEFAULT_PER_PAGE) -> int:
    """How many pages the pager should offer for `count` tickets."""
    if count <= 0:
        return 0
    return (count + per_page - 1) // per_page


def page_slice(
    tickets: list[Ticket], page: int, per_page: int = DEFAULT_PER_PAGE
) -> list[Ticket]:
    """The tickets shown on `page`, counting from 1."""
    if page < 1:
        raise ValueError("page numbering starts at 1")
    start = (page - 1) * per_page
    # DEMO BUG #3: `end` is computed as an INCLUSIVE index but handed to a
    # slice, which is exclusive. Every page is one ticket short, and the ticket
    # that falls off is not pushed onto the next page -- page 2 starts after
    # it. It exists, it is findable by reference, and it is on no page at all.
    end = start + per_page - 1
    return tickets[start:end]
