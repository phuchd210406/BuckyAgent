"""Cheap local code search over a workspace. OWNER: Engineer C.

No vector database, on purpose. The AWS deck is explicit that OpenSearch's
minimum always-on capacity runs to hundreds of dollars a month, and our corpus
is one repository, in a temp directory, for sixty seconds. An index we build in
memory and throw away costs nothing and cannot be stale.

The whole problem this module solves is a VOCABULARY GAP. Clients write
"postage", "basket", "voucher"; the code says `shipping`, `cart`, `promo`.
Nothing else in here matters as much as `SYNONYMS` below: a perfect BM25 over
the wrong words still ranks the wrong file first.

Three decisions worth stating plainly:

* **We index identifiers, comments and docstrings — not string literals.**
  Those three are where developers write in English. Literals are data, and in
  a test file they are mostly numbers that match any query with a number in it.

* **Identifiers are split on snake_case and camelCase, and the whole form is
  kept too.** That is what lets "free postage" reach `free_shipping_threshold`.

* **The unit of retrieval is a function, not a file.** A file's score is its
  best function's score, and the snippet returned is that function's source,
  capped at 60 lines. The model never receives a page dump, and the localiser
  gets a symbol name it can hang a hypothesis on.
"""
from __future__ import annotations

import ast
import io
import math
import re
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from repro.contracts import MAX_LOCALISE_CANDIDATES, Hypothesis
from repro.sandbox.workspace import Workspace

# --- bounds. Everything here ends up in a prompt, so nothing is unbounded. ---
MAX_FILES = 300
MAX_FILE_BYTES = 200_000
SNIPPET_MAX_LINES = 60

# Okapi BM25's standard constants. `b` is what makes a short precise function
# beat a long file that happens to mention the same word once.
BM25_K1 = 1.2
BM25_B = 0.75

# A synonym is weaker evidence than the client's own word appearing verbatim.
SYNONYM_WEIGHT = 0.6

# Softens `score / top_score` so a batch of uniformly weak hits does not report
# the best of a bad lot as certainty. See `rank_candidates`.
CONFIDENCE_SOFTENER = 1.0

# ---------------------------------------------------------------------------
# The vocabulary gap.
#
# Client word -> the words a developer would have typed. Kept deliberately
# small: every entry is a claim that two words mean the same thing here, and a
# wrong one drags an unrelated file up the ranking for every future report.
# ---------------------------------------------------------------------------

SYNONYMS: dict[str, tuple[str, ...]] = {
    "postage": ("shipping", "delivery", "freight"),
    "shipping": ("postage", "delivery"),
    "basket": ("cart", "order", "bag"),
    "voucher": ("promo", "discount", "coupon", "code"),
    "coupon": ("promo", "discount", "voucher", "code"),
    "checkout": ("cart", "order", "payment", "purchase"),
    "login": ("auth", "authenticate", "session", "signin"),
    "password": ("passwd", "credential", "auth"),
    "charged": ("charge", "price", "cost", "bill", "fee"),
    "free": ("zero", "waived", "threshold"),
}

# Phrases are matched against the raw complaint BEFORE tokenising, because
# "sign in" and "didn't go through" carry meaning their separate words do not.
PHRASE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sign in": ("login", "auth", "authenticate", "session"),
    "signed in": ("login", "auth", "session"),
    "log in": ("login", "auth", "authenticate", "session"),
    "logged in": ("login", "auth", "session"),
    "didn t go through": ("error", "exception", "fail", "failed"),
    "did not go through": ("error", "exception", "fail", "failed"),
    "went through": ("error", "exception", "fail", "commit"),
    "money off": ("discount", "promo"),
    "password reset": ("reset", "token", "password", "recovery"),
}

# Words that appear in every complaint and every file, and so separate nothing.
# BM25's IDF already discounts them; dropping them keeps the query small and
# stops "it" matching an `it` in someone's variable name.
STOPWORDS = frozenset(
    """
    a an and are as at be been but by can did do does doesn don for from get got
    had has have i if in is it its just me my no not of on or our out over so
    some than that the their them then there they this to try tried up us use
    used very was we were what when which who why will with would you your
    """.split()
)

# `HTTPServer` -> HTTP, Server; `free_shipping_2` -> free, shipping, 2.
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)|^\s*class\s+(\w+)", re.MULTILINE)

#: Mirrors B5's `repo_facts.list_source_files`, which excludes tests/. The fault
#: lives in the source; a test file that exercises the buggy function shares all
#: of its vocabulary and would crowd out the thing we are looking for.
_TEST_MARKERS = ("tests", "test")
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist"})


