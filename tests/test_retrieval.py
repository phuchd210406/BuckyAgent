"""
Local code search. The first test is the module's whole point.

If `test_shopcart_complaint_ranks_pricing_first` fails, the localiser hands the
model the wrong file and no amount of prompt work downstream recovers: the agent
never finds the bug. Everything else here exists to keep that test honest —
that it passes because the vocabulary map works, not because the corpus was
small or the competition was excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from repro.contracts import MAX_LOCALISE_CANDIDATES
from repro.retrieval import index
from repro.retrieval.index import (
    SNIPPET_MAX_LINES,
    expand_query,
    list_source_files,
    rank_candidates,
    search,
    split_identifier,
)
from repro.sandbox.fake import FakeWorkspace
from repro.sandbox.workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
SHOPCART = REPO_ROOT / "fixtures" / "demo_repos" / "shopcart"

# eval/dataset.yaml, case `shopcart-free-shipping`, as the client wrote it.
COMPLAINT = (
    "hi, i tried to buy stuff this morning and it charged me postage even "
    "though the site says free postage over $50. my basket was definitely more "
    "than $50. can you sort it out, we have customers complaining"
)


@pytest.fixture
def shopcart(tmp_path) -> Workspace:
    ws = Workspace(SHOPCART, root=tmp_path / "ws")
    yield ws
    ws.close()


def workspace_of(tmp_path, files: dict[str, str], name: str = "repo") -> Workspace:
    """A throwaway repo from {rel_path: source}, opened as a real Workspace."""
    source = tmp_path / name
    for rel, text in files.items():
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    source.mkdir(parents=True, exist_ok=True)
    return Workspace(source, root=tmp_path / f"{name}-ws")


def every_py(ws: Workspace) -> list[str]:
    """Every .py path including tests/, for searching wider than the default."""
    return [p.relative_to(ws.path).as_posix() for p in sorted(Path(ws.path).rglob("*.py"))]


# ---------------------------------------------------------------------------
# The point of the module
# ---------------------------------------------------------------------------


def test_shopcart_complaint_ranks_pricing_first(shopcart):
    """A client says "postage"; the code says `shipping`. Bridge that, or fail."""
    hits = search(shopcart, COMPLAINT, k=5)

    assert hits, "the complaint matched nothing at all"
    assert hits[0][0] == "shopcart/pricing.py"


def test_the_winning_snippet_is_the_buggy_function(shopcart):
    """Ranking the right file is half of it: the snippet has to show the fault."""
    path, snippet, _ = search(shopcart, COMPLAINT, k=5)[0]

    assert path == "shopcart/pricing.py"
    assert snippet.startswith("def shipping_for(")
    assert "FREE_SHIPPING_THRESHOLD" in snippet
    assert "def total(" not in snippet  # the function, not the file


def test_pricing_beats_the_test_file_on_the_words_themselves(shopcart):
    """The win must be earned, not an artefact of excluding tests/.

    `tests/test_pricing.py` shares nearly all of pricing.py's vocabulary —
    shipping, threshold, free, promo, total. Searching both, the source file has
    to come out ahead on term overlap alone, because the day a client's repo
    keeps its tests somewhere this module does not recognise, that is all the
    ranking has left.
    """
    hits = search(shopcart, COMPLAINT, k=5, files=every_py(shopcart))
    ranked = [path for path, _, _ in hits]

    assert ranked[0] == "shopcart/pricing.py"
    assert "tests/test_pricing.py" in ranked
    by_path = {path: score for path, _, score in hits}
    assert by_path["shopcart/pricing.py"] > by_path["tests/test_pricing.py"] * 1.5


def test_the_synonym_map_is_what_finds_it(shopcart, monkeypatch):
    """"postage" appears nowhere in shopcart. Without the map, nothing matches."""
    assert "postage" not in (SHOPCART / "shopcart" / "pricing.py").read_text()

    assert search(shopcart, "free postage", k=5)[0][0] == "shopcart/pricing.py"

    monkeypatch.setattr(index, "SYNONYMS", {})
    monkeypatch.setattr(index, "PHRASE_SYNONYMS", {})
    assert search(shopcart, "postage", k=5) == []


def test_candidates_from_the_complaint_name_the_buggy_symbol(shopcart):
    candidates = rank_candidates(search(shopcart, COMPLAINT, k=5), MAX_LOCALISE_CANDIDATES)

    assert candidates
    assert candidates[0].file_path == "shopcart/pricing.py"
    assert candidates[0].symbol == "shipping_for"
    assert candidates[0].confidence > 0.5


# ---------------------------------------------------------------------------
# The vocabulary gap, term by term
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "client_word, code_word",
    [
        ("postage", "shipping"),
        ("basket", "cart"),
        ("voucher", "promo"),
        ("coupon", "discount"),
    ],
)
def test_client_words_reach_code_words(client_word, code_word):
    assert code_word in dict(expand_query(client_word))


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("i can't sign in any more", "login"),
        ("my payment didn't go through", "exception"),
        ("the password reset email never came", "token"),
    ],
)
def test_phrases_are_matched_before_the_words_are_split(phrase, expected):
    """"sign in" means something its two words separately do not."""
    assert expected in dict(expand_query(phrase))


def test_the_clients_own_word_outweighs_an_inferred_one():
    terms = dict(expand_query("shipping"))
    assert terms["shipping"] == 1.0
    assert terms["postage"] == pytest.approx(index.SYNONYM_WEIGHT)
    assert terms["postage"] < terms["shipping"]


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("free_shipping_threshold", ["free", "shipping", "threshold", "free_shipping_threshold"]),
        ("applyPromoCode", ["apply", "promo", "code", "applypromocode"]),
        ("HTTPServerError", ["http", "server", "error", "httpservererror"]),
        ("total", ["total"]),
    ],
)
def test_identifiers_split_and_keep_the_whole_form(identifier, expected):
    assert split_identifier(identifier) == expected


def test_stopwords_do_not_reach_the_query():
    assert "the" not in dict(expand_query("the site says the postage was the problem"))


# ---------------------------------------------------------------------------
# Snippets stay small
# ---------------------------------------------------------------------------


def test_a_long_function_is_capped_not_dumped(tmp_path):
    body = "\n".join(f"    shipping_step_{i} = {i}" for i in range(200))
    ws = workspace_of(tmp_path, {"app/big.py": f"def shipping_total():\n{body}\n"})

    _, snippet, _ = search(ws, "shipping total", k=1)[0]
    lines = snippet.splitlines()

    assert len(lines) <= SNIPPET_MAX_LINES
    assert "more lines not shown" in lines[-1]
    assert "shipping_step_199" not in snippet
    ws.close()


def test_a_module_level_match_returns_the_header_not_the_file(tmp_path):
    """"Never a whole file" has to hold for small files too."""
    source = (
        '"""Delivery pricing rules."""\n'
        "FREE_SHIPPING_THRESHOLD = 50.0\n"
        "\n"
        "def unrelated_helper():\n"
        "    return 1\n"
    )
    ws = workspace_of(tmp_path, {"app/rules.py": source})

    _, snippet, _ = search(ws, "free shipping threshold", k=1)[0]

    assert "FREE_SHIPPING_THRESHOLD" in snippet
    assert "def unrelated_helper" not in snippet
    ws.close()


# ---------------------------------------------------------------------------
# Refusing to fall over
# ---------------------------------------------------------------------------


def test_an_empty_repo_returns_nothing_and_does_not_raise(tmp_path):
    ws = workspace_of(tmp_path, {}, name="empty")
    assert search(ws, COMPLAINT, k=5) == []
    assert rank_candidates([], MAX_LOCALISE_CANDIDATES) == []
    ws.close()


def test_a_repo_with_only_tests_returns_nothing(tmp_path):
    ws = workspace_of(tmp_path, {"tests/test_x.py": "def test_shipping(): pass\n"})
    assert search(ws, "shipping", k=5) == []
    ws.close()


def test_an_empty_query_returns_nothing(shopcart):
    assert search(shopcart, "", k=5) == []
    assert search(shopcart, "   ", k=5) == []
    assert search(shopcart, COMPLAINT, k=0) == []


def test_a_file_that_does_not_parse_is_still_searchable(tmp_path):
    """A syntax error in the client's repo is not a reason to go blind."""
    ws = workspace_of(
        tmp_path,
        {
            "app/broken.py": "def shipping_for(  # unclosed\n",
            "app/other.py": "def unrelated():\n    return 0\n",
        },
    )
    hits = search(ws, "shipping", k=5)

    assert [path for path, _, _ in hits] == ["app/broken.py"]
    ws.close()


