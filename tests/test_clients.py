"""Which model gets called, and whether anything is called at all.

`fake` is not a cheaper version of a real run -- it can only answer prompts
somebody recorded. Resolving to it silently is therefore a correctness problem
as much as a cost one, so these tests pin what `auto` does with each credential
combination, and `tests/test_api.py` pins that the UI is told the truth about it.
"""
from __future__ import annotations

import pytest

from repro import clients
from repro.llm.anthropic_api import AnthropicLLM, MissingAPIKey
from repro.settings import ANTHROPIC_HAIKU, HAIKU, model_id_for, usd_for


@pytest.fixture(autouse=True)
def _no_inherited_credentials(monkeypatch):
    for name in (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_auto_prefers_the_key_that_does_not_expire(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    assert clients.resolve_provider("auto") == "anthropic"


def test_auto_falls_back_to_bedrock_when_that_is_all_there_is(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    assert clients.resolve_provider("auto") == "bedrock"


def test_auto_with_no_credentials_at_all_replays_rather_than_crashing():
    """A missing key must degrade to something that runs, and say so elsewhere."""
    assert clients.resolve_provider("auto") == "fake"


def test_an_explicit_provider_is_never_second_guessed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert clients.resolve_provider("fake") == "fake"


def test_the_environment_is_read_at_call_time_not_at_import(monkeypatch):
    """A key exported after this module loaded still counts."""
    assert clients.credential_for("anthropic") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert clients.credential_for("anthropic") is True


def test_an_unknown_provider_names_the_ones_that_exist():
    with pytest.raises(ValueError) as raised:
        clients.llm_for("gpt")
    assert "anthropic" in str(raised.value) and "bedrock" in str(raised.value)


def test_the_anthropic_client_refuses_to_start_without_a_key():
    """Raised before any network call, and it names the variable to set."""
    with pytest.raises(MissingAPIKey) as raised:
        AnthropicLLM()
    assert "ANTHROPIC_API_KEY" in str(raised.value)


# --- pricing ------------------------------------------------------------------
def test_both_haikus_are_priced_and_priced_the_same():
    """The cost counter is only honest if the id it is given has a row."""
    assert usd_for(HAIKU, 1_000_000, 0) == 1.0
    assert usd_for(ANTHROPIC_HAIKU, 1_000_000, 0) == 1.0
    assert usd_for(ANTHROPIC_HAIKU, 0, 1_000_000) == 5.0


def test_the_model_a_provider_would_use_is_answerable_without_calling_it(monkeypatch):
    assert model_id_for("anthropic") == ANTHROPIC_HAIKU
    assert model_id_for("bedrock") == HAIKU
    # `fake` calls nothing, so naming a model for it would be a lie.
    assert model_id_for("fake") == ""
