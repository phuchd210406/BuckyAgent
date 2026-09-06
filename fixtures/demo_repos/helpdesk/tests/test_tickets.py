"""The suite that already existed. The agent must never break these."""
import pytest

from helpdesk.tickets import Ticket, open_tickets, page_slice, total_pages

SAMPLE = [Ticket(f"T-{n}", f"subject {n}") for n in range(1, 8)]


def test_no_pages_for_an_empty_queue():
    assert total_pages(0) == 0


def test_pages_for_an_exact_multiple():
    assert total_pages(40) == 2


def test_a_partial_page_still_counts():
    assert total_pages(41) == 3


def test_a_short_list_fits_on_one_page():
    assert page_slice(SAMPLE, 1) == SAMPLE


def test_page_numbering_starts_at_one():
    with pytest.raises(ValueError):
        page_slice(SAMPLE, 0)


def test_closed_tickets_are_filtered_out():
    tickets = [Ticket("T-1", "a"), Ticket("T-2", "b", status="closed")]
    assert open_tickets(tickets) == [tickets[0]]