def test_unreadable_and_oversized_files_are_skipped_not_fatal(tmp_path, monkeypatch):
    ws = workspace_of(tmp_path, {"app/huge.py": "def shipping_for():\n    return 1\n"})
    monkeypatch.setattr(index, "MAX_FILE_BYTES", 5)
    assert search(ws, "shipping", k=5) == []
    ws.close()


def test_results_are_deterministic(shopcart):
    """A cassette key is a hash of the prompt these snippets go into."""
    assert search(shopcart, COMPLAINT, k=5) == search(shopcart, COMPLAINT, k=5)


def test_ties_break_on_path_not_on_walk_order(tmp_path):
    same = "def shipping_for():\n    return 1\n"
    ws = workspace_of(tmp_path, {"app/z.py": same, "app/a.py": same, "app/m.py": same})

    paths = [path for path, _, _ in search(ws, "shipping_for", k=5)]
    assert paths == ["app/a.py", "app/m.py", "app/z.py"]
    ws.close()


# ---------------------------------------------------------------------------
# list_source_files
# ---------------------------------------------------------------------------


def test_source_files_exclude_tests_and_junk(tmp_path):
    ws = workspace_of(
        tmp_path,
        {
            "app/pricing.py": "x = 1\n",
            "app/test_pricing.py": "x = 1\n",
            "tests/test_app.py": "x = 1\n",
            "tests/__init__.py": "",
            "build/generated.py": "x = 1\n",
            "README.md": "not python",
        },
    )
    assert list_source_files(ws) == ["app/pricing.py"]
    ws.close()


