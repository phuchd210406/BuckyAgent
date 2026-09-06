"""Every module must at least import. Catches syntax errors across all five streams."""
import importlib

import pytest

MODULES = [
    "repro.contracts",
    "repro.settings",
    "repro.llm.base",
    "repro.llm.fake",
    "repro.sandbox.workspace",
    "repro.sandbox.runner",
    "repro.sandbox.patcher",
    "repro.retrieval.index",
    "repro.graph.state",
    "repro.graph.build",
    "repro.agents.intake",
    "repro.agents.localiser",
    "repro.agents.repro_agent",
    "repro.agents.fix",
    "repro.agents.reporter",
]


@pytest.mark.parametrize("name", MODULES)
def test_imports(name):
    importlib.import_module(name)
