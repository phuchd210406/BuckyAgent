"""
Deterministic record/replay client.

This is the highest-leverage file in the repo for a 30-hour build:
  * the graph is testable with zero AWS spend and zero flakiness,
  * CI can run the full pipeline on every push,
  * the demo has a guaranteed-working offline fallback if the venue wifi dies.

Usage:
    REPRO_RECORD=1 LLM_PROVIDER=bedrock  ... # records real replies to cassettes/
    LLM_PROVIDER=fake                    ... # replays them, free and identical
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from repro.contracts import LLMResponse, TokenUsage
from repro.llm.base import SchemaValidationError

T = TypeVar("T", bound=BaseModel)


def cassette_key(system: str, user: str, schema_name: str) -> str:
    blob = json.dumps([system, user, schema_name], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class FakeLLM:
    """Replays a recorded reply, keyed by a hash of the exact prompt."""

    def __init__(self, cassette_dir: str | Path, strict: bool = True) -> None:
        self.dir = Path(cassette_dir)
        self.strict = strict
        self.calls: list[tuple[str, str]] = []

    def _load(self, key: str) -> str:
        path = self.dir / f"{key}.json"
        if not path.exists():
            raise SchemaValidationError(
                f"No cassette {key}. Re-record with REPRO_RECORD=1, or the prompt changed."
            )
        return json.loads(path.read_text())["text"]

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append((system, user))
        text = self._load(cassette_key(system, user, "raw"))
        return LLMResponse(text=text, stop_reason="end_turn", usage=TokenUsage(calls=1))

    def structured(
        self, *, system: str, user: str, schema: Type[T], max_tokens: int = 1024
    ) -> tuple[T, LLMResponse]:
        self.calls.append((system, user))
        text = self._load(cassette_key(system, user, schema.__name__))
        try:
            obj = schema.model_validate_json(text)
        except ValidationError as exc:  # pragma: no cover - cassette corruption
            raise SchemaValidationError(str(exc)) from exc
        return obj, LLMResponse(text=text, stop_reason="end_turn", usage=TokenUsage(calls=1))


class ScriptedLLM:
    """Returns objects handed to it. For unit tests that need no cassette."""

    def __init__(self, replies: list[BaseModel | str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def _next(self):
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies: the graph looped further than the test expected")
        return self.replies.pop(0)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=str(self._next()), stop_reason="end_turn", usage=TokenUsage(calls=1))

    def structured(self, *, system: str, user: str, schema, max_tokens: int = 1024):
        self.calls.append((system, user))
        obj = self._next()
        assert isinstance(obj, schema), f"scripted reply is {type(obj)}, graph asked for {schema}"
        return obj, LLMResponse(text=obj.model_dump_json(), stop_reason="end_turn", usage=TokenUsage(calls=1))