def test_source_files_are_capped(tmp_path):
    ws = workspace_of(tmp_path, {f"app/m{i}.py": "x = 1\n" for i in range(10)})
    assert len(list_source_files(ws, max_files=4)) == 4
    ws.close()


def test_b5_repo_facts_is_preferred_when_it_lands(tmp_path, monkeypatch):
    """B5 owns the real lister. When it exists, this module must defer to it."""
    import sys
    import types

    module = types.ModuleType("repro.sandbox.repo_facts")
    module.list_source_files = lambda ws, max_files=300: ["app/pricing.py"]
    monkeypatch.setitem(sys.modules, "repro.sandbox.repo_facts", module)

    ws = workspace_of(tmp_path, {"app/pricing.py": "x = 1\n", "app/other.py": "x = 1\n"})
    assert list_source_files(ws) == ["app/pricing.py"]
    ws.close()


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------


def test_candidates_are_capped_at_the_contract_bound():
    hits = [(f"app/m{i}.py", f"def f{i}():\n    pass", 10.0 - i) for i in range(12)]
    assert len(rank_candidates(hits, 99)) == MAX_LOCALISE_CANDIDATES
    assert len(rank_candidates(hits, 2)) == 2
    assert rank_candidates(hits, 0) == []


def test_confidence_is_normalised_and_ordered():
    hits = [("a.py", "def a(): pass", 8.0), ("b.py", "def b(): pass", 4.0), ("c.py", "", 1.0)]
    candidates = rank_candidates(hits, 5)

    scores = [c.confidence for c in candidates]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= c.confidence <= 1.0 for c in candidates)
    # score / (best + softener): the top hit is confident, never certain.
    assert candidates[0].confidence == pytest.approx(8.0 / (8.0 + index.CONFIDENCE_SOFTENER), abs=1e-3)


def test_a_batch_of_weak_hits_does_not_crown_the_best_of_a_bad_lot():
    strong = rank_candidates([("a.py", "def a(): pass", 9.0)], 5)[0]
    weak = rank_candidates([("a.py", "def a(): pass", 0.2)], 5)[0]

    assert strong.confidence > 0.85
    assert weak.confidence < 0.25


def test_rationale_is_left_for_the_model():
    """It has to quote the client's words, and only the model has read them."""
    candidates = rank_candidates([("a.py", "def a(): pass", 3.0)], 5)
    assert all(c.rationale == "" for c in candidates)


