"""
Bedrock AgentCore entrypoint. OWNER: Engineer A.

Session 2, LAB 06: this file is the graph plus four lines. Nothing about the
graph changes for deployment -- `run()` is the same function the CLI and the
FastAPI surface call, and this file only turns an HTTP payload into a
`ClientReport` and a `RunRecord` back into JSON.

Run it locally first:

    LLM_PROVIDER=stub PYTHONPATH=src python src/repro/agentcore/agent.py
    curl -s localhost:8080/invocations -d '{"report": {...}}'

and only `agentcore launch` once, for the recording.
"""
from __future__ import annotations

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore
from pydantic import ValidationError

from repro.contracts import (
    MAX_REPRO_ATTEMPTS,
    ClientReport,
    Handover,
    Hypothesis,
    ReportFacts,
    TestArtifact,
)
from repro.graph.build import run
from repro.graph.sandbox_seam import Sandbox, StubSandbox
from repro.llm.base import LLMClient

LOG = logging.getLogger("repro.agentcore")

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    """POST /invocations -> {"verdict": ..., "handover": {...}}.

    A bad payload is a 400-shaped dict, never an exception: a client that sends
    us nonsense should get told what was wrong with it, not a stack trace and a
    dead worker.
    """
    if not isinstance(payload, dict):
        return _bad_request([{"loc": "", "msg": "payload must be a JSON object"}])
    try:
        report = ClientReport.model_validate(payload.get("report"))
    except ValidationError as exc:
        return _bad_request(
            [
                {
                    "loc": ".".join(str(part) for part in error["loc"]),
                    "msg": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors(include_url=False)
            ]
        )

    provider = os.getenv("LLM_PROVIDER", "fake").strip().lower()
    try:
        record = run(report, llm_for(provider), sandbox=sandbox_for(provider))
    except Exception as exc:  # noqa: BLE001 - a handler must not take the server down
        LOG.exception("run %s failed", report.run_id)
        return {
            "status": 500,
            "error": f"{type(exc).__name__}: {exc}",
            "run_id": report.run_id,
        }

    return {
        "run_id": record.run_id,
        "verdict": record.verdict.value,
        "handover": record.handover.model_dump() if record.handover else None,
        "usage": record.usage.model_dump(),
    }


def _bad_request(detail: list[dict]) -> dict:
    return {
        "status": 400,
        "error": 'invalid payload: expected {"report": {...}} matching ClientReport',
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# One file, three ways to get a model. Same graph behind all three.
# ---------------------------------------------------------------------------


def llm_for(provider: str) -> LLMClient:
    """LLM_PROVIDER=bedrock deployed, =fake locally on cassettes, =stub for smoke."""
    if provider == "bedrock":
        from repro.llm.bedrock import BedrockLLM, RecordingLLM
        from repro.settings import settings

        client = BedrockLLM()
        return RecordingLLM(client) if settings().record else client
    if provider == "fake":
        from repro.llm.fake import FakeLLM
        from repro.settings import settings

        return FakeLLM(settings().cassette_dir)
    if provider == "stub":
        from repro.llm.fake import ScriptedLLM

        return ScriptedLLM(_smoke_script())
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}: expected bedrock, fake or stub")


def sandbox_for(provider: str) -> Sandbox | None:
    """None lets run() open a real workspace. Only the smoke provider stubs it."""
    return StubSandbox() if provider == "stub" else None


def _smoke_script() -> list:
    """Canned replies for LLM_PROVIDER=stub: no AWS, no cassettes, no repo.

    This proves the HTTP contract and the graph wiring and NOTHING else. The
    stubbed sandbox runs no tests, so nothing reproduces and the honest verdict
    is `not_reproduced` -- which is exactly what a smoke test should return
    rather than a fabricated success.
    """
    return [
        ReportFacts(observed_behaviour="smoke test", confidence=1.0),
        Hypothesis(file_path="smoke.py", rationale="Smoke test. No search ran.", confidence=0.0),
        *[
            TestArtifact(path=f"tests/test_smoke_{i}.py", source="def test_smoke():\n    pass\n")
            for i in range(MAX_REPRO_ATTEMPTS)
        ],
        Handover(
            dev_summary="Smoke test: the graph ran end to end against a stubbed model.",
            client_reply="Smoke test. Nothing was run against your project.",
        ),
    ]


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", "8080")))
