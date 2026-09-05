# Evaluation & correctness

Session 3 requires that testing and evaluation appear **in your slides**, and it
gives you the six metrics it wants to see. This document is the source for slide 7.

## Part 1 — How we stop ourselves shipping serious bugs

Five mechanisms, cheapest first. Every one of them runs without spending a cent
of the AWS budget.

### 1. Contract-first, frozen at hour 1
`src/repro/contracts.py` is written before any node exists and is owned by one
person. Five engineers can then build in parallel because they agree on shapes,
not on implementations. `tests/test_contracts.py` fails CI if anyone edits it
quietly. **This is what makes the parallelism safe.**

### 2. The fake LLM (`src/repro/llm/fake.py`)
Record real Bedrock replies once, replay them forever. Consequences:

- the entire graph is unit-testable with **zero tokens and zero flakiness**;
- CI runs the full pipeline on every push, free;
- if the venue wifi or the AWS lease dies at hour 29, `LLM_PROVIDER=fake`
  still gives a complete, honest demo. **Record cassettes by hour 20 and treat
  that as the disaster-recovery plan.**

### 3. Executable invariants
`RunRecord.check_invariants()` encodes the product's safety property as code:

- a patch may never be accepted without a reproduction;
- a patch may never be accepted unless the repro test went green **and** the
  pre-existing suite stayed green;
- no loop may exceed its cap;
- no run may exceed `MAX_RUN_USD`.

The API refuses to emit a patch when the list is non-empty, and the eval harness
asserts it is empty for every case. A bug in the agent that would ship a wrong
patch is therefore a **test failure**, not a demo-day surprise.

### 4. Golden dataset with adversarial cases
`eval/dataset.yaml`. Eight cases, and the mix is the point:

| # | Kind | What it proves |
|---|---|---|
| 1–4 | Reproducible bug, vague complaint | The happy path actually works |
| 5 | Under-specified complaint ("it's broken") | The agent **asks** instead of guessing |
| 6 | **Not a bug** — the client misunderstood the feature | The agent reports NOT_REPRODUCED instead of inventing a fix |
| 7 | Bug whose naive fix breaks another test | The suite gate catches the regression |
| 8 | Bug outside the repo (their own network) | The agent gives up honestly |

Cases 5–8 are worth more than 1–4. Any team can demo a happy path. Showing the
agent decline to act is what separates "functional" from "technically advanced"
on the rubric, and it is the whole story for the video's final thirty seconds.

### 5. CI on every push
`ruff` + `pytest` on GitHub Actions. Branch `main` is protected; work happens on
`feat/<name>` branches and merges by PR. At 30 hours this feels like overhead
for about ten minutes and then saves you at hour 26 when two people's changes
collide.

## Part 2 — The six metrics (slide 7)

Session 3's "Measuring Performance of Digital AI Agents", instantiated for us.
`eval/run_eval.py` prints exactly this table.

| # | Metric | How we compute it | Target for the demo |
|---|---|---|---|
| 1 | **Schema validation pass rate** | share of `structured()` calls whose first reply validates against the Pydantic model | ≥ 95% |
| 2 | **Tool-call success rate** | share of sandbox invocations returning a parseable `ExecutionResult` (a *failing test* counts as success — the tool worked) | ≥ 98% |
| 3 | **Reproduction rate** ⭐ | share of genuinely-buggy cases where a red test was produced | ≥ 6/7 |
| 4 | **False-fix rate** ⭐⭐ | share of runs emitting a patch with **no** verified red→green. **This must be 0. It is structurally 0, and the invariant test proves it.** | 0% |
| 5 | **Loop discipline** | mean repro attempts per case; % of runs hitting the cap | mean ≤ 1.8 |
| 6 | **Token cost per run** | summed from `usage` on every response, priced from `settings.PRICES` | ≤ $0.03 |

Two metrics are ours rather than the deck's, and both map straight back to the
problem statement: **reproduction rate** is the thing the person could not do,
and **false-fix rate** is the harm we exist to prevent.

Report metric 6 in cents on the slide. "One resolved client complaint costs
about two cents of model time against a developer hour" is the single most
quotable number you will have, and it is measured, not claimed.

## Part 3 — Manual verification before you record

Do these in order at hour 24. Do not skip to the recording.

```bash
make test                      # unit + contract + invariant tests, all green
make eval                      # golden dataset, prints the table above
git stash && make eval         # sanity: unchanged tree gives the same numbers
```

Then, by hand:

1. **Break it on purpose.** Point the agent at a demo repo and hand it a
   complaint about a feature that does not exist. It must reach
   `NOT_REPRODUCED`. If it invents a plausible-looking patch, stop everything
   and fix that before anything else — it is the one failure that invalidates
   the pitch.
2. **Regression check.** Manually edit the accepted patch from case 7 to the
   naive wrong fix. The suite gate must reject it.
3. **Cold start.** `git clone` into a fresh directory, `pip install -r
   requirements.txt`, `make test`, `make demo`. Judges run your README. If it
   fails on a clean machine, technical quality is capped at 1 point regardless
   of how good the agent is.
4. **Offline.** Turn off wifi, `LLM_PROVIDER=fake make demo`. It must still run
   end to end.
