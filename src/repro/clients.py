"""Choosing an LLM client from the environment. One place, so two callers cannot disagree.

`LLM_PROVIDER=bedrock` deployed, `=fake` locally against recorded cassettes.
The AgentCore entrypoint adds its own `stub` provider on top of this for its
smoke test; nothing else should.
"""
from __future__ import annotations

import os

from repro.llm.base import LLMClient


def llm_for(provider: str | None = None) -> LLMClient:
    """The client this environment asks for. Imports lazily: no boto3 for fakes."""
    chosen = (provider or os.getenv("LLM_PROVIDER", "fake")).strip().lower()

    if chosen == "bedrock":
        from repro.llm.bedrock import BedrockLLM, RecordingLLM
        from repro.settings import settings

        client = BedrockLLM()
        return RecordingLLM(client) if settings().record else client

    if chosen == "fake":
        from repro.llm.fake import FakeLLM
        from repro.settings import settings

        return FakeLLM(settings().cassette_dir)

    raise ValueError(f"unknown LLM_PROVIDER {chosen!r}: expected 'bedrock' or 'fake'")
