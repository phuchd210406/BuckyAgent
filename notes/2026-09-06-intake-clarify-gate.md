# The intake gate ends every realistic run at the first node

**From:** Engineer C (model layer) · **For:** Engineer A (orchestration)
**Date:** 2026-09-06, after the first real Bedrock recording (task C4)
**Files:** `src/repro/graph/build.py` (yours), `src/repro/agents/intake.py` (mine)

## What happens

`route_after_intake` sends a run to clarify when `facts.missing` holds a
**single** entry:

```python
# src/repro/graph/build.py:95
if facts.missing:
    return "clarify"
```

and clarify is terminal — `graph.add_edge("clarify", END)` at `build.py:243`.
So one conservative entry from the model ends the run before the localiser has
looked at the repository at all.

The gate you documented directly above it is not the one that got built:

```python
# src/repro/graph/build.py:49
# quality gate from docs/ARCHITECTURE.md ("confidence < 0.6 or missing critical").
```

The confidence half exists as `CLARIFY_CONFIDENCE_FLOOR`. The *critical* half
was never implemented, so in practice the rule is "any missing".

## Evidence

I ran the shopcart case end to end against real Bedrock three times while
recording cassettes (task C4, about $0.008 in total). Haiku 4.5 returned a
non-empty `missing` on every single run:

| Run | `missing` | Problem |
|---|---|---|
| 1 | `["expected_behaviour", "environment", "steps"]` | lists a field it had just filled; `environment` blocks nothing |
| 2 | `["order_id", "basket_total", "postage_amount_charged"]` | none of these are fields of `ReportFacts` |
| 3 | `["expected_behaviour"]` | a real field, but again one it had filled |

Every one of those runs ends as `needs_clarification`. Our flagship demo case
never reaches the localiser, never writes a repro test, and never produces a
patch.

I tightened the intake prompt twice between those runs, which fixed rows 1 and
2 — it now states that `missing` holds names of fields in *this* schema and
nothing else, that a field you filled is never missing, and that it is usually
empty. Row 3 survives, because the client never *literally* spelled out their
expectation, and most client complaints never do.

The recorded cassettes are committed, so `LLM_PROVIDER=fake` reproduces this
offline for free — see `eval/recorded_runs.json` and
`tests/test_replay_fidelity.py`.

## Suggested fix

In `build.py`, which is yours:

```python
#: A field the localiser cannot start without. An absent environment, or one
#: more step the reporter did not spell out, is not a reason to stop the run
#: and go back to the client.
CRITICAL_FACTS = frozenset({"observed_behaviour", "entrypoint_hint"})

if set(facts.missing) & CRITICAL_FACTS:
    return "clarify"
```

This matches the gate in your own comment, and it is robust to the model
naming a field that does not exist — which it did on one run in three.

## The decision that is not mine

`tasks/ENGINEER_C.md` tells me to write the intake prompt so that:

> If they did not state expected behaviour, expected_behaviour is null and
> "expected_behaviour" goes in missing.

Following that instruction guarantees a clarify for nearly every real client
complaint, because people describe what went wrong far more often than they
state what they expected instead. That brief and the current router cannot both
stand.

My vote is to change the router. The alternative is to weaken the intake
prompt's anti-fabrication rule, which is the one thing that instruction exists
to protect — a fabricated fact sends every later stage after the wrong code.

## Two things to know before touching this

* `src/repro/graph/*` is read-only for me, so I have not changed the router.
  The prompt-side improvements are already committed on `feat/model`.
* **Cassette keys are live now.** A cassette key is a hash of the exact system
  prompt, so any edit to `intake.py`'s `SYSTEM_PROMPT` changes its key and
  replay breaks with "No cassette". That is intentional. If the resolution
  involves changing the prompt rather than the router, tell me and I will
  re-record.
