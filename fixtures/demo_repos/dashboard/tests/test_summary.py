"""The suite that already existed. The agent must never break these."""
from dashboard.summary import ELLIPSIS, summarise

LOREM = (
    "the quarterly review meeting has been moved to the larger room on the "
    "second floor because the projector in the usual room is still broken"
)


def test_short_text_is_left_alone():
    assert summarise("all good", 80) == "all good"


def test_runs_of_whitespace_are_collapsed():
    assert summarise("too   many\n\nspaces", 80) == "too many spaces"


def test_text_at_exactly_the_limit_is_left_alone():
    assert summarise("abcde", 5) == "abcde"


def test_shortened_text_is_marked_with_an_ellipsis():
    assert summarise(LOREM, 40).endswith(ELLIPSIS)


def test_words_are_never_cut_in_half():
    """A preview may end early, but never mid-word."""
    shown = summarise(LOREM, 40).rstrip(ELLIPSIS).split()
    assert shown, "the preview should not be empty"
    assert all(word in LOREM.split() for word in shown)


def test_a_long_string_with_no_spaces_is_marked_as_shortened():
    """There is no word boundary to cut on, but the preview still has to say so."""
    unbroken = "https://example.internal/reports/" + "a" * 200
    out = summarise(unbroken, 40)
    assert out.endswith(ELLIPSIS)
    shown = out.rstrip(ELLIPSIS)
    assert shown, "a string with no spaces must not shorten away to nothing"
    assert unbroken.startswith(shown)
