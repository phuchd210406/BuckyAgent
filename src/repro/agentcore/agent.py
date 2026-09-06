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
import sys
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore
from pydantic import ValidationError

# `repro` lives under src/, and this file is run as a SCRIPT, not imported as a
# module of an installed package -- so nothing puts src/ on the path for it.
# Locally that is covered by PYTHONPATH=src; deployed it is not, because the
# runtime is created from `source_path` (the repo root) and requirements.txt
# does not install this project.
#
# When the import fails there, it does not look like an import failure: the
# process dies before it binds :8080 and AgentCore can only report "Runtime
# initialization time exceeded. Please make sure that initialization completes
# in 30s", which sends you looking at startup time instead of at sys.path.
#
# TWO directories, and the second one is the non-obvious half. Direct Code Deploy
# unpacks the dependency layer FLAT into the deployment root -- /var/task holds
# `bedrock_agentcore/`, `anyio/`, `pydantic/` and the rest beside `src/`. That
# root is on sys.path automatically only when the entrypoint sits in it. Ours is
# three levels down, so sys.path[0] is .../src/repro/agentcore and /var/task is
# never added: every third-party import fails, starting with the AgentCore SDK
# on the line below, and the runtime dies before it binds.
#
# Both are idempotent, so this is a no-op wherever PYTHONPATH or an install has
# already done the job.
_HERE = Path(__file__).resolve()
for _path in (str(_HERE.parents[2]), str(_HERE.parents[3])):  # .../src, then the root
    if _path not in sys.path:
        sys.path.insert(0, _path)

from repro import clients  # noqa: E402  (must follow the sys.path bootstrap above)
from repro.contracts import (  # noqa: E402
    MAX_REPRO_ATTEMPTS,
    ClientReport,
    Handover,
    Hypothesis,
    ReportFacts,
    TestArtifact,
)
from repro.graph.build import run  # noqa: E402
from repro.graph.sandbox_seam import Sandbox, StubSandbox  # noqa: E402
from repro.llm.base import LLMClient  # noqa: E402

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
    """LLM_PROVIDER=bedrock deployed, =fake locally on cassettes, =stub for smoke.

    Only `stub` is ours; the other two come from repro.clients so the API and
    this entrypoint cannot drift apart on what a provider name means.
    """
    if provider == "stub":
        from repro.llm.fake import ScriptedLLM

        return ScriptedLLM(_smoke_script())
    return clients.llm_for(provider)


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