# ---------------------------------------------------------------------------
# Tokenising
# ---------------------------------------------------------------------------


def split_identifier(word: str) -> list[str]:
    """`free_shipping_threshold` -> the parts AND the whole, lowercased.

    Keeping the whole form matters as much as splitting it: a client who pastes
    an identifier verbatim should get an exact hit, and a client who writes
    "free postage" should reach it through its parts.
    """
    parts = [p.lower() for chunk in word.split("_") if chunk for p in _CAMEL_RE.findall(chunk)]
    whole = word.lower().strip("_")
    if whole and whole not in parts:
        parts.append(whole)
    return parts


def tokenise(text: str) -> list[str]:
    """Lowercased terms from prose or code, with identifiers split."""
    out: list[str] = []
    for word in _WORD_RE.findall(text or ""):
        out.extend(split_identifier(word))
    return [t for t in out if t and t not in STOPWORDS]


def expand_query(query: str) -> list[tuple[str, float]]:
    """The client's words, plus the code words they probably meant.

    Returns (term, weight) pairs. A term the client actually wrote weighs 1.0; a
    term we inferred from the synonym map weighs less, so a file that uses the
    client's own vocabulary still wins against one reached only by inference.
    """
    weights: dict[str, float] = {}

    def offer(term: str, weight: float) -> None:
        if term and term not in STOPWORDS:
            weights[term] = max(weights.get(term, 0.0), weight)

    # Phrases first, off the raw string: "sign in" is not the sum of its words.
    flat = _NON_ALNUM_RE.sub(" ", (query or "").lower())
    for phrase, expansions in PHRASE_SYNONYMS.items():
        if phrase in flat:
            for term in expansions:
                offer(term, SYNONYM_WEIGHT)

    for term in tokenise(query):
        offer(term, 1.0)
        for synonym in SYNONYMS.get(term, ()):
            offer(synonym, SYNONYM_WEIGHT)

    return sorted(weights.items())


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    """One retrievable chunk: a function, or a file's module level."""

    path: str
    symbol: str | None
    start: int  # 1-based, inclusive
    end: int  # 1-based, inclusive
    terms: Counter = field(default_factory=Counter)

    @property
    def length(self) -> int:
        return max(1, sum(self.terms.values()))


def _comments(source: str) -> list[tuple[int, str]]:
    """(lineno, text) for every `#` comment. Never raises on a broken file."""
    found: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found.append((token.start[0], token.string.lstrip("#")))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # A file we cannot tokenise still gets indexed, just without comments.
        pass
    return found


_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _identifiers(node: ast.AST, *, descend_into_functions: bool) -> list[str]:
    """Every identifier and docstring word in a subtree.

    `descend_into_functions=False` is what separates the module-level unit from
    the function units: without it every term would be counted twice, once in
    the function that owns it and once in the file that contains it.

    A skipped function contributes NOTHING to its enclosing module unit, not
    even its name. Adding the names back is tempting and is a trap: it builds a
    tiny table-of-contents unit out of pure signal, and BM25 normalises by
    length, so that unit outranks the real function every time — leaving the
    localiser with a one-line snippet of the file header instead of the code.
    The function is already findable through its own unit, which has the better
    snippet anyway.
    """
    words: list[str] = []
    stack: list[ast.AST] = [node]
    first = True
    while stack:
        current = stack.pop()
        if not first and isinstance(current, _FUNC_TYPES) and not descend_into_functions:
            continue
        first = False

        if isinstance(current, (ast.Module, ast.ClassDef, *_FUNC_TYPES)):
            doc = ast.get_docstring(current)
            if doc:
                words.extend(tokenise(doc))
        if isinstance(current, (ast.ClassDef, *_FUNC_TYPES)):
            words.extend(split_identifier(current.name))
        elif isinstance(current, ast.Name):
            words.extend(split_identifier(current.id))
        elif isinstance(current, ast.Attribute):
            words.extend(split_identifier(current.attr))
        elif isinstance(current, ast.arg):
            words.extend(split_identifier(current.arg))
        elif isinstance(current, ast.keyword) and current.arg:
            words.extend(split_identifier(current.arg))
        elif isinstance(current, ast.alias):
            for part in (current.name or "").split(".") + [current.asname or ""]:
                words.extend(split_identifier(part))

        stack.extend(ast.iter_child_nodes(current))
    return [w for w in words if w not in STOPWORDS]


def _span(node: ast.AST) -> tuple[int, int]:
    """A definition's line span, decorators included — they are part of it."""
    start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
    return start, getattr(node, "end_lineno", None) or node.lineno


