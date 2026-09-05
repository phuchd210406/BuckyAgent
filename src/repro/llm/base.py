"""The one seam between the agents and any model provider."""
from __future__ import annotations

from typing import Protocol, Type, TypeVar

from pydantic import BaseModel

from repro.contracts import LLMResponse

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    """Every agent depends on THIS, never on boto3 directly.

    Because this is a Protocol, `FakeLLM` satisfies it without inheritance,
    which is what lets the whole graph be tested for zero tokens.
    """

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        ...

    def structured(
        self, *, system: str, user: str, schema: Type[T], max_tokens: int = 1024
    ) -> tuple[T, LLMResponse]:
        """Return a validated instance of `schema`.

        Implementations MUST re-prompt at most once on a validation error and
        MUST raise `SchemaValidationError` on the second failure rather than
        returning a partially-filled object.
        """
        ...


class SchemaValidationError(RuntimeError):
    """Raised when the model could not be made to produce the required shape."""
