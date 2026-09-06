"""Preview text for dashboard cards. Contains DEMO BUG #7 (fixtures/BUGS.md).

Do not fix in main -- the demo needs the defect present.
"""
from __future__ import annotations

ELLIPSIS = "…"

#: The card was built for this many characters.
DEFAULT_LIMIT = 80


def summarise(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """Shorten `text` to fit a card, without cutting a word in half."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    # DEMO BUG #7: this searches FORWARD for the space after `limit`, so the
    # whole of the word straddling the boundary is kept and the preview runs
    # past the width the card was built for.
    #
    # Cutting at exactly `limit` is NOT the fix: test_words_are_never_cut_in_half
    # is a real requirement, and a hard cut also has to keep working for a long
    # string with no spaces in it at all.
    cut = text.find(" ", limit)
    if cut == -1:
        cut = len(text)
    return text[:cut] + ELLIPSIS
