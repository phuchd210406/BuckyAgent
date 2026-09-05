"""Cheap local code search over a workspace. OWNER: Engineer C."""
from __future__ import annotations

from repro.contracts import Hypothesis
from repro.sandbox.workspace import Workspace


def search(ws: Workspace, query: str, k: int = 8) -> list[tuple[str, str, float]]:
    """Return (rel_path, snippet, score), best first. Pure Python, no network.

    Snippets are capped so the model never receives a page dump.
    """
    raise NotImplementedError("Engineer C, task C2")


def rank_candidates(hits: list[tuple[str, str, float]], k: int) -> list[Hypothesis]:
    raise NotImplementedError("Engineer C, task C3")
