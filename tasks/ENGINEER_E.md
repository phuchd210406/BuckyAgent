# Engineer E — Evaluation, Demo Assets & Submission

**You own the evidence and the deliverables. You are also the team's QA.**

Four of the five judging criteria are decided by artefacts you own: benefits
(your measured numbers), effectiveness (your golden dataset), originality (your
positioning) and presentation (your deck and video). Technical quality is the
only one you do not own outright — and even there, the judges' entry point is
your README.

Do not let anyone treat this as the leftover role. On a 30-hour clock, this is
the workstream with the highest marginal return, because a working agent that
nobody can evaluate scores about the same as a broken one.

| | |
|---|---|
| **Files you own** | `eval/**`, `fixtures/**`, `README.md`, `docs/*` updates, the deck, the video |
| **Files you may READ but never EDIT** | all `src/**` |
| **You depend on** | Engineer A's `run()` at hour 16 — until then you build fixtures and the harness against `RunRecord` fixtures you write yourself |
| **People depend on you for** | the seeded repos (everyone tests against them) |

Branch `feat/eval`. **Ship the seeded repos by hour 5** — every other engineer
uses them as test input.

---

## E1 · Seven more seeded repos and complaints (3 h — do this first)

```
Read fixtures/BUGS.md and fixtures/demo_repos/shopcart/ as the worked example.

Build seven more cases in the same shape. Each needs: a small stdlib-only Python
package, a green pre-existing pytest suite, exactly one planted defect, and a
CLIENT COMPLAINT written the way a non-technical person actually writes.

The complaints must be BAD. No reproduction steps, no error messages, vague
nouns, some irrelevant detail, occasional wrong self-diagnosis. If our demo
inputs are well-formed GitHub issues, we are demoing a problem that Copilot
already solved and we lose the originality criterion outright.

Required mix:
  1-4. Reproducible bugs, vague complaints. Vary the domain: date handling
       across a month boundary, a pagination off-by-one, a currency rounding
       error, a sort that is unstable on ties.
  5.   Under-specified: the complaint is literally "it's broken, please fix".
       Ground truth = the agent should ASK, not guess. Expected verdict
       NEEDS_CLARIFICATION.
  6.   NOT A BUG: the client misunderstood the feature and the code is correct.
       Expected verdict NOT_REPRODUCED. This is the most important case we have.
  7.   A bug whose OBVIOUS fix breaks a different existing test. Expected: the
       suite gate rejects attempt 1, the agent adapts on attempt 2.
  8.   Root cause outside the repo (the client's own network/browser). Expected
       verdict NOT_REPRODUCED with an honest client reply.

Every repo's own suite must be green before the agent touches it — verify with
pytest and commit only green ones.

Write eval/dataset.yaml with one entry per case: id, repo, complaint,
expected_verdict, expected_files (may be empty), must_not_break (test node ids),
and notes explaining the trap.
```

**Done when:** all eight repos have green suites, and cases 5, 6 and 8 have
`expected_files: []` — the agent is *supposed* to produce nothing there.

---

## E2 · The evaluation harness (2 h)

```
Write eval/run_eval.py.

For each case in dataset.yaml: run the agent, collect the RunRecord, and score:
  - verdict_correct         (matches expected_verdict)
  - reproduced              (any repro attempt with reproduced=True)
  - localisation_hit        (any hypothesis file in expected_files)
  - false_fix               (a patch emitted with NO verified red->green)  <-- must be 0
  - regression              (an accepted fix where must_not_break tests fail)
  - repro_attempts, fix_attempts, llm_calls, input_tokens, output_tokens, usd,
    wall_clock_s
  - schema_first_pass_rate  (from Engineer C's counter)
  - invariants_clean        (check_invariants() == [])

Print the exact six-row metrics table from docs/EVALUATION.md, plus a per-case
breakdown. Write eval/results.json and eval/results.md — results.md gets pasted
straight into the deck.

Default to LLM_PROVIDER=fake so the harness is free and repeatable; a --live
flag runs against Bedrock.

Two hard assertions that exit non-zero:
  - false_fix must be 0 across all cases
  - invariants_clean must be True for every case
If either fails, the build is broken regardless of what else passed. Wire
run_eval.py into CI so a regression here fails the PR.

Build and test this against hand-written RunRecord fixtures BEFORE the real
graph exists, so the harness is ready the moment A's run() lands.
```

**Done when:** `make eval` prints the table and exits 0 on fixtures.

---

## E3 · README (90 min)

