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


#: Subdirectory of the cassette dir where a prompt that matched nothing is
#: written. Debugging output, not cassettes -- git-ignored, and skipped by the
#: `*.json` glob callers use to enumerate real cassettes.
MISSES_DIRNAME = "_misses"


def cassette_key(system: str, user: str, schema_name: str) -> str:
    blob = json.dumps([system, user, schema_name], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class FakeLLM:
    """Replays a recorded reply, keyed by a hash of the exact prompt."""

    def __init__(self, cassette_dir: str | Path, strict: bool = True) -> None:
        self.dir = Path(cassette_dir)
        self.strict = strict
        self.calls: list[tuple[str, str]] = []
        #: Keys that missed, in order, for tests and for a caller that wants to
        #: report every gap rather than only the first.
        self.misses: list[str] = []

    def _load(self, key: str, *, system: str, user: str, schema_name: str) -> str:
        path = self.dir / f"{key}.json"
        if not path.exists():
            self.misses.append(key)
            raise SchemaValidationError(self._explain_miss(key, system, user, schema_name))
        return json.loads(path.read_text())["text"]

    # --- diagnosing a miss --------------------------------------------------
    def _explain_miss(self, key: str, system: str, user: str, schema_name: str) -> str:
        """Say what missed, and leave the prompt on disk so it can be diffed.

        A key is a hash, so on its own it names nothing a person can act on. The
        prompt behind it is the only thing that identifies WHICH edit broke
        replay, and it exists only here, in memory, at the moment of the miss.
        """
        candidates = self._recorded_keys_for(schema_name)
        dumped = self._dump_miss(key, system, user, schema_name, candidates)

        parts = [
            f"No cassette {key} for schema {schema_name}. A cassette key hashes "
            f"(system, user, schema), so either the prompt changed since it was "
            f"recorded or this call was never recorded at all."
        ]
        if dumped is not None:
            parts.append(f"The prompt that missed is in {dumped}.")
        if candidates:
            listed = ", ".join(f"{k}.json" for k in sorted(candidates))
            parts.append(
                f"Recorded for {schema_name}: {listed}. Diff the miss against one of "
                f"those to see which words moved."
            )
        else:
            parts.append(
                f"Nothing was ever recorded for {schema_name}, so this is a call the "
                f"recording run never reached -- check whether routing changed."
            )
        parts.append("Re-record with REPRO_RECORD=1 LLM_PROVIDER=bedrock.")
        return " ".join(parts)

    def _recorded_keys_for(self, schema_name: str) -> list[str]:
        """Keys of cassettes recorded for the same schema. Never raises."""
        found: list[str] = []
        try:
            for path in sorted(self.dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if payload.get("schema", "raw") == schema_name:
                    found.append(path.stem)
        except OSError:
            pass
        return found

    def _dump_miss(
        self, key: str, system: str, user: str, schema_name: str, candidates: list[str]
    ) -> Path | None:
        """Write the unmatched prompt next to the cassettes. Never raises.

        Best-effort on purpose: a read-only cassette directory must still give
        the caller the "No cassette" error, not an unrelated permissions error
        raised from inside the error path.
        """
        try:
            directory = self.dir / MISSES_DIRNAME
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{key}.json"
            path.write_text(
                json.dumps(
                    {
                        "key": key,
                        "schema": schema_name,
                        "system": system,
                        "user": user,
                        "recorded_for_this_schema": sorted(candidates),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            return path
        except (OSError, ValueError):  # pragma: no cover - best effort by design
            return None

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append((system, user))
        text = self._load(
            cassette_key(system, user, "raw"), system=system, user=user, schema_name="raw"
        )
        return LLMResponse(text=text, stop_reason="end_turn", usage=TokenUsage(calls=1))

    def structured(
        self, *, system: str, user: str, schema: Type[T], max_tokens: int = 1024
    ) -> tuple[T, LLMResponse]:
        self.calls.append((system, user))
        text = self._load(
            cassette_key(system, user, schema.__name__),
            system=system,
            user=user,
            schema_name=schema.__name__,
        )
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