def units_for(rel_path: str, source: str) -> list[_Unit]:
    """Split one source file into retrievable units. Never raises.

    A file that does not parse is still searchable — it is indexed whole, from
    its raw words. A syntax error in the client's repo is not a reason for the
    agent to go blind.
    """
    line_count = max(1, source.count("\n") + 1)
    comments = _comments(source)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        unit = _Unit(rel_path, None, 1, line_count)
        unit.terms.update(tokenise(source))
        return [unit]

    functions = [n for n in ast.walk(tree) if isinstance(n, _FUNC_TYPES)]
    units: list[_Unit] = []
    for node in functions:
        start, end = _span(node)
        unit = _Unit(rel_path, node.name, start, end)
        unit.terms.update(_identifiers(node, descend_into_functions=True))
        unit.terms.update(
            word
            for lineno, text in comments
            if start <= lineno <= end
            for word in tokenise(text)
        )
        units.append(unit)

    # Everything outside a function: the module docstring, class names, the
    # constants (FREE_SHIPPING_THRESHOLD) and the comments around them. Its
    # SNIPPET is only the header — the docstring, imports and constants above
    # the first definition — because "never a whole file" has to hold for a
    # small file too, and a 40-line module is not a locator.
    covered = {(u.start, u.end) for u in units}
    first_def = min((u.start for u in units), default=line_count + 1)
    module = _Unit(rel_path, None, 1, max(1, min(line_count, first_def - 1)))
    module.terms.update(_identifiers(tree, descend_into_functions=False))
    module.terms.update(
        word
        for lineno, text in comments
        if not any(start <= lineno <= end for start, end in covered)
        for word in tokenise(text)
    )
    if module.terms:
        units.append(module)
    return units


def _snippet(source_lines: list[str], unit: _Unit) -> str:
    """The unit's own source, never more than SNIPPET_MAX_LINES lines."""
    lines = source_lines[unit.start - 1 : unit.end]
    if len(lines) > SNIPPET_MAX_LINES:
        hidden = len(lines) - (SNIPPET_MAX_LINES - 1)
        lines = lines[: SNIPPET_MAX_LINES - 1] + [f"# ... {hidden} more lines not shown"]
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Reading the workspace
# ---------------------------------------------------------------------------


