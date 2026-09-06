"""Suite-wide fixtures.

Everything here exists to stop the developer's own shell from changing what the
tests mean.
"""
from __future__ import annotations

import pytest

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
