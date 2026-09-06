"""
BedrockLLM against a mocked boto3 client. No network, no credentials, no spend.

The client is injected, so nothing here can reach AWS even if the environment
happens to hold live keys. What is worth pinning down, in order of how much it
would hurt to get wrong in the demo:

  * a `max_tokens` stop is refused, not parsed — the reply below is deliberately
    VALID JSON, so a version that parsed truncated text would pass this test by
    returning an object instead of raising;
  * a validation failure re-prompts exactly once and then gives up;
  * the cassette a recording run writes is the one a replay run looks up.
"""
from __future__ import annotations

import json
from dataclasses import replace
from unittest import mock

import pytest
from botocore.exceptions import ClientError

from repro.contracts import Hypothesis
from repro.llm import bedrock
from repro.llm.base import SchemaValidationError
from repro.llm.bedrock import BedrockLLM, RecordingLLM, TruncatedResponseError
from repro.llm.fake import FakeLLM, cassette_key
from repro.settings import HAIKU, Settings, usd_for

GOOD = json.dumps(
    {"file_path": "shopcart/pricing.py", "symbol": "total", "rationale": "x", "confidence": 0.9}
)
# `confidence` is out of the 0..1 bound, so pydantic rejects it. A model that
# returns this has produced well-formed JSON of the wrong shape — the case the
# repair round exists for.
BAD = json.dumps({"file_path": "shopcart/pricing.py", "rationale": "x", "confidence": 7.0})


def reply(text: str, *, stop_reason: str = "end_turn", inp: int = 100, out: int = 50) -> dict:
    """The shape bedrock-runtime `converse` actually returns."""
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out},
    }


def throttle() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse"
    )


def llm(*replies, **kw) -> BedrockLLM:
    """A client whose `converse` returns/raises each of `replies` in turn."""
    client = mock.Mock()
    client.converse.side_effect = list(replies)
    kw.setdefault("sleep", mock.Mock())  # never actually sleep in tests
    return BedrockLLM(client=client, model_id=HAIKU, **kw)


def sent(client_llm: BedrockLLM, call: int = 0) -> dict:
    """The kwargs of the nth `converse` call."""
    return client_llm._client.converse.call_args_list[call].kwargs


# --- complete() ------------------------------------------------------------


def test_complete_returns_text_stop_reason_and_priced_usage():
    client = llm(reply("ready", inp=12_000, out=3_000))
    got = client.complete(system="s", user="u")

    assert got.text == "ready"
    assert got.stop_reason == "end_turn"
    assert got.usage.input_tokens == 12_000
    assert got.usage.output_tokens == 3_000
    assert got.usage.calls == 1
    # 12k in at $1/1M + 3k out at $5/1M = 0.012 + 0.015. Pinned as a literal so a
    # price-table edit has to be deliberate.
    assert got.usage.usd == 0.027
    assert got.usage.usd == usd_for(HAIKU, 12_000, 3_000)


def test_complete_uses_converse_with_the_configured_model_and_ceiling():
    client = llm(reply("ok"))
    client.complete(system="be brief", user="hello", max_tokens=64)

    kwargs = sent(client)
    assert kwargs["modelId"] == HAIKU
    assert kwargs["system"] == [{"text": "be brief"}]
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "hello"}]}]
    assert kwargs["inferenceConfig"]["maxTokens"] == 64


def test_complete_passes_max_tokens_through_rather_than_raising():
    # Only structured() refuses truncated text; free text is the caller's to judge.
    client = llm(reply("half a sen", stop_reason="max_tokens"))
    assert client.complete(system="s", user="u").stop_reason == "max_tokens"


# --- structured(): the happy path and the metric ---------------------------


def test_first_attempt_validation_is_counted():
    client = llm(reply(GOOD))
    obj, response = client.structured(system="s", user="u", schema=Hypothesis)

    assert isinstance(obj, Hypothesis)
    assert obj.file_path == "shopcart/pricing.py"
    assert client._client.converse.call_count == 1
    assert (client.structured_calls, client.first_attempt_validations) == (1, 1)
    assert client.reprompts == 0
    assert client.first_attempt_rate == 1.0
    assert response.usage.calls == 1


def test_system_prompt_carries_the_schema_and_the_no_fence_instruction():
    client = llm(reply(GOOD))
    client.structured(system="you localise bugs", user="u", schema=Hypothesis)

    system = sent(client)["system"][0]["text"]
    assert system.startswith("you localise bugs")  # caller's prompt is not replaced
    assert "Hypothesis" in system
    assert json.dumps(Hypothesis.model_json_schema(), separators=(",", ":")) in system
    assert "no markdown code fence" in system.lower()


