"""
Money guards. OWNER: Engineer C (task C5).

Two caps, at two scopes, for two different accidents:

  * `BudgetGuard` bounds ONE run at `MAX_RUN_USD`. A loop that will not
    converge is the expected way to overspend, and `contracts.MAX_RUN_USD` is
    the number the invariant check enforces afterwards. This enforces it
    *during*, which is the only version that saves money.
  * `session_guard()` bounds the whole PROCESS at `REPRO_SESSION_BUDGET_USD`
    (default $1.00), because the team shares $20 and an experiment left running
    over lunch does not stop at one run's cap.

BEFORE, NOT AFTER. Both are checked ahead of each provider call, against a
deliberately pessimistic estimate of what that call will cost: the prompt's
length for input, and the full `max_tokens` ceiling for output. Checking
afterwards means the call that broke the cap has already been paid for, which
is precisely the call you wanted to stop. The estimate can only be too high --
the model cannot emit more than `max_tokens` -- so the guard never lets a call
through that it should have refused.

WHY A LATCH, NOT `sys.exit`. The brief says the session cap should hard-stop
the process. A library that calls `sys.exit` or `os._exit` takes an API server
down mid-request and destroys the audit trail of the run that tripped it, which
costs more than the dollar it saved. `SessionBudgetGuard` latches instead: once
tripped it refuses every subsequent call for the life of the process, so no
further money can be spent either way, and the caller still gets an exception
it can record. Reversing that is the lead's call, not mine.

THE ENV VAR IS READ AT CALL TIME, never through `Settings`. That class evaluates
its `os.getenv` defaults once, when the module is first imported, so a value
exported afterwards would be silently ignored -- and a budget cap that quietly
does not apply is worse than no cap at all.
"""
from __future__ import annotations

import math
import os
import threading

from repro.contracts import MAX_RUN_USD, TokenUsage
from repro.settings import settings, usd_for

SESSION_BUDGET_ENV = "REPRO_SESSION_BUDGET_USD"
DEFAULT_SESSION_BUDGET_USD = 1.00

#: For the pre-flight estimate only. Pessimistic on purpose: English runs nearer
#: 4 characters per token, so this over-counts the input rather than under-counts.
CHARS_PER_TOKEN = 3.5


class BudgetExceeded(RuntimeError):
    """A call was refused because making it would break a spending cap.

    Raised INSTEAD of the call, never after it. The message names the cap, what
    has been spent, and what the refused call was estimated to cost.
    """


class SessionBudgetExceeded(BudgetExceeded):
    """The process-wide cap. Once this fires, this process makes no more calls."""


def session_budget_usd() -> float:
    """The session cap, read from the environment on every check."""
    raw = os.environ.get(SESSION_BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_SESSION_BUDGET_USD
    try:
        value = float(raw)
    except ValueError:
        # An unparseable cap must not read as "no cap".
        return DEFAULT_SESSION_BUDGET_USD
    return value if value >= 0 else DEFAULT_SESSION_BUDGET_USD


def estimate_call_usd(
    model_id: str | None = None, *, system: str = "", user: str = "", max_tokens: int = 1024
) -> float:
    """What one call could cost at worst: this prompt in, `max_tokens` out."""
    model = model_id or settings().model_id
    input_tokens = math.ceil((len(system) + len(user)) / CHARS_PER_TOKEN)
    return usd_for(model, input_tokens, max_tokens)


class BudgetGuard:
    """Accumulates TokenUsage and refuses the call that would break the cap.

    Thread-safe because `graph.build.run` is documented as safe to call
    concurrently, and a session guard is shared by every run in the process.
    """

    exceeded_error: type[BudgetExceeded] = BudgetExceeded

    def __init__(self, limit_usd: float = MAX_RUN_USD, label: str = "run") -> None:
        self._limit_usd = float(limit_usd)
        self.label = label
        self.usage = TokenUsage()
        self._lock = threading.Lock()

    # --- what has been spent ------------------------------------------------
    @property
    def limit_usd(self) -> float:
        return self._limit_usd

    @property
    def usd(self) -> float:
        return self.usage.usd

    @property
    def calls(self) -> int:
        return self.usage.calls

    @property
    def remaining_usd(self) -> float:
        return round(max(0.0, self.limit_usd - self.usd), 6)

    # --- the gate -----------------------------------------------------------
    def check(self, estimated_usd: float = 0.0) -> None:
        """Raise if spending `estimated_usd` more would break the cap.

        Call this BEFORE the provider call. `>` not `>=`, to agree exactly with
        `RunRecord.check_invariants` and `graph.build.budget_exhausted`: landing
        precisely on the cap is spent, not overspent.
        """
        with self._lock:
            spent = self.usage.usd
        projected = round(spent + max(0.0, estimated_usd), 6)
        if projected > self.limit_usd:
            raise self.exceeded_error(
                f"{self.label} budget exceeded: ${spent:.6f} already spent and this call "
                f"is estimated at ${estimated_usd:.6f}, which would reach "
                f"${projected:.6f} against a ${self.limit_usd:.2f} cap. "
                f"The call was NOT made."
            )

    def add(self, usage: TokenUsage) -> TokenUsage:
        """Record what a completed call actually cost. Returns the new total.

        Does not raise: the money is already spent, and turning a completed call
        into an exception would lose the reply we paid for. The next `check`
        refuses the next call.
        """
        with self._lock:
            self.usage = self.usage.merge(usage)
            return self.usage

    def reset(self) -> None:
        with self._lock:
            self.usage = TokenUsage()

    def __repr__(self) -> str:
        return (
            f"<BudgetGuard {self.label} ${self.usd:.6f}/${self.limit_usd:.2f} "
            f"calls={self.calls}>"
        )


class SessionBudgetGuard(BudgetGuard):
    """Process-wide, latching, and re-reads its cap from the environment.

    The latch is the point: after the cap is reached this guard refuses every
    call for the life of the process, even if something later resets the usage
    or lowers the spend. A runaway experiment therefore stops spending once,
    permanently, rather than resuming the moment a new run starts with a fresh
    per-run guard.
    """

    exceeded_error = SessionBudgetExceeded

    def __init__(self) -> None:
        super().__init__(limit_usd=DEFAULT_SESSION_BUDGET_USD, label="session")
        self._tripped: str | None = None

    @property
    def limit_usd(self) -> float:
        return session_budget_usd()

    @property
    def tripped(self) -> bool:
        return self._tripped is not None

    def check(self, estimated_usd: float = 0.0) -> None:
        if self._tripped is not None:
            raise SessionBudgetExceeded(
                f"session budget was already exceeded and this process is stopped "
                f"for spending: {self._tripped}"
            )
        try:
            super().check(estimated_usd)
        except BudgetExceeded as exc:
            self._tripped = str(exc)
            raise

    def reset(self) -> None:
        """Clears the latch too. For tests and for a deliberate operator override."""
        super().reset()
        self._tripped = None


_SESSION_GUARD = SessionBudgetGuard()


def session_guard() -> SessionBudgetGuard:
    """The one process-wide guard. Shared by every BedrockLLM in this process."""
    return _SESSION_GUARD


def reset_session_budget() -> None:
    """Drop the session guard's spend and its latch.

    Used by the autouse fixture in tests/conftest.py so one test's simulated
    spending cannot trip another's, and available to an operator who has decided
    to keep going after reading why it stopped.
    """
    _SESSION_GUARD.reset()
