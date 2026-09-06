"""
The real client: Bedrock `converse`, plus the record wrapper that feeds cassettes.

Design notes worth keeping, because each one cost someone an afternoon:

* **`converse`, never `invoke_model`.** `converse` normalises the request and
  response shape across vendors, so swapping HAIKU for SONNET on a retry is a
  one-line change to `model_id` instead of a rewrite of the body encoder.

* **`stopReason == "max_tokens"` means the text is INCOMPLETE.** Session 1 makes
  this point specifically: truncated JSON reads like a model error but is not
  one — the model was cut off mid-object by *our* ceiling. Parsing it produces a
  confusing pydantic error that sends you looking at the prompt, so
  `structured()` refuses to parse and raises `TruncatedResponseError` naming the
  ceiling instead. Raise the caller's `max_tokens`; do not touch the prompt.

* **One re-prompt, then stop.** A validation failure gets exactly one repair
  round with the pydantic error fed back, then `SchemaValidationError`. A
  partially-filled object is never returned: downstream code trusts these shapes.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Type, TypeVar

from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError

from repro.contracts import LLMResponse, TokenUsage
from repro.llm.base import SchemaValidationError
from repro.llm.budget import BudgetGuard, estimate_call_usd, session_guard
from repro.llm.fake import cassette_key
from repro.settings import settings, usd_for

T = TypeVar("T", bound=BaseModel)

# Bedrock's own throttle codes. Retrying these is correct; retrying anything else
# (a bad model id, a denied profile) just burns the same failure three times.
THROTTLE_CODES = frozenset({"ThrottlingException", "TooManyRequestsException"})

MAX_THROTTLE_TRIES = 3
BACKOFF_BASE_S = 0.5

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


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


def _text_of(response: dict[str, Any]) -> str:
    """Join the text blocks of a converse reply, ignoring any non-text block."""
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    return "".join(b["text"] for b in blocks if "text" in b)


def _usage_of(response: dict[str, Any], model_id: str) -> TokenUsage:
    """Build a priced `TokenUsage` from the reply's `usage` block."""
    raw = response.get("usage", {})
    inp = int(raw.get("inputTokens", 0))
    out = int(raw.get("outputTokens", 0))
    return TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        calls=1,
        usd=usd_for(model_id, inp, out),
    )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class BedrockLLM:
    """`LLMClient` over bedrock-runtime `converse`.

    Satisfies the protocol structurally; it does not inherit from it, so the
    agents can be handed a `FakeLLM` in exactly the same slot.

    Metric #1 on the evaluation slide is `first_attempt_validations /
    structured_calls`: the share of structured calls whose FIRST reply validated.
    It is the number that says whether the prompts and schema descriptions are
    doing their job, so it is a plain counter anyone can read off the instance.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        model_id: str | None = None,
        region: str | None = None,
        max_throttle_tries: int = MAX_THROTTLE_TRIES,
        backoff_base_s: float = BACKOFF_BASE_S,
        sleep: Callable[[float], None] = time.sleep,
        temperature: float = 0.0,
        budget: BudgetGuard | None = None,
    ) -> None:
        cfg = settings()
        self.model_id = model_id or cfg.model_id
        self.region = region or cfg.region
        self.max_throttle_tries = max_throttle_tries
        self.backoff_base_s = backoff_base_s
        self.temperature = temperature
        self._sleep = sleep
        self._client = client if client is not None else self._make_client()

        # This run's cap. It is the ACCOUNTANT as well as the gate, so there is
        # one record of what was spent rather than two that can disagree.
        self.budget = budget if budget is not None else BudgetGuard()

        # --- metrics ---------------------------------------------------------
        self.structured_calls = 0          # structured() entered
        self.first_attempt_validations = 0  # ...of which validated on reply #1
        self.reprompts = 0                 # repair rounds we had to run

    def _make_client(self) -> Any:
        # Imported here so that merely importing this module costs nothing, and
        # so an injected mock client never builds a real boto3 session.
        import boto3

        return boto3.client("bedrock-runtime", region_name=self.region)

    @property
    def usage(self) -> TokenUsage:
        """Everything this client has spent. Kept by the budget guard."""
        return self.budget.usage

    @property
    def first_attempt_rate(self) -> float:
        """Metric #1. 1.0 when every structured call validated first time."""
        if not self.structured_calls:
            return 1.0
        return self.first_attempt_validations / self.structured_calls

    # --- the single network call ------------------------------------------
    def _converse(self, *, system: str, user: str, max_tokens: int) -> LLMResponse:
        """One `converse` round trip, retried on throttling with exponential backoff.

        The budget is checked HERE, before the request leaves, and against a
        worst-case estimate of this call. Checking after the reply comes back
        would mean the call that broke the cap had already been billed.
        """
        estimated = estimate_call_usd(
            self.model_id, system=system, user=user, max_tokens=max_tokens
        )
        # Session first: it guards the money the whole team shares.
        session_guard().check(estimated)
        self.budget.check(estimated)

        for attempt in range(self.max_throttle_tries):
            try:
                raw = self._client.converse(
                    modelId=self.model_id,
                    system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": self.temperature},
                )
            except ClientError as exc:
                last_try = attempt == self.max_throttle_tries - 1
                if _error_code(exc) not in THROTTLE_CODES or last_try:
                    raise
                self._sleep(self.backoff_base_s * (2**attempt))
                continue

            response = LLMResponse(
                text=_text_of(raw),
                stop_reason=raw.get("stopReason", "end_turn"),
                usage=_usage_of(raw, self.model_id),
            )
            # What it ACTUALLY cost, which is what the next check reads.
            self.budget.add(response.usage)
            session_guard().add(response.usage)
            return response

        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    # --- LLMClient ---------------------------------------------------------
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

    # --- helpers -----------------------------------------------------------
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
            f"Bedrock stopped at the max_tokens ceiling ({max_tokens}) while producing "
            f"{schema.__name__}, so the reply is truncated and its JSON is incomplete. "
            "Not parsed: this is our ceiling, not a model error. Raise max_tokens or "
            "ask for a smaller object."
        )


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class RecordingLLM:
    """Wraps any `LLMClient` and writes each reply to a cassette on the way out.

    The key comes from `fake.cassette_key`, IMPORTED rather than reimplemented:
    replay looks a reply up by hashing the same three strings, so two copies of
    that hash that ever drift by a byte mean every cassette becomes unfindable.

    It records the CALLER's `system`/`user`, not the schema-augmented prompt the
    Bedrock client actually sends, because replay calls `FakeLLM` with the
    caller's arguments.
    """

    def __init__(
        self,
        inner: Any,
        *,
        cassette_dir: str | Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        cfg = settings()
        self.inner = inner
        self.dir = Path(cassette_dir if cassette_dir is not None else cfg.cassette_dir)
        self.enabled = cfg.record if enabled is None else enabled
        self.written: list[str] = []

    def _write(self, key: str, payload: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{key}.json"
        # Write-then-rename: a half-written cassette replays as a corrupt reply,
        # which is a far more confusing failure than a missing one.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
        self.written.append(key)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        response = self.inner.complete(system=system, user=user, max_tokens=max_tokens)
        if self.enabled:
            self._write(
                cassette_key(system, user, "raw"),
                {
                    # The prompt is stored alongside the reply so a cassette can
                    # say what produced it. The key is a hash of the prompt, not
                    # of this file, so adding these fields leaves every existing
                    # cassette findable.
                    "system": system,
                    "user": user,
                    "text": response.text,
                    "stop_reason": response.stop_reason,
                    "usage": response.usage.model_dump(),
                },
            )
        return response

    def structured(
        self, *, system: str, user: str, schema: Type[T], max_tokens: int = 1024
    ) -> tuple[T, LLMResponse]:
        obj, response = self.inner.structured(
            system=system, user=user, schema=schema, max_tokens=max_tokens
        )
        if self.enabled:
            self._write(
                cassette_key(system, user, schema.__name__),
                {
                    # The prompt that produced this reply. Without it a cassette
                    # is a hash with no way back to the words behind it, and a
                    # miss can only be diagnosed by guessing which edit moved
                    # the key. See FakeLLM._explain_miss, which diffs against these.
                    "system": system,
                    "user": user,
                    # `text` is the VALIDATED object, because that is what
                    # FakeLLM feeds straight to model_validate_json. The literal
                    # reply can be fenced, or be the second half of a repair
                    # round; it is kept beside it for the audit trail only.
                    "text": obj.model_dump_json(),
                    "raw_text": response.text,
                    "schema": schema.__name__,
                    "stop_reason": response.stop_reason,
                    "usage": response.usage.model_dump(),
                },
            )
        return obj, response
