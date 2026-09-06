"""AgentCore entrypoint tests. OWNER: Engineer A.

scripts/smoke_agentcore.sh proves the HTTP contract, but it is not run in CI.
These cover the handler itself so a broken payload path cannot ship quietly.
"""
from __future__ import annotations

import pytest

from repro.agentcore.agent import handler, llm_for, sandbox_for
from repro.graph.sandbox_seam import StubSandbox

GOOD_REPORT = {
    "run_id": "smoke-1",
    "raw_text": "Checkout charged me twice.",
    "channel": "email",
    "repo_path": "/tmp",
}


@pytest.fixture(autouse=True)
def _stub_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")


def test_a_valid_report_comes_back_with_a_verdict_and_a_handover():
    body = handler({"report": GOOD_REPORT})

    assert body["run_id"] == "smoke-1"
    # The stub sandbox runs nothing, so nothing reproduces. That is the honest
    # answer, and the smoke test asserts it rather than a fabricated success.
    assert body["verdict"] == "not_reproduced"
    assert body["handover"]["client_reply"]
    assert body["usage"]["calls"] == 6
    assert "status" not in body


@pytest.mark.parametrize(
    "payload",
    [
        {"report": {"raw_text": "no run_id, no repo_path"}},
        {"report": None},
        {},
        {"report": "a string"},
        "not a dict at all",
        None,
    ],
)
def test_a_bad_payload_is_a_400_shape_not_an_exception(payload):
    body = handler(payload)

    assert body["status"] == 400
    assert body["detail"]
    assert "verdict" not in body  # nothing that reads like a result


def test_unknown_provider_is_named_loudly():
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        llm_for("gpt-by-accident")


def test_only_the_smoke_provider_stubs_the_sandbox():
    assert isinstance(sandbox_for("stub"), StubSandbox)
    # None means run() opens a real workspace over the client's repo.
    assert sandbox_for("fake") is None
    assert sandbox_for("bedrock") is None
