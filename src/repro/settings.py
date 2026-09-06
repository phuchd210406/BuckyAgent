"""Central configuration. Read model ids from constants, never build them."""
from __future__ import annotations

import os
from dataclasses import dataclass

# Session 1: "Read model ids from a constant, never build them."
#
# These are `us.` INFERENCE PROFILE ids, not bare model ids and not `global.`
# ones. All three forms were probed against our sandbox account on 2026-09-06
# (`make check-bedrock` reproduces it):
#   * `anthropic.claude-*` alone -> ValidationException, "Invocation ... with
#     on-demand throughput isn't supported": Claude 4.5 has no on-demand base
#     model, so an inference profile is mandatory.
#   * `global.anthropic.claude-*` -> AccessDeniedException, and AWS's message is
#     specific: bedrock:InvokeModel on
#     `arn:aws:bedrock:::foundation-model/anthropic.claude-haiku-4-5-...` is
#     refused "with an explicit deny in a service control policy". The profile
#     is ACTIVE and model access IS granted (authorization=AUTHORIZED,
#     entitlement=AVAILABLE) — the global profile resolves to a REGIONLESS
#     foundation-model ARN that the sandbox's SCP denies outright. No console
#     toggle and no IAM edit of ours can override an SCP.
#   * `us.anthropic.claude-*` in us-east-1 -> works, for both models.
# ap-southeast-1 only publishes the `global.` profile, so it cannot work here at
# all: us-east-1 is the answer to the region question the two decks disagreed on.
HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# USD per 1M tokens, used to compute per-run cost from the `usage` block.
PRICES = {HAIKU: (1.0, 5.0), SONNET: (3.0, 15.0)}


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv("LLM_PROVIDER", "fake")  # fake | bedrock
    region: str = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    model_id: str = os.getenv("REPRO_MODEL_ID", HAIKU)
    workspace_root: str = os.getenv("REPRO_WORKSPACE", "/tmp/repro-workspaces")
    cassette_dir: str = os.getenv("REPRO_CASSETTES", "src/repro/llm/cassettes")
    record: bool = os.getenv("REPRO_RECORD", "0") == "1"


def settings() -> Settings:
    return Settings()


def usd_for(model_id: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES.get(model_id, PRICES[HAIKU])
    return round(input_tokens / 1e6 * inp + output_tokens / 1e6 * out, 6)
