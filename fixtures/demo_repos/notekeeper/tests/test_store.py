"""The suite that already existed. The agent must never break these."""
import pytest

from notekeeper.store import Note, NoteStore


def make_store() -> NoteStore:
    store = NoteStore()
    store.add(Note("Invoice March", "send to accounts", tags=["work"]))
    store.add(Note("shopping", "milk, bread", tags=["home"]))
    store.add(Note("old plan", "superseded", tags=["work"], archived=True))
    return store


def test_archived_notes_are_hidden_by_default():
    assert [n.title for n in make_store().all()] == ["Invoice March", "shopping"]


def test_archived_notes_can_be_asked_for():
    assert len(make_store().all(include_archived=True)) == 3


def test_archiving_hides_a_note():
    store = make_store()
    store.archive("shopping")
    assert [n.title for n in store.all()] == ["Invoice March"]


def test_archiving_an_unknown_note_raises():
    with pytest.raises(KeyError):
        make_store().archive("nope")


def test_tagged_ignores_archived_notes():
    assert [n.title for n in make_store().tagged("work")] == ["Invoice March"]


def test_search_matches_the_body():
    assert [n.title for n in make_store().search("milk")] == ["shopping"]


def test_search_matches_the_title():
    assert [n.title for n in make_store().search("Invoice")] == ["Invoice March"]