```
Write README.md. The judges' rubric says a good README suffices for them to
understand the project, so this is a graded artefact, not housekeeping.

Structure:
  1. One sentence on what it does, and the problem statement, verbatim from
     docs/PROBLEM_STATEMENT.md
  2. The 60-second quickstart: clone, venv, pip install, cp .env.example .env,
     make demo. It MUST work with no AWS credentials at all — LLM_PROVIDER=fake
     is the default for exactly this reason. Test it in a fresh container.
  3. The architecture diagram from docs/ARCHITECTURE.md
  4. File-by-file table: every directory under src/, one line each on purpose.
     This is explicitly asked for in the submission guidelines.
  5. How to run the tests and the eval, with the expected output
  6. The safety property, stated plainly: no patch is ever emitted without a
     test that failed before it and passes after, plus the full suite staying
     green — and a pointer to the exact function that enforces it
     (RunRecord.check_invariants) and the test that proves it
  7. Cost, measured: USD per run
  8. Limitations, honestly listed (from the "What we are NOT building" section)

Point 6 is the one to get right. A judge who follows that pointer and finds the
invariant enforced in code will believe everything else on your slides.
```

**Done when:** a teammate who has not seen the repo can go from `git clone` to a
successful `make demo` in under five minutes, on a clean machine, following only
the README. Actually watch them do it; do not assume.

---

## E4 · The ten slides (3 h, hour 24)

```
Follow the Session 3 sample flow exactly — judges recognise the structure and it
costs nothing to give them.

 1. Title & team — one-line mission: "from 'it's broken' to a failing test"
 2. Problem & why it matters — the POV statement, the 92% missing-steps figure
    with its citation, and a real quote from one of the two people we interviewed
 3. Solution overview — what it does in one sentence, and the ONE differentiator:
    we refuse to emit a patch without a verified reproduction
 4. Methodology — the design-thinking loop and how we scoped down
 5. Technical architecture — the graph diagram, the bounded loops, the stack
 6. Innovation & uniqueness — THE SOLVED-PROBLEM SLIDE. Name Copilot's coding
    agent, Devin and gitagent explicitly. State the three differences from
    docs/PROBLEM_STATEMENT.md. A judge WILL raise this; answering it before they
    ask converts your weakest criterion into your strongest.
 7. Benefits & evaluation — the six-metric table from eval/results.md, with
    false-fix rate at 0% called out, and the cost per run in cents
 8. Demo preview — three screenshots: the failed first repro attempt, the two
    green badges, the client reply
 9. Roadmap — GitHub App and webhooks, more languages, learning which
    hypotheses paid off across runs
10. Conclusion & call to action

Rules from the deck: one core message per slide; no generic claims; every number
sourced. Replace "improves developer productivity" with "reduced a 4-message
client thread to one reply, at 1.9 cents per run".

Tie each slide to a criterion in the speaker notes so nothing is orphaned.
```

**Done when:** every slide has one message and every number has a source.

---

## E5 · The five-minute video (3 h, hour 27)

```
Follow the Session 3 timing exactly. Script it, then record. Do not improvise.

  0:00-0:30  Hook. Read the real client email aloud, on screen, verbatim:
             "hi, i tried to buy stuff this morning and it charged me postage
             even though the site says free postage over $50..."
             Then: "no steps, no error, no browser. 92% of bug reports are
             missing at least one step needed to reproduce them."
  0:30-1:00  Problem. The Monday morning. What the maintainer does today.
  1:00-1:30  Solution in one sentence + why an agent, not a script.
  1:30-3:30  THE DEMO. Paste the email. Then narrate the REASONING, not the UI:
             "it pulled out what she said and marked what she didn't...
              it's guessing pricing.py because she said postage and the code
              says shipping...
              first test it wrote PASSED — so it did not reproduce the bug, and
              it's revising...
              second attempt: RED. Now it has proof.
              here's the patch. The test goes green — and the six existing tests
              stay green. Only now will it show a diff."
             The failed first attempt is the most important 15 seconds in the
             whole video. Do not cut it.
  3:30-4:15  Impact. The metrics table. False-fix rate 0%. Cost per run. Then
             the client reply on screen: the loop closed with a human.
  4:15-5:00  Close. Roadmap, call to action.

Captions throughout — the deck asks for accessibility and rooms are loud.
Record at 1080p, deliver 720p, and check the code is legible after compression
BEFORE you record all five minutes.

Have a backup take recorded with LLM_PROVIDER=fake as insurance.
```

**Done when:** it is under 5:00 with the retry visible and captions on.

---

## E6 · Submission (60 min, hour 29)

Work `docs/DEPLOY.md` Stage 5 line by line. Then, before you submit, do the four
manual checks in `docs/EVALUATION.md` Part 3 **on a clean clone** — especially
the cold start. One submission only; there is no second attempt.
