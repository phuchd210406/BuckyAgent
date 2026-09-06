"""
The six system prompts. OWNER: Engineer C.

A prompt is product text, so it gets tests like any other product text. Three
things are worth guarding:

  * COST. Every one of these is resent on each step of the loop, and the loop
    runs up to nine model calls, so a prompt that doubles in size doubles a bill
    nobody sees until the invoice.
  * TRUTH. The worked examples name real symbols and real numbers from
    fixtures/demo_repos/shopcart. A fixture that drifts turns the example we
    teach from into a lie, silently.
  * THE CASSETTES. After hour 20 (task C4) a prompt edit changes its
    cassette_key and replay breaks with "No cassette". These tests are where a
    careless edit is supposed to stop.
"""
from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICING = REPO_ROOT / "fixtures" / "demo_repos" / "shopcart" / "shopcart" / "pricing.py"

NODES = ["intake", "clarify", "localiser", "repro_agent", "fix", "reporter"]

#: The brief's ceiling. Under it, a full nine-call run spends under 2,250 tokens
#: on system prompts alone.
MAX_PROMPT_TOKENS = 250


def prompt(node: str) -> str:
    return importlib.import_module(f"repro.agents.{node}").SYSTEM_PROMPT


def estimate_tokens(text: str) -> float:
    """A deliberately PESSIMISTIC token estimate.

    There is no tokenizer in this project's dependencies and the recording run
    is the only place a real count exists, so this bounds from above instead:
    3.5 characters per token (English prose runs nearer 4) and 1.35 tokens per
    whitespace word, whichever is larger. A prompt that passes here is under
    the real ceiling with room to spare; one that fails may still be fine, and
    should be trimmed anyway rather than argued with.
    """
    return max(len(text) / 3.5, len(text.split()) * 1.35)


