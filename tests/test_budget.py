"""
Spending caps. OWNER: Engineer C (task C5).

The property under test is BEFORE, not AFTER. A guard that adds up what a run
has spent and complains afterwards has already paid for the call you wanted to
stop; the only version that saves money refuses to make it. So the assertions
here are as much about `converse.call_count` as about the exception.
"""
from __future__ import annotations

import threading
from unittest import mock

import pytest
from botocore.exceptions import ClientError

from repro.contracts import MAX_RUN_USD, TokenUsage
from repro.llm import budget as budget_module
from repro.llm.bedrock import BedrockLLM
from repro.llm.budget import (
    DEFAULT_SESSION_BUDGET_USD,
    SESSION_BUDGET_ENV,
    BudgetExceeded,
    BudgetGuard,
    SessionBudgetExceeded,
    estimate_call_usd,
    session_budget_usd,
    session_guard,
)
from repro.settings import HAIKU, usd_for

#: $5/1M output on Haiku, so a 20k-token ceiling estimates at exactly $0.10 and
#: makes the arithmetic in these tests readable.
CEILING = 20_000
PER_CALL_USD = 0.10


def reply(out_tokens: int = CEILING, in_tokens: int = 0) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens},
    }


def llm(*replies, **kw) -> BedrockLLM:
    client = mock.Mock()
    client.converse.side_effect = list(replies)
    kw.setdefault("sleep", mock.Mock())
    return BedrockLLM(client=client, model_id=HAIKU, **kw)


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_the_offending_call_is_never_made():
    """The one that matters: refused BEFORE the request leaves, not after.

    Three calls at $0.10 against a $0.25 cap. The third would reach $0.30, so it
    must not happen at all -- and the run must still be under its cap afterwards,
    which is what distinguishes this from a guard that notices too late.
    """
    client = llm(*[reply() for _ in range(3)], budget=BudgetGuard(limit_usd=0.25))

    for _ in range(2):
        client.complete(system="s", user="u", max_tokens=CEILING)
    assert client._client.converse.call_count == 2
    assert client.budget.usd == pytest.approx(0.20)

    with pytest.raises(BudgetExceeded) as exc:
        client.complete(system="s", user="u", max_tokens=CEILING)

    assert client._client.converse.call_count == 2, "the refused call was still made"
    assert client.budget.usd == pytest.approx(0.20), "spend moved without a call"
    assert client.budget.usd <= 0.25, "the cap was broken before it was enforced"
    assert "was NOT made" in str(exc.value)


def test_the_message_says_what_was_spent_and_what_was_refused():
    client = llm(reply(), budget=BudgetGuard(limit_usd=0.15))
    client.complete(system="s", user="u", max_tokens=CEILING)

    with pytest.raises(BudgetExceeded) as exc:
        client.complete(system="s", user="u", max_tokens=CEILING)

    message = str(exc.value)
    assert "0.100000" in message  # already spent
    assert "0.15" in message  # the cap
    assert "run budget exceeded" in message


def test_landing_exactly_on_the_cap_is_allowed():
    """`>` not `>=`, to agree with check_invariants and budget_exhausted."""
    guard = BudgetGuard(limit_usd=0.25)
    guard.add(TokenUsage(usd=0.20, calls=1))

    guard.check(0.05)  # exactly 0.25: spent, not overspent
    with pytest.raises(BudgetExceeded):
        guard.check(0.050001)


def test_a_guard_with_no_room_refuses_the_very_first_call():
    client = llm(reply(), budget=BudgetGuard(limit_usd=0.0))

    with pytest.raises(BudgetExceeded):
        client.complete(system="s", user="u", max_tokens=CEILING)
    assert client._client.converse.call_count == 0


def test_the_repair_round_is_checked_too():
    """structured() can make two calls; the second gets its own gate."""
    from repro.contracts import Hypothesis

    bad = '{"file_path": "a.py", "rationale": "x", "confidence": 7.0}'
    client = llm(
        {**reply(), "output": {"message": {"content": [{"text": bad}]}}},
        reply(),
        budget=BudgetGuard(limit_usd=0.15),
    )

    with pytest.raises(BudgetExceeded):
        client.structured(system="s", user="u", schema=Hypothesis, max_tokens=CEILING)
    assert client._client.converse.call_count == 1, "the re-prompt was not gated"


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_the_guard_is_the_clients_one_record_of_spend():
    client = llm(reply(out_tokens=1_000, in_tokens=1_000), reply(out_tokens=1_000, in_tokens=1_000))
    client.complete(system="s", user="u")
    client.complete(system="s", user="u")

    assert client.usage is client.budget.usage
    assert client.budget.calls == 2
    assert client.budget.usd == pytest.approx(2 * usd_for(HAIKU, 1_000, 1_000))


def test_defaults_to_the_contract_cap():
    assert BudgetGuard().limit_usd == MAX_RUN_USD
    assert llm().budget.limit_usd == MAX_RUN_USD


def test_remaining_never_goes_negative():
    guard = BudgetGuard(limit_usd=0.25)
    guard.add(TokenUsage(usd=0.10))
    assert guard.remaining_usd == pytest.approx(0.15)

    guard.add(TokenUsage(usd=0.90))
    assert guard.remaining_usd == 0.0


