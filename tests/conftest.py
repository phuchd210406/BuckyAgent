"""Suite-wide fixtures.

Everything here exists to stop the developer's own shell from changing what the
tests mean.
"""
from __future__ import annotations

import pytest

from repro.llm.budget import SESSION_BUDGET_ENV, reset_session_budget
from repro.sandbox.workspace import KEEP_ENV_VAR


@pytest.fixture(autouse=True)
def _no_inherited_keep_flag(monkeypatch):
    """Clear REPRO_KEEP_WORKSPACE for every test.

    It is a debugging aid: exporting it makes `Workspace.close()` leave the tree
    on disk. An engineer who exports it and then runs the suite would otherwise
    see unrelated failures in any test that asserts a workspace was cleaned up
    (tests/test_graph.py::test_two_runs_never_share_a_workspace, for one).
    Tests that want the flag set it themselves with monkeypatch.
    """
    monkeypatch.delenv(KEEP_ENV_VAR, raising=False)


@pytest.fixture(autouse=True)
def _never_call_a_real_model(monkeypatch):
    """Pin LLM_PROVIDER=fake unless a test says otherwise.

    `clients.resolve_provider` defaults to `auto`, which picks the first
    provider that has a credential -- correct for a person running the app, and
    a way to spend money by running the test suite on a machine that happens to
    have ANTHROPIC_API_KEY exported. The suite asserts behaviour, never model
    quality, so it never needs a real one.
    """
    monkeypatch.setenv("LLM_PROVIDER", "fake")


@pytest.fixture(autouse=True)
def _fresh_session_budget(monkeypatch):
    """Give every test its own session budget.

    `repro.llm.budget.session_guard()` is process-wide and LATCHING by design:
    once the cap is reached it refuses calls for the life of the process. That
    is the point in production and poison in a test suite, where one test's
    simulated spending would otherwise trip every test that ran after it. The
    cap is also read from the environment on each check, so an engineer who
    exports REPRO_SESSION_BUDGET_USD must not change what the suite means.
    """
    monkeypatch.delenv(SESSION_BUDGET_ENV, raising=False)
    reset_session_budget()
    yield
    reset_session_budget()