def shopcart_pricing():
    """The fixture module the repro_agent example is written against."""
    name = "_shopcart_pricing"
    module = types.ModuleType(name)
    module.__file__ = str(PRICING)
    # Registered BEFORE exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], and an unregistered module makes that None.
    sys.modules[name] = module
    try:
        # compile+exec rather than importlib's loader ON PURPOSE. The loader
        # writes __pycache__ INTO the fixture directory, and
        # tests/test_sandbox.py hashes that directory to prove a workspace is
        # an independent copy — so importing the fixture the ordinary way makes
        # an unrelated test fail.
        exec(compile(PRICING.read_text(), str(PRICING), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


# --- every prompt ----------------------------------------------------------


@pytest.mark.parametrize("node", NODES)
def test_every_node_has_a_real_prompt(node):
    text = prompt(node)
    assert text.strip(), f"{node}: SYSTEM_PROMPT is empty"
    assert "TODO" not in text, f"{node}: still a placeholder"


@pytest.mark.parametrize("node", NODES)
def test_every_prompt_is_within_the_token_budget(node):
    text = prompt(node)
    estimated = estimate_tokens(text)
    assert estimated <= MAX_PROMPT_TOKENS, (
        f"{node}: ~{estimated:.0f} tokens ({len(text)} chars) exceeds "
        f"{MAX_PROMPT_TOKENS}. It is resent on every step of the loop."
    )


@pytest.mark.parametrize("node", NODES)
def test_every_prompt_carries_a_worked_example(node):
    text = prompt(node)
    assert re.search(r"Example|GOOD:", text), f"{node}: no worked example"


@pytest.mark.parametrize("node", NODES)
def test_no_prompt_restates_the_json_contract(node):
    """BedrockLLM._system_with_schema appends the schema and the "one JSON
    object, no code fence" instruction to every system prompt. Saying it here
    too pays for the same words twice on every call."""
    text = prompt(node).lower()
    for duplicated in ("json schema", "code fence", "no markdown", "valid json"):
        assert duplicated not in text, f"{node}: the client already says {duplicated!r}"


# --- intake ----------------------------------------------------------------


def test_intake_forbids_inventing():
    text = prompt("intake")
    assert "expected_behaviour null" in text
    assert "never the obvious intermediate step" in text
    assert "fabrications" in text


def test_intake_keeps_missing_for_things_that_actually_block():
    """`missing` is a routing decision, not a completeness report.

    graph.build.route_after_intake sends the run to clarify -- and clarify ENDS
    the run -- if `missing` has a single entry. The first recording (task C4)
    caught the model listing "environment" and a field it had just filled, so
    the flagship demo case never reached the localiser. The prompt has to say
    what the field is FOR.
    """
    text = prompt("intake")
    assert "names of fields in THIS schema and nothing else" in text
    assert "STOPS the run" in text
    assert "could not begin without it" in text
    assert "A field you filled is never missing" in text


def test_intake_example_shows_the_steps_it_refuses_to_invent():
    text = prompt("intake")
    assert 'NOT ["entered password","clicked submit"]' in text


# --- clarify ---------------------------------------------------------------


def test_clarify_bans_the_jargon_and_shows_the_pair():
    text = prompt("clarify")
    for banned in ("file names", "stack traces", "console", "network tab", "reproduce"):
        assert banned in text, f"clarify: does not ban {banned!r}"
    assert "BAD:" in text and "GOOD:" in text
    assert "What HTTP status did the checkout endpoint return?" in text


def test_clarify_asks_for_one_question_not_three():
    """ClarifyingQuestion is ONE question and contracts.py is frozen.

    The brief says three; clarify_node calls llm.structured once for a single
    ClarifyingQuestion, so the prompt follows the code. If the lead adds a list
    wrapper to contracts.py, this test is the reminder to rewrite the prompt.
    """
    from repro.agents.clarify import clarify_node  # noqa: F401  (the node is the contract)

    text = prompt("clarify")
    assert "ONE question" in text
    assert "One question only" in text


# --- localiser -------------------------------------------------------------


def test_localiser_ranks_by_explanation_and_quotes_the_client():
    text = prompt("localiser")
    assert "PRODUCES the observed behaviour" in text
    assert "not the most interesting code" in text
    assert "quotes the client's own words" in text
    assert "Copy file_path exactly" in text
    assert "Never propose a fix" in text


# --- repro_agent -----------------------------------------------------------


def test_repro_prompt_states_the_hard_rules():
    text = prompt("repro_agent")
    assert "Import only from the project and the standard library." in text
    assert "No network" in text
    assert "Never mock or patch the code under test" in text
    assert "not what the code does now" in text


def test_repro_prompt_says_a_passing_test_is_a_failed_reproduction():
    assert "A test that PASSES is a failed reproduction" in prompt("repro_agent")


def test_the_shopcart_example_is_arithmetically_true():
    """The example must actually reproduce the bug, against the real fixture.

    This is the test that catches fixture drift. If someone changes
    FREE_SHIPPING_THRESHOLD, SHIPPING_FLAT or the SAVE10 rate, the worked
    example we teach the model from becomes wrong, and nothing else in the
    suite would notice.
    """
    text = prompt("repro_agent")
    match = re.search(
        r'assert total\(\[Line\("A", ([\d.]+), 1\)\], promo="(\w+)"\) == ([\d.]+)', text
    )
    assert match, "the shopcart example is missing or its shape changed"
    goods, promo, claimed = float(match.group(1)), match.group(2), float(match.group(3))

    pricing = shopcart_pricing()
    actual = pricing.total([pricing.Line("A", goods, 1)], promo=promo)

    # The client's expectation: the basket clears the threshold BEFORE the
    # discount, so postage is free and they pay for goods only.
    assert goods > pricing.FREE_SHIPPING_THRESHOLD
    assert claimed == pytest.approx(pricing.apply_promo(goods, promo))
    # And today's code disagrees, which is what makes the test a reproduction.
    assert actual != pytest.approx(claimed)
    assert actual == pytest.approx(claimed + pricing.SHIPPING_FLAT)


# --- fix -------------------------------------------------------------------


def test_fix_prompt_bans_every_way_of_faking_a_green_test():
    text = prompt("fix")
    assert "change the test file, or any test, in any way" in text
    assert "weaken an assertion" in text
    assert "skip/xfail" in text
    assert "try/except" in text
    assert "reformat, rename or tidy" in text


def test_fix_prompt_has_an_escape_hatch_that_is_not_a_patch():
    """Rewriting an existing test is a human's call, so the model must decline."""
    text = prompt("fix")
    assert "return an empty unified_diff" in text
    assert "design decision for a human" in text


# --- reporter --------------------------------------------------------------


def test_reporter_prompt_does_not_let_the_model_choose_the_verdict():
    text = prompt("reporter")
    assert "The verdict is given to you" in text
    assert "never soften it" in text


def test_reporter_prompt_protects_the_client_from_a_promised_fix():
    text = prompt("reporter")
    assert "not reproduced_and_fixed" in text
    assert "must not imply anything was fixed or shipped" in text


def test_reporter_prompt_covers_both_audiences_with_an_example_each():
    text = prompt("reporter")
    assert "dev_summary" in text and "client_reply" in text
    assert "evidence trail" in text
    assert "Example dev_summary" in text and "Example client_reply" in text
    assert "no jargon" in text