def test_symbols_come_from_the_snippet():
    hits = [
        ("a.py", "@dataclass\nclass Cart:\n    pass", 3.0),
        ("b.py", "async def checkout(user):\n    pass", 2.0),
        ("c.py", "SHIPPING_FLAT = 4.90", 1.0),
    ]
    assert [c.symbol for c in rank_candidates(hits, 5)] == ["Cart", "checkout", None]


def test_duplicate_file_and_symbol_pairs_are_dropped():
    """localiser.py dedupes on (file_path, symbol); do not hand it collisions."""
    hits = [
        ("a.py", "def same(): pass", 3.0),
        ("a.py", "def same(): pass", 2.0),
        ("a.py", "def other(): pass", 1.0),
    ]
    candidates = rank_candidates(hits, 5)
    assert [(c.file_path, c.symbol) for c in candidates] == [("a.py", "same"), ("a.py", "other")]


def test_zero_scored_hits_are_not_candidates():
    assert rank_candidates([("a.py", "def a(): pass", 0.0)], 5) == []


# ---------------------------------------------------------------------------
# In-memory workspaces
#
# FakeWorkspace (task B4) holds its files in a dict and has no tree on disk, so
# anything that walks `ws.path` -- this module's own fallback and B5's
# repo_facts alike -- finds nothing and returns [] without raising. A graph test
# written against the fake would then see the localiser retrieve NOTHING, pass,
# and say nothing at all about the system that ships.
# ---------------------------------------------------------------------------


def fake_shopcart() -> FakeWorkspace:
    ws = FakeWorkspace()
    ws.write_file("shopcart/pricing.py", (SHOPCART / "shopcart" / "pricing.py").read_text())
    ws.write_file("shopcart/__init__.py", "")
    ws.write_file("tests/test_pricing.py", (SHOPCART / "tests" / "test_pricing.py").read_text())
    return ws


def test_search_finds_files_written_to_an_in_memory_workspace():
    hits = search(fake_shopcart(), COMPLAINT, k=5)

    assert hits, "an in-memory workspace retrieved nothing at all"
    assert hits[0][0] == "shopcart/pricing.py"
    assert hits[0][1].startswith("def shipping_for(")


def test_a_fake_workspace_and_a_real_one_return_identical_hits(shopcart):
    """The parity that matters for retrieval: same files in, same ranking out.

    tests/test_fake_parity.py pins FakeWorkspace to Workspace's own API. This
    pins what this module MAKES of them, which is the part a localiser test
    actually asserts on.
    """
    real = search(shopcart, COMPLAINT, k=5)
    fake = search(fake_shopcart(), COMPLAINT, k=5)

    assert [path for path, _, _ in fake] == [path for path, _, _ in real]
    assert [snippet for _, snippet, _ in fake] == [snippet for _, snippet, _ in real]


def test_candidates_from_an_in_memory_workspace_name_the_buggy_symbol():
    candidates = rank_candidates(search(fake_shopcart(), COMPLAINT, k=5), MAX_LOCALISE_CANDIDATES)

    assert candidates[0].file_path == "shopcart/pricing.py"
    assert candidates[0].symbol == "shipping_for"


def test_an_in_memory_listing_still_excludes_tests():
    assert list_source_files(fake_shopcart()) == ["shopcart/__init__.py", "shopcart/pricing.py"]


def test_an_empty_in_memory_workspace_returns_nothing():
    assert list_source_files(FakeWorkspace()) == []
    assert search(FakeWorkspace(), COMPLAINT, k=5) == []


def test_a_workspaces_own_listing_wins_over_a_disk_walk(monkeypatch):
    """B5 walks `ws.path` too, so for an in-memory workspace it also finds [].

    The workspace's own account of its contents is the only truth there is for
    one, so it has to be consulted before either walk.
    """
    import types

    module = types.ModuleType("repro.sandbox.repo_facts")
    module.list_source_files = lambda ws, max_files=300: ["never/used.py"]
    monkeypatch.setitem(sys.modules, "repro.sandbox.repo_facts", module)

    assert list_source_files(fake_shopcart()) == ["shopcart/__init__.py", "shopcart/pricing.py"]