def test_fenced_reply_is_parsed():
    client = llm(reply(f"```json\n{GOOD}\n```"))
    obj, _ = client.structured(system="s", user="u", schema=Hypothesis)

    assert obj.confidence == 0.9
    # Still a first-attempt success: the fence is our problem, not a schema failure.
    assert client.first_attempt_validations == 1
    assert client._client.converse.call_count == 1


# --- structured(): the repair round ----------------------------------------


def test_one_reprompt_then_success():
    client = llm(reply(BAD), reply(GOOD))
    obj, response = client.structured(system="s", user="the original ask", schema=Hypothesis)

    assert isinstance(obj, Hypothesis)
    assert client._client.converse.call_count == 2
    assert (client.structured_calls, client.first_attempt_validations) == (1, 0)
    assert client.reprompts == 1
    assert client.first_attempt_rate == 0.0

    repair = sent(client, 1)["messages"][0]["content"][0]["text"]
    assert BAD in repair                    # the reply it must correct
    assert "confidence" in repair           # the pydantic error names the field
    assert "less than or equal to 1" in repair
    assert "the original ask" in repair     # and the request it was answering

    # Both round trips were billed, so the caller must see both.
    assert response.usage.calls == 2
    assert response.usage.input_tokens == 200
    assert response.usage.usd == usd_for(HAIKU, 200, 100)


def test_two_failures_raise_rather_than_returning_a_partial_object():
    client = llm(reply(BAD), reply(BAD))
    with pytest.raises(SchemaValidationError) as exc:
        client.structured(system="s", user="u", schema=Hypothesis)

    assert "Hypothesis" in str(exc.value)
    # Exactly one repair round, never a third try.
    assert client._client.converse.call_count == 2
    assert client.first_attempt_validations == 0


def test_prose_around_the_json_still_gets_one_repair_round():
    client = llm(reply(f"Sure! Here is the object:\n{GOOD}"), reply(GOOD))
    obj, _ = client.structured(system="s", user="u", schema=Hypothesis)

    assert obj.symbol == "total"
    assert client.reprompts == 1


# --- structured(): the truncation rule -------------------------------------


def test_max_tokens_raises_instead_of_parsing():
    # The text is perfectly valid JSON. An implementation that parses before
    # checking stopReason returns an object here and fails this test — which is
    # the whole point: with a real truncated reply the bug shows up as a
    # baffling pydantic error instead of "you hit the ceiling".
    client = llm(reply(GOOD, stop_reason="max_tokens"))
    with pytest.raises(TruncatedResponseError) as exc:
        client.structured(system="s", user="u", schema=Hypothesis, max_tokens=256)

    message = str(exc.value)
    assert "max_tokens" in message
    assert "256" in message
    assert "Hypothesis" in message
    # No repair round: re-prompting cannot fix our own ceiling.
    assert client._client.converse.call_count == 1
    assert client.first_attempt_validations == 0


def test_truncation_is_not_a_schema_error():
    # Callers that catch SchemaValidationError to retry with a different prompt
    # must NOT swallow this one.
    client = llm(reply(GOOD, stop_reason="max_tokens"))
    with pytest.raises(TruncatedResponseError):
        client.structured(system="s", user="u", schema=Hypothesis)
    assert not issubclass(TruncatedResponseError, SchemaValidationError)


def test_truncated_repair_round_also_raises():
    client = llm(reply(BAD), reply(GOOD, stop_reason="max_tokens"))
    with pytest.raises(TruncatedResponseError):
        client.structured(system="s", user="u", schema=Hypothesis)
    assert client._client.converse.call_count == 2


# --- throttling ------------------------------------------------------------


def test_throttling_retries_with_exponential_backoff():
    sleep = mock.Mock()
    client = llm(throttle(), throttle(), reply(GOOD), sleep=sleep)
    obj, _ = client.structured(system="s", user="u", schema=Hypothesis)

    assert isinstance(obj, Hypothesis)
    assert client._client.converse.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]
    # A throttle is not a schema failure: this still counts as first-attempt.
    assert client.first_attempt_validations == 1


def test_throttling_gives_up_after_three_tries():
    client = llm(throttle(), throttle(), throttle())
    with pytest.raises(ClientError) as exc:
        client.complete(system="s", user="u")

    assert exc.value.response["Error"]["Code"] == "ThrottlingException"
    assert client._client.converse.call_count == 3


