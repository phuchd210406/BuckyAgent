# Prompt for Engineer A's session — fix the intake clarify gate

Written by Engineer C on 2026-09-06, after the first real Bedrock recording
(task C4). Background and evidence: `notes/2026-09-06-intake-clarify-gate.md`.

Paste everything inside the fence below into Engineer A's Claude Code session.

```
Fix the intake quality gate in src/repro/graph/build.py. It currently ends
almost every real run at the first node.

## The problem

route_after_intake sends a run to clarify when facts.missing holds a SINGLE
entry (build.py:95), and clarify is terminal — graph.add_edge("clarify", END)
at build.py:243. So one conservative entry from the model ends the run before
the localiser has looked at the repository at all.

Your own comment at build.py:49 documents the intended gate as
"confidence < 0.6 or missing critical". The confidence half exists as
CLARIFY_CONFIDENCE_FLOOR. The "critical" half was never implemented, so the
rule in practice is "any missing".

## Evidence

Engineer C ran the shopcart case end to end against real Bedrock three times
while recording cassettes. Haiku 4.5 returned a non-empty `missing` every time:

  1. ["expected_behaviour", "environment", "steps"]  — includes a field it had
     just filled; "environment" blocks nothing
  2. ["order_id", "basket_total", "postage_amount_charged"] — none of these are
     fields of ReportFacts at all
  3. ["expected_behaviour"] — a real field, but again one it had filled

The intake prompt was already tightened twice, which fixed rows 1 and 2. Row 3
survives because the client never literally spelled out their expectation, and
most client complaints never do. Every one of those runs ends as
needs_clarification: our flagship demo case never reaches the localiser, never
writes a repro test, and never produces a patch.

Full write-up with the reasoning: notes/2026-09-06-intake-clarify-gate.md

## What to change

In build.py, gate on fields the localiser genuinely cannot start without:

    #: A field the localiser cannot start without. An absent environment, or one
    #: more step the reporter did not spell out, is not a reason to stop the run
    #: and go back to the client.
    CRITICAL_FACTS = frozenset({"observed_behaviour", "entrypoint_hint"})

    if set(facts.missing) & CRITICAL_FACTS:
        return "clarify"

Set membership also makes the router robust to the model naming a field that
does not exist, which it did on one run in three. Keep the confidence floor as
it is. If you prefer a different set of critical fields, say which and why —
the shape of the fix matters more than the exact membership.

## Constraints

- Do NOT change the SYSTEM_PROMPT constants in src/repro/agents/*. Engineer C
  owns those, and a cassette key is a hash of the exact prompt: editing one
  breaks replay until C re-records.
- Do NOT change src/repro/contracts.py. It is frozen.
- The alternative fix — weakening the intake prompt's rule about `missing` — is
  a decision for the lead, not a change to make here. tasks/ENGINEER_C.md tells
  C to put "expected_behaviour" in `missing` whenever the reporter did not state
  it, and that instruction and this router cannot both stand.

## Expected test fallout — read this before "fixing" it

After this change, tests/test_replay_fidelity.py::
test_every_recorded_case_replays_to_its_recorded_verdict WILL FAIL with
"No cassette <hash>". That is correct and expected: the recorded run stopped at
clarify, so there are only two cassettes; once the router lets the run continue
to the localiser, it asks for a reply that was never recorded.

Do not delete or edit the cassettes in src/repro/llm/cassettes/, do not weaken
or skip that test, and do not change eval/recorded_runs.json. Re-recording costs
real money and Engineer C is the budget owner. Leave the failure in place and
tell C the router change has landed so they can re-record.

## Also update

- tests/test_routers.py — it asserts the current behaviour directly (see the
  route_after_intake cases around line 69). Update those and add cases for a
  non-critical `missing` entry routing to localise, and for a field name that is
  not in ReportFacts being ignored rather than blocking.
- tests/test_graph.py if any end-to-end case depends on the old routing.

## Verify

    make test     # everything green except the replay-fidelity failure above
    make lint

Then report which tests you changed and confirm the replay-fidelity failure is
the only one left.
```

## Two things to watch in the resulting diff

1. **The "expected test fallout" section is the part most likely to be ignored.**
   An agent trying to get the suite fully green will reach for the cassettes in
   `src/repro/llm/cassettes/`, for `eval/recorded_runs.json`, or for a skip
   marker on the fidelity test. Check the diff touches none of those.
2. **Ping Engineer C once the router lands.** The re-record after this fix will
   run through repro and fix for the first time, so it costs more than the
   ~$0.0026 the intake-only runs have been costing — and it is the first real
   test of whether the repro and fix prompts work at all.
