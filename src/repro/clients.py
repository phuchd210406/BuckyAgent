"""Choosing an LLM client from the environment. One place, so two callers cannot disagree.

Three providers, and the difference between them is where the money comes from:

  * `anthropic` — Claude Haiku 4.5 on the first-party API, with an
    `ANTHROPIC_API_KEY`. A key that does not expire, which is why it is the
    default for a real run.
  * `bedrock`   — the same model through the AWS sandbox lease. What the
    AgentCore deployment uses; its credentials expire every 12 hours.
  * `fake`      — replays recorded cassettes. Free, deterministic, offline, and
    NOT a real run: it can only answer prompts somebody already recorded, so a
    complaint or a repository it has never seen makes it miss.

`auto` picks the first of those that actually has a credential, and says so.
That is what the API and the CLI ask for, so a missing key degrades to the free
path with an explanation instead of a stack trace.
"""
from __future__ import annotations

import logging
import os

from repro.llm.base import LLMClient

LOG = logging.getLogger("repro.clients")

PROVIDERS = ("anthropic", "bedrock", "fake")

#: What `auto` tries, in order. Anthropic first: its key does not expire.
AUTO_ORDER = ("anthropic", "bedrock")


def credential_for(provider: str) -> bool:
    """True when `provider` has something to authenticate with. No network.

    Deliberately cheap and read at call time: a key exported after this module
    was imported must still count, and asking AWS whether a lease is alive costs
    a round trip on every page load of the UI.
    """
    if provider == "fake":
        return True
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if provider == "bedrock":
        # A session token is not required for long-lived keys, so the pair is
        # the test. Whether the lease has EXPIRED is only knowable by calling
        # AWS; `scripts/check_bedrock.py` is the thing that does that.
        return bool(
            os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
            and os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
        ) or bool(os.environ.get("AWS_PROFILE", "").strip())
    return False


def resolve_provider(provider: str | None = None) -> str:
    """The provider that will actually be used, resolving `auto` to a real one."""
    chosen = (provider or os.getenv("LLM_PROVIDER", "auto")).strip().lower()
    if chosen != "auto":
        return chosen
    for candidate in AUTO_ORDER:
        if credential_for(candidate):
            return candidate
    return "fake"


def llm_for(provider: str | None = None) -> LLMClient:
    """The client this environment asks for. Imports lazily: no boto3 for fakes."""
    chosen = resolve_provider(provider)

    if chosen == "anthropic":
        from repro.llm.anthropic_api import AnthropicLLM

        return AnthropicLLM()

    if chosen == "bedrock":
        from repro.llm.bedrock import BedrockLLM, RecordingLLM
        from repro.settings import settings

        client = BedrockLLM()
        return RecordingLLM(client) if settings().record else client

    if chosen == "fake":
        from repro.llm.fake import FakeLLM
        from repro.settings import settings

        return FakeLLM(settings().cassette_dir)

    raise ValueError(
        f"unknown LLM_PROVIDER {chosen!r}: expected one of {', '.join(PROVIDERS)} or 'auto'"
    )
