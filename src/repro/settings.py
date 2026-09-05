"""Central configuration. Read model ids from constants, never build them."""
from __future__ import annotations

import os
from dataclasses import dataclass

# Session 1: "Read model ids from a constant, never build them."
HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

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
