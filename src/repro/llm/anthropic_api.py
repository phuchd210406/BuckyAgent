"""The second real client: Claude Haiku 4.5 straight off the Anthropic API.

WHY THIS EXISTS ALONGSIDE `bedrock.py`. The Bedrock path is the one the decks
describe and the one the AgentCore deployment uses, but it depends on a sandbox
lease whose credentials expire every twelve hours -- and an expired lease means
the app silently falls back to replaying cassettes, which is not a real run. An
`ANTHROPIC_API_KEY` is a key that does not expire at lunchtime, so the demo has
a second way to reach the same model. Same model family, same prices, same
`LLMClient` protocol, same budget guards: nothing above this file can tell which
one it was handed, which is the whole point of the seam.

Everything about *structured output* -- the schema instruction, the single
repair round, refusing to parse a truncated reply -- lives in
`repro.llm.structured` and is shared with Bedrock. This file is one network call
and the price of it.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from repro.contracts import LLMResponse, TokenUsage
from repro.llm.budget import BudgetGuard, estimate_call_usd, session_guard
from repro.llm.structured import StructuredJSONClient
from repro.settings import settings, usd_for

#: The environment variable the SDK itself reads. Named here so the error
#: message below can name it, and so `clients.llm_for` can check for it.
API_KEY_ENV = "ANTHROPIC_API_KEY"


class MissingAPIKey(RuntimeError):
    """No credential for the Anthropic API. Raised before any network call."""


class AnthropicLLM(StructuredJSONClient):
    """`LLMClient` over the Anthropic Messages API.

    Retries are the SDK's job (it backs off on 429 and 5xx by itself), so unlike
    the Bedrock client there is no throttle loop here. What is NOT delegated is
    the money: both budget guards are checked before the request leaves, against
    a pessimistic estimate, exactly as in `bedrock.py`. A cap that is enforced in
    one provider and not the other is not a cap.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        max_retries: int = 2,
        temperature: float = 0.0,
        budget: BudgetGuard | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__()
        cfg = settings()
        self.model_id = model_id or cfg.anthropic_model_id
        self.temperature = temperature
        self.timeout_s = timeout_s
        self._max_retries = max_retries
        self._client = client if client is not None else self._make_client(api_key)
        self.budget = budget if budget is not None else BudgetGuard()

    def _make_client(self, api_key: str | None) -> Any:
        # Imported here so importing this module costs nothing and needs no SDK,
        # and so an injected fake client never builds a real one.
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "LLM_PROVIDER=anthropic needs the Anthropic SDK: pip install anthropic "
                "(it is in requirements.txt)."
            ) from exc

        key = api_key or os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            raise MissingAPIKey(
                f"{API_KEY_ENV} is not set, so there is no way to call Claude. Put a key in "
                ".env (see .env.example), or use LLM_PROVIDER=bedrock with fresh AWS "
                "credentials, or LLM_PROVIDER=fake to replay the recorded run for free."
            )
        return anthropic.Anthropic(
            api_key=key, max_retries=self._max_retries, timeout=self.timeout_s
        )

    @property
    def usage(self) -> TokenUsage:
        """Everything this client has spent. Kept by the budget guard."""
        return self.budget.usage

    # --- the single network call -------------------------------------------
    def _converse(self, *, system: str, user: str, max_tokens: int) -> LLMResponse:
        """One Messages round trip, priced from the reply's own usage block."""
        estimated = estimate_call_usd(
            self.model_id, system=system, user=user, max_tokens=max_tokens
        )
        # Session first: it guards the money the whole team shares.
        session_guard().check(estimated)
        self.budget.check(estimated)

        raw = self._client.messages.create(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system,
            temperature=self.temperature,
            messages=[{"role": "user", "content": user}],
        )

        response = LLMResponse(
            text=_text_of(raw),
            # `stop_reason` is read by `_refuse_truncated`, which is the reason
            # a cut-off reply is never parsed as JSON. A refusal is passed
            # through as itself rather than mapped onto something else: the
            # caller's schema validation will fail loudly, which is correct.
            stop_reason=getattr(raw, "stop_reason", None) or "end_turn",
            usage=_usage_of(raw, self.model_id),
        )
        self.budget.add(response.usage)
        session_guard().add(response.usage)
        return response


def _text_of(message: Any) -> str:
    """Join the text blocks of a reply, ignoring thinking or tool blocks."""
    blocks = getattr(message, "content", None) or []
    parts = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def _usage_of(message: Any, model_id: str) -> TokenUsage:
    """Build a priced `TokenUsage` from the reply's `usage` block."""
    raw = getattr(message, "usage", None)
    inp = int(getattr(raw, "input_tokens", 0) or 0)
    out = int(getattr(raw, "output_tokens", 0) or 0)
    return TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        calls=1,
        usd=usd_for(model_id, inp, out),
    )


def available(getenv: Callable[[str, str], str] = os.environ.get) -> bool:  # type: ignore[assignment]
    """True when this provider has a credential to use. No network, no import."""
    return bool((getenv(API_KEY_ENV, "") or "").strip())