def test_other_client_errors_are_not_retried():
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "explicit deny"}}, "Converse"
    )
    client = llm(denied, reply(GOOD))
    with pytest.raises(ClientError):
        client.complete(system="s", user="u")
    # Retrying a denial three times just burns the same failure three times.
    assert client._client.converse.call_count == 1


# --- accumulated spend -----------------------------------------------------


def test_client_accumulates_usage_across_calls():
    client = llm(reply(GOOD, inp=1_000, out=1_000), reply("hi", inp=1_000, out=1_000))
    client.structured(system="s", user="u", schema=Hypothesis)
    client.complete(system="s", user="u")

    assert client.usage.calls == 2
    assert client.usage.input_tokens == 2_000
    assert client.usage.usd == round(2 * usd_for(HAIKU, 1_000, 1_000), 6)


# --- RecordingLLM ----------------------------------------------------------


def test_recorded_cassette_is_the_one_replay_looks_up(tmp_path):
    """The round trip that matters: record with Bedrock, replay with FakeLLM.

    If the key were reimplemented instead of imported from fake.py, this is
    where it would show up — as "No cassette", offline, on demo day.
    """
    recorder = RecordingLLM(llm(reply(GOOD)), cassette_dir=tmp_path, enabled=True)
    recorded, _ = recorder.structured(system="sys", user="usr", schema=Hypothesis)

    key = cassette_key("sys", "usr", "Hypothesis")
    assert (tmp_path / f"{key}.json").exists()
    assert recorder.written == [key]

    replayed, response = FakeLLM(tmp_path).structured(system="sys", user="usr", schema=Hypothesis)
    assert replayed == recorded
    assert response.stop_reason == "end_turn"


def test_recorded_completion_replays_under_the_raw_key(tmp_path):
    recorder = RecordingLLM(llm(reply("ready")), cassette_dir=tmp_path, enabled=True)
    recorder.complete(system="sys", user="usr")

    assert (tmp_path / f"{cassette_key('sys', 'usr', 'raw')}.json").exists()
    assert FakeLLM(tmp_path).complete(system="sys", user="usr").text == "ready"


def test_recording_normalises_a_fenced_reply_so_replay_can_parse_it(tmp_path):
    # FakeLLM feeds the cassette straight to model_validate_json, so a fence
    # recorded verbatim would break every replay of this prompt.
    recorder = RecordingLLM(llm(reply(f"```json\n{GOOD}\n```")), cassette_dir=tmp_path, enabled=True)
    obj, _ = recorder.structured(system="sys", user="usr", schema=Hypothesis)

    saved = json.loads((tmp_path / f"{cassette_key('sys', 'usr', 'Hypothesis')}.json").read_text())
    assert json.loads(saved["text"]) == obj.model_dump()
    assert saved["raw_text"].startswith("```")  # the literal reply is kept for the audit trail
    assert FakeLLM(tmp_path).structured(system="sys", user="usr", schema=Hypothesis)[0] == obj


def test_recording_disabled_is_a_pure_passthrough(tmp_path):
    recorder = RecordingLLM(llm(reply(GOOD)), cassette_dir=tmp_path, enabled=False)
    obj, _ = recorder.structured(system="sys", user="usr", schema=Hypothesis)

    assert isinstance(obj, Hypothesis)
    assert list(tmp_path.iterdir()) == []
    assert recorder.written == []


def test_recording_follows_the_record_setting(monkeypatch, tmp_path):
    """`enabled` defaults to `settings().record`.

    Note this patches `settings`, not the environment. `Settings` is a dataclass
    whose defaults call `os.getenv` in the field-default expression, so they are
    evaluated ONCE at import: `monkeypatch.setenv("REPRO_RECORD", "1")` here has
    no effect on `settings().record`. That is fine for `REPRO_RECORD=1 make
    record`, which sets the variable before the process starts, but it means no
    test may set the mode by touching the environment.
    """
    monkeypatch.setattr(bedrock, "settings", lambda: replace(Settings(), record=True))
    assert RecordingLLM(llm(reply(GOOD)), cassette_dir=tmp_path).enabled

    monkeypatch.setattr(bedrock, "settings", lambda: replace(Settings(), record=False))
    assert not RecordingLLM(llm(reply(GOOD)), cassette_dir=tmp_path).enabled


def test_explicit_enabled_beats_the_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(bedrock, "settings", lambda: replace(Settings(), record=False))
    recorder = RecordingLLM(llm(reply(GOOD)), cassette_dir=tmp_path, enabled=True)
    recorder.structured(system="sys", user="usr", schema=Hypothesis)
    assert recorder.written  # a caller that asks to record, records