def _is_test_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    name = Path(rel_path).stem
    return any(p in _TEST_MARKERS for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test")


def _self_listing(ws: Workspace) -> list[str] | None:
    """Paths a workspace can enumerate itself, or None if it cannot.

    `sandbox.fake.FakeWorkspace` holds its files in a dict and has no tree on
    disk, so walking `ws.path` finds nothing there. Without this, every search
    against the fake returns [] SILENTLY, and a graph test written against it is
    green while saying nothing about the real system.

    Duck-typed rather than imported: retrieval must not depend on a test double.
    """
    written = getattr(ws, "written_files", None)
    if not callable(written):
        return None
    try:
        return sorted(written())
    except (TypeError, RuntimeError):
        return None


def list_source_files(ws: Workspace, max_files: int = MAX_FILES) -> list[str]:
    """Repo-relative .py paths, tests excluded, sorted, capped.

    Three sources, in order of authority:

    1. a workspace that can enumerate ITSELF. For an in-memory workspace that is
       the only truth there is, and it has to win over any walk of `ws.path`,
       B5's included -- both walk a directory that does not exist and return [].
    2. Engineer B's `repo_facts.list_source_files` (task B5), the real one for a
       real repo. Same contract, so swapping it in changes nothing here.
    3. this module's own walk, for before B5 landed.

    Falls back rather than failing at every step: the localiser must keep working.
    """
    names = _self_listing(ws)

    if names is None:
        try:
            from repro.sandbox.repo_facts import list_source_files as b5

            return list(b5(ws, max_files=max_files))
        except (ImportError, AttributeError, NotImplementedError):
            pass

        root = Path(ws.path)
        names = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py"))

    found: list[str] = []
    for rel in names:
        if not rel.endswith(".py"):
            continue
        if any(part in _SKIP_DIRS for part in Path(rel).parts):
            continue
        if _is_test_path(rel):
            continue
        found.append(rel)
    return found[:max_files]


def _read(ws: Workspace, rel_path: str) -> str | None:
    """Source text, or None for anything we should not or cannot index."""
    try:
        target = ws.resolve_path(rel_path)
    except (ValueError, RuntimeError):
        return None

    try:
        if target.is_file():
            if target.stat().st_size > MAX_FILE_BYTES:
                return None
            return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # An unreadable file is one fewer candidate, not a failed run.
        return None

    # Nothing on disk. An in-memory workspace still serves it through the
    # Workspace API, which is the surface both of them are guaranteed to share.
    try:
        return ws.read_file(rel_path, max_chars=MAX_FILE_BYTES)
    except (AttributeError, OSError, ValueError, RuntimeError):
        return None


# ---------------------------------------------------------------------------
# The public surface (pinned by graph/sandbox_seam.WorkspaceSandbox)
# ---------------------------------------------------------------------------


def search(
    ws: Workspace, query: str, k: int = 8, *, files: list[str] | None = None
) -> list[tuple[str, str, float]]:
    """Return (rel_path, snippet, score), best first. Pure Python, no network.

    One hit per file — its best-matching function — so `k` files come back, not
    `k` fragments of the same file. Snippets are capped so the model never
    receives a page dump. Returns [] for an empty query or an empty repo; it
    never raises on a repo it cannot read.

    `files` overrides which paths are indexed. The default excludes tests/ (see
    `list_source_files`); pass an explicit list to search them too.
    """
    terms = expand_query(query)
    if k <= 0 or not terms:
        return []

    paths = list(files) if files is not None else list_source_files(ws)
    units: list[_Unit] = []
    sources: dict[str, list[str]] = {}
    for rel_path in paths[:MAX_FILES]:
        source = _read(ws, rel_path)
        if source is None:
            continue
        sources[rel_path] = source.splitlines()
        units.extend(units_for(rel_path, source))
    if not units:
        return []

    doc_freq: Counter[str] = Counter()
    for unit in units:
        doc_freq.update(unit.terms.keys())
    total_units = len(units)
    avg_len = sum(u.length for u in units) / total_units

    best: dict[str, tuple[float, _Unit]] = {}
    for unit in units:
        score = _bm25(unit, terms, doc_freq, total_units, avg_len)
        if score <= 0:
            continue
        if unit.path not in best or score > best[unit.path][0]:
            best[unit.path] = (score, unit)

    # Path breaks ties so two equally-good files always come back in the same
    # order: a cassette key is a hash of the prompt these snippets go into.
    ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
    return [
        (path, _snippet(sources[path], unit), round(score, 4))
        for path, (score, unit) in ranked[:k]
    ]


def _bm25(
    unit: _Unit,
    terms: list[tuple[str, float]],
    doc_freq: Counter[str],
    total_units: int,
    avg_len: float,
) -> float:
    """Okapi BM25 over one unit, with each query term carrying its own weight."""
    score = 0.0
    norm = BM25_K1 * (1 - BM25_B + BM25_B * unit.length / max(avg_len, 1.0))
    for term, weight in terms:
        freq = unit.terms.get(term, 0)
        if not freq:
            continue
        df = doc_freq[term]
        idf = math.log(1 + (total_units - df + 0.5) / (df + 0.5))
        score += weight * idf * (freq * (BM25_K1 + 1)) / (freq + norm)
    return score


def symbol_of(snippet: str) -> str | None:
    """The function or class a snippet defines, for `Hypothesis.symbol`."""
    match = _DEF_RE.search(snippet or "")
    return (match.group(1) or match.group(2)) if match else None


def rank_candidates(hits: list[tuple[str, str, float]], k: int) -> list[Hypothesis]:
    """Turn search hits into typed candidates, without spending a token.

    `rationale` is left empty ON PURPOSE: it must quote the client's own words
    back, and only the model has read the complaint. Filling it here with
    "matched 4 terms" would put a machine's excuse in front of a human.

    Confidence is `score / (best_score + CONFIDENCE_SOFTENER)`: ordering is
    preserved, the range is 0..1, and — because the softener never vanishes — a
    batch of weak matches scores low rather than crowning the best of a bad lot.
    """
    limit = max(0, min(k, MAX_LOCALISE_CANDIDATES))
    if not hits or limit == 0:
        return []

    ordered = sorted(hits, key=lambda hit: (-hit[2], hit[0]))
    best_score = max(0.0, ordered[0][2])
    if best_score <= 0:
        return []

    out: list[Hypothesis] = []
    seen: set[tuple[str, str | None]] = set()
    for path, snippet, score in ordered:
        if len(out) >= limit:
            break
        symbol = symbol_of(snippet)
        if (path, symbol) in seen:
            continue
        seen.add((path, symbol))
        confidence = min(1.0, max(0.0, score) / (best_score + CONFIDENCE_SOFTENER))
        out.append(
            Hypothesis(
                file_path=path,
                symbol=symbol,
                rationale="",
                confidence=round(confidence, 3),
            )
        )
    return out