def test_add_records_a_completed_call_without_raising():
    """The money is already gone; an exception here would lose the paid-for reply."""
    guard = BudgetGuard(limit_usd=0.01)
    guard.add(TokenUsage(usd=5.0, calls=1))  # must not raise

    assert guard.usd == 5.0
    with pytest.raises(BudgetExceeded):
        guard.check()  # but nothing more may be spent


def test_a_throttled_call_costs_nothing():
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")
    client = llm(throttle, reply(out_tokens=1_000), budget=BudgetGuard(limit_usd=0.25))

    client.complete(system="s", user="u")
    assert client.budget.calls == 1


def test_reset_clears_the_spend():
    guard = BudgetGuard(limit_usd=0.25)
    guard.add(TokenUsage(usd=0.20, calls=2))
    guard.reset()

    assert (guard.usd, guard.calls) == (0.0, 0)


def test_concurrent_adds_do_not_lose_spend():
    """run() is documented as safe to call concurrently; a shared guard must be too."""
    guard = BudgetGuard(limit_usd=1e9)
    threads = [
        threading.Thread(target=lambda: [guard.add(TokenUsage(usd=0.001, calls=1)) for _ in range(50)])
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert guard.calls == 400


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


def test_the_estimate_assumes_the_worst_case_reply():
    """It must never under-estimate, or a call slips through the gate."""
    system, user = "s" * 3_500, "u" * 3_500
    estimated = estimate_call_usd(HAIKU, system=system, user=user, max_tokens=CEILING)

    # 7000 chars at 3.5 chars/token = 2000 input tokens, plus the full ceiling out.
    assert estimated == pytest.approx(usd_for(HAIKU, 2_000, CEILING))
    # A real reply is shorter than the ceiling, so the estimate bounds it above.
    assert estimated >= usd_for(HAIKU, 2_000, CEILING // 2)


def test_a_bigger_ceiling_costs_more_to_ask_for():
    small = estimate_call_usd(HAIKU, system="s", user="u", max_tokens=1_000)
    large = estimate_call_usd(HAIKU, system="s", user="u", max_tokens=100_000)
    assert large > small


# ---------------------------------------------------------------------------
# The session guard
# ---------------------------------------------------------------------------


def test_the_session_cap_defaults_to_one_dollar():
    assert session_budget_usd() == DEFAULT_SESSION_BUDGET_USD
    assert session_guard().limit_usd == DEFAULT_SESSION_BUDGET_USD


def test_the_session_cap_is_read_at_call_time(monkeypatch):
    """Not through Settings, which freezes its os.getenv defaults at import.

    A cap that silently does not apply is worse than no cap at all.
    """
    monkeypatch.setenv(SESSION_BUDGET_ENV, "3.50")
    assert session_budget_usd() == 3.50
    assert session_guard().limit_usd == 3.50

    monkeypatch.setenv(SESSION_BUDGET_ENV, "0.25")
    assert session_guard().limit_usd == 0.25


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "-5"])
def test_an_unusable_cap_falls_back_rather_than_meaning_no_cap(monkeypatch, raw):
    monkeypatch.setenv(SESSION_BUDGET_ENV, raw)
    assert session_budget_usd() == DEFAULT_SESSION_BUDGET_USD


def test_the_session_guard_stops_the_process_spending(monkeypatch):
    monkeypatch.setenv(SESSION_BUDGET_ENV, "0.25")
    client = llm(*[reply() for _ in range(4)], budget=BudgetGuard(limit_usd=1e9))

    for _ in range(2):
        client.complete(system="s", user="u", max_tokens=CEILING)

    with pytest.raises(SessionBudgetExceeded):
        client.complete(system="s", user="u", max_tokens=CEILING)
    assert client._client.converse.call_count == 2


def test_the_session_guard_latches_across_clients(monkeypatch):
    """The lunchtime scenario: a fresh run must not resume spending.

    Each run gets its own BudgetGuard, so without a latch a runaway loop that
    starts a new run every time would keep paying, one run's cap at a time.
    """
    monkeypatch.setenv(SESSION_BUDGET_ENV, "0.15")
    first = llm(reply(), budget=BudgetGuard(limit_usd=1e9))
    first.complete(system="s", user="u", max_tokens=CEILING)

    with pytest.raises(SessionBudgetExceeded):
        first.complete(system="s", user="u", max_tokens=CEILING)

    # A brand-new client, a brand-new run guard, the same stopped process.
    monkeypatch.setenv(SESSION_BUDGET_ENV, "1000.0")
    second = llm(reply(), budget=BudgetGuard(limit_usd=1e9))
    with pytest.raises(SessionBudgetExceeded, match="already exceeded"):
        second.complete(system="s", user="u", max_tokens=CEILING)
    assert second._client.converse.call_count == 0
    assert session_guard().tripped


def test_the_latch_can_be_released_deliberately(monkeypatch):
    monkeypatch.setenv(SESSION_BUDGET_ENV, "0.05")
    client = llm(reply(), budget=BudgetGuard(limit_usd=1e9))
    with pytest.raises(SessionBudgetExceeded):
        client.complete(system="s", user="u", max_tokens=CEILING)

    monkeypatch.setenv(SESSION_BUDGET_ENV, "10.0")
    budget_module.reset_session_budget()

    assert not session_guard().tripped
    client.complete(system="s", user="u", max_tokens=CEILING)
    assert client._client.converse.call_count == 1


def test_session_exceeded_is_a_budget_exceeded():
    """A caller that catches BudgetExceeded must not miss the session cap."""
    assert issubclass(SessionBudgetExceeded, BudgetExceeded)
