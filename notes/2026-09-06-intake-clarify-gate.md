# What the real Bedrock recordings found

**From:** Engineer C (model layer) · **For:** Engineer A (orchestration)
**Date:** 2026-09-06, from the task C4 recordings
**Files:** `src/repro/graph/build.py`, `src/repro/agents/fix.py`,
`src/repro/graph/sandbox_seam.py` (all yours)

Started as the intake-gate write-up. The filename is cited from `build.py:67`
and `tasks/prompt.md`, so it keeps its name and collects the rest.

---

# 1. The intake gate ended every realistic run at the first node

**STATUS: FIXED** in `4d5cd5b`, and fixed better than I suggested — checking
`missing` against the actual output (`if not getattr(facts, name, None)`) also
disposes of the model listing a field it had just filled, which was the one case
my prompt changes could not reach. Kept here for the record.

## What happened

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

* `src/repro/graph/*` is read-only for me, so I did not change the router.
* **Cassette keys are live.** A cassette key is a hash of the exact system
  prompt, so any edit to a `SYSTEM_PROMPT` changes its key and replay breaks
  with "No cassette". Re-recorded after the router fix landed.

---

# 2. The fix node is asked to patch a file it has never seen

**STATUS: OPEN.** Found in the recording taken after the router fix. The run now
gets much further — it reproduces the bug with a real failing test — and then
loses all three fix attempts to the same cause.

## What happens

Every patch the model produced invents the file it is patching:

```diff
-def shipping_for(subtotal):
+def shipping_for(subtotal, discount=0):
-    if subtotal > 50:
+    if subtotal - discount > 50:
     return 5.95
```

The real function in `fixtures/demo_repos/shopcart/shopcart/pricing.py` has a
different signature, a different docstring, and named constants
(`FREE_SHIPPING_THRESHOLD`, `SHIPPING_FLAT`) where the model wrote the literals
`50`, `5.95` and `5.99`. `git apply` rejects all three, correctly — those
context lines do not exist. Three attempts, three rejections, verdict
`reproduced_not_fixed`.

## Why no prompt can fix it

`fix_node`'s payload is `facts`, `hypotheses`, `failing_test`, `failure` and
`rejected`. `Hypothesis` carries `file_path`, `symbol`, `rationale` and
`confidence` — no code. The `Sandbox` protocol has `search`, `rank_candidates`,
`write_test`, `run_test`, `run_suite`, `apply_patch` and `revert_patch`, and no
way to read a file at all.

So the model is asked for a valid unified diff, with real context lines, against
source it has never been shown. Inventing the context is the only move available
to it. I would rather not spend another recording establishing that again.

## What would fix it

Put the code in front of the model. Two shapes, both yours:

* **cheapest** — the localiser already has the snippet. `retrieval.search()`
  returns `(rel_path, snippet, score)`, and the snippet is the matching
  function's body capped at 60 lines. Carrying the chosen hit's snippet into the
  fix payload needs no new plumbing and no tokens beyond the snippet itself.
* **more general** — add `read_file(rel_path, max_chars)` to the `Sandbox`
  protocol, delegating to `Workspace.read_file`, which is already bounded and
  already truncates. ARCHITECTURE.md's allow-list has "read a file" in it, so
  this is inside the contract rather than an extension of it.

I would take the first. It adds no seam method, and a 60-line function body is
the right size for the minimal patch the fix prompt asks for anyway.

## While it is open

`make demo` reaches `reproduced_not_fixed`, which is honest and still a decent
demo — we reproduce the client's bug with a real failing test they can watch go
red. But metric 4 on the evaluation slide (false-fix rate) is trivially 0
because no patch is ever accepted, and the "reproduced and fixed" story on the
deck has no run behind it yet.
