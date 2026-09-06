"""Structured JSON output, shared by every real provider.

This started life inside `bedrock.py` and moved here the day a second provider
appeared. There is exactly one implementation of "ask the model for an object,
refuse a truncated reply, allow one repair round, then give up", because two
copies of that logic drift and the drift is invisible: the same run through two
providers would disagree about whether the model answered.

`BedrockLLM` and `AnthropicLLM` both inherit `StructuredJSONClient` and supply
nothing but `_converse` -- one round trip, priced, with a `stop_reason`.
"""
from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from repro.contracts import LLMResponse
from repro.llm.base import SchemaValidationError

T = TypeVar("T", bound=BaseModel)

# How much of a bad reply we quote back in the repair prompt. The whole reply can
# be a full max_tokens of prose; quoting it verbatim doubles the cost of the very
# call that already failed once.
REPAIR_QUOTE_CHARS = 4_000


class TruncatedResponseError(RuntimeError):
    """The model hit the `max_tokens` ceiling, so its text is cut off mid-object.

    Deliberately NOT a `SchemaValidationError`: the model did nothing wrong and
    re-prompting it will not help. The fix is a larger ceiling or a smaller ask.
    """


# ---------------------------------------------------------------------------
# Prompt fragments
# ---------------------------------------------------------------------------

_JSON_INSTRUCTION = """
Reply with ONE JSON object and nothing else.
No prose before or after it. No markdown code fence. No explanation.
Every required field must be present; omit optional fields you cannot support
from the input rather than inventing a value for them.

The object must validate against this JSON Schema for `{name}`:
{schema}
""".strip()

_REPAIR_INSTRUCTION = """
Your previous reply did not validate against the required JSON Schema.

--- your previous reply ---
{reply}
--- end of previous reply ---

--- validation errors ---
{errors}
--- end of validation errors ---

Fix exactly these errors and return the corrected object. One JSON object, no
prose, no code fence. Do not change fields that were already valid.

--- the original request, unchanged ---
{user}
""".strip()

# Models emit ```json fences regardless of being told not to, so strip one
# defensively. An UNCLOSED fence deliberately does not match: that is the shape
# of a truncated reply, and it should fail loudly rather than parse half an object.
_FENCE_RE = re.compile(r"\A\s*```(?:json|JSON)?\s*\n(.*?)\n?\s*```\s*\Z", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Return `text` with a wrapping markdown fence removed, if there is one."""
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


class StructuredJSONClient:
    """`structured()` for any client that can do one `_converse` round trip.

    Subclasses implement `_converse(system=..., user=..., max_tokens=...)` and
    inherit the three counters below, which are the evaluation slide's metric #1:
    the share of structured calls whose FIRST reply validated.
    """

    #: Set by subclasses before the first call.
    model_id: str = ""

    def __init__(self) -> None:  # pragma: no cover - subclasses call this
        self.structured_calls = 0
        self.first_attempt_validations = 0
        self.reprompts = 0

    # --- the one thing a subclass must provide ------------------------------
    def _converse(self, *, system: str, user: str, max_tokens: int) -> LLMResponse:
        raise NotImplementedError

    @property
    def first_attempt_rate(self) -> float:
        """Metric #1. 1.0 when every structured call validated first time."""
        if not self.structured_calls:
            return 1.0
        return self.first_attempt_validations / self.structured_calls

    # --- LLMClient ----------------------------------------------------------
    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        """Free-text completion. `stop_reason` is passed through for the caller to judge."""
        return self._converse(system=system, user=user, max_tokens=max_tokens)

    def structured(
        self, *, system: str, user: str, schema: Type[T], max_tokens: int = 1024
    ) -> tuple[T, LLMResponse]:
        """Return a validated `schema` instance, or raise. Never a half-filled object."""
        self.structured_calls += 1
        schema_system = self._system_with_schema(system, schema)

        first = self._converse(system=schema_system, user=user, max_tokens=max_tokens)
        self._refuse_truncated(first, schema, max_tokens)
        try:
            obj = schema.model_validate_json(strip_code_fence(first.text))
        except ValidationError as exc:
            first_error = exc
        else:
            self.first_attempt_validations += 1
            return obj, first

        # One repair round. Exactly one: a model that cannot hit the shape twice
        # will not hit it on the third try either, and each round is billed.
        self.reprompts += 1
        repair_user = _REPAIR_INSTRUCTION.format(
            reply=first.text[:REPAIR_QUOTE_CHARS],
            errors=str(first_error)[:REPAIR_QUOTE_CHARS],
            user=user,
        )
        second = self._converse(system=schema_system, user=repair_user, max_tokens=max_tokens)
        # Both calls were billed, so the response the caller accounts for carries
        # the sum. Anything less under-reports the run against MAX_RUN_USD.
        second = LLMResponse(
            text=second.text,
            stop_reason=second.stop_reason,
            usage=first.usage.merge(second.usage),
        )
        self._refuse_truncated(second, schema, max_tokens)
        try:
            obj = schema.model_validate_json(strip_code_fence(second.text))
        except ValidationError as exc:
            raise SchemaValidationError(
                f"{schema.__name__} did not validate after one re-prompt.\n"
                f"first error: {first_error}\n"
                f"second error: {exc}"
            ) from exc
        return obj, second

    # --- helpers ------------------------------------------------------------
    def _system_with_schema(self, system: str, schema: Type[T]) -> str:
        # Compact and unsorted: pydantic emits a deterministic schema already, so
        # this stays byte-stable across runs while costing the fewest tokens. The
        # system prompt is resent on every step of the loop.
        as_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        instruction = _JSON_INSTRUCTION.format(name=schema.__name__, schema=as_json)
        return f"{system.strip()}\n\n{instruction}"

    def _refuse_truncated(self, response: LLMResponse, schema: Type[T], max_tokens: int) -> None:
        """Never parse a reply the ceiling cut off — see the module docstring."""
        if response.stop_reason != "max_tokens":
            return
        raise TruncatedResponseError(
            f"the model stopped at the max_tokens ceiling ({max_tokens}) while producing "
            f"{schema.__name__}, so the reply is truncated and its JSON is incomplete. "
            "Not parsed: this is our ceiling, not a model error. Raise max_tokens or "
            "ask for a smaller object."
        )
