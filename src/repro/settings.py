"""Central configuration. Read model ids from constants, never build them."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The repo root, three levels up from src/repro/settings.py.
REPO_ROOT = Path(__file__).resolve().parents[2]

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

# The SAME models on the first-party Anthropic API, which is the other way to
# reach them (LLM_PROVIDER=anthropic). Ids there are undated and carry no
# `us.` profile prefix -- they are not built from the Bedrock ones, they are
# their own constants, for the same reason the Bedrock ids are constants.
ANTHROPIC_HAIKU = "claude-haiku-4-5"
ANTHROPIC_SONNET = "claude-sonnet-5"

# USD per 1M tokens, used to compute per-run cost from the `usage` block.
# Bedrock is partner-priced and the first-party API is not, so the two Haikus
# happen to agree and the two Sonnets do not. Both are listed rather than
# assumed: an unpriced id silently falls back to the Haiku row.
PRICES = {
    HAIKU: (1.0, 5.0),
    SONNET: (3.0, 15.0),
    ANTHROPIC_HAIKU: (1.0, 5.0),
    ANTHROPIC_SONNET: (2.0, 10.0),
}

#: What the UI shows when it names the model. Keyed by id so it cannot drift.
MODEL_LABELS = {
    HAIKU: "Claude Haiku 4.5 (Bedrock)",
    SONNET: "Claude Sonnet 4.5 (Bedrock)",
    ANTHROPIC_HAIKU: "Claude Haiku 4.5 (Anthropic API)",
    ANTHROPIC_SONNET: "Claude Sonnet 5 (Anthropic API)",
}


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv("LLM_PROVIDER", "fake")  # fake | bedrock | anthropic
    region: str = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    model_id: str = os.getenv("REPRO_MODEL_ID", HAIKU)
    anthropic_model_id: str = os.getenv("REPRO_ANTHROPIC_MODEL_ID", ANTHROPIC_HAIKU)
    workspace_root: str = os.getenv("REPRO_WORKSPACE", "/tmp/repro-workspaces")
    cassette_dir: str = os.getenv("REPRO_CASSETTES", "src/repro/llm/cassettes")
    record: bool = os.getenv("REPRO_RECORD", "0") == "1"


def settings() -> Settings:
    return Settings()


def load_env(path: Path | None = None) -> None:
    """Make the repo's `.env` effective for whoever is starting up.

    Called by the CLI and by the API on import, because a key that is in .env
    and not in the process environment is a key the app cannot use -- and the
    symptom is a silent fall back to replayed cassettes, which looks like a bad
    model rather than like an unread file.

    `override=False` so an exported value beats the file: DEPLOY.md's 12-hour
    routine is to re-export fresh AWS keys, and those must win over a stale file.
    Empty AWS values are then DELETED, because botocore treats an empty string
    as a credential and fails differently (and more confusingly) than no
    credential at all.
    """
    env_path = path or REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is in requirements
        return
    load_dotenv(env_path, override=False)
    for key, value in list(os.environ.items()):
        if key.startswith("AWS_") and not value.strip():
            del os.environ[key]


def usd_for(model_id: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES.get(model_id, PRICES[HAIKU])
    return round(input_tokens / 1e6 * inp + output_tokens / 1e6 * out, 6)


def model_id_for(provider: str) -> str:
    """The model id a provider will actually use. One place, so the UI cannot lie."""
    cfg = settings()
    if provider == "anthropic":
        return cfg.anthropic_model_id
    if provider == "bedrock":
        return cfg.model_id
    return ""  # fake replays whatever was recorded; naming a model would be a lie


def model_label(model_id: str) -> str:
    """A human name for a model id, falling back to the id itself."""
    return MODEL_LABELS.get(model_id, model_id)
