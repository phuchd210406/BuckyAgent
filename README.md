# Repro — from "it's broken" to a failing test

Repro reads a non-technical client complaint, finds the suspect code, **writes a
test that fails for the reason the client described**, and only then proposes a
patch — which it accepts solely when that test goes green *and* the project's
existing suite stays green.

## 1. The problem

Verbatim from [`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md):

> **A solo maintainer at a small software agency, on the Monday morning a client
> emails "the checkout is broken, please fix ASAP", needs a way to turn that one
> sentence into a test that fails for the reason the client is describing — before
> anyone touches the code — because 92% of studied bug reports are missing at least
> one step needed to reproduce them (Johnson et al., "An Empirical Investigation
> into the Reproduction of Bug Reports for Android Apps", arXiv:2301.01235, 2023),
> and developers spend 35–50% of their working time validating and debugging
> (Layman & Zazworka, "The Debugging Mindset", ACM Queue, 2017).**

Copilot's coding agent, Devin, Cursor and Sweep all fix bugs from issues — and
all of them *start from a well-formed issue*. Writing that issue is the work our
person cannot get the client to do. Repro starts one step earlier, and refuses to
emit a patch until it has proof the bug is real. See
[§6](#6-the-safety-property) and
[`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md) for the full argument.

---

## 2. Quickstart — 60 seconds, no credentials of any kind

```bash
git clone https://github.com/phuchd210406/BuckyAgent.git && cd BuckyAgent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
make demo
```

**No AWS account and no API key are required, and once the dependencies are
installed nothing needs the network again.** `make demo` forces
`LLM_PROVIDER=fake`, which replays model answers recorded once from real Bedrock
into `src/repro/llm/cassettes/`. The pytest runs, the workspace, the patcher and
the invariant checks are all real; only the model is replayed.

Verified on 2026-09-07 from a clean `git clone` inside a stock
`docker.io/library/python:3.12` container with no `.env` secrets, no
`ANTHROPIC_API_KEY` and no `AWS_*` variables set. **All five commands together
took 36 seconds**, the clone and the `pip install` being nearly all of it.
Python 3.11+ is required; the same walkthrough also passes on 3.14.

<details>
<summary>What <code>make demo</code> prints (actual output from that container)</summary>

```
provider   fake    repo fixtures/demo_repos/shopcart
           replaying recorded answers: no model is called. Set ANTHROPIC_API_KEY for a real run.

run        00594f1fa43b
verdict    REPRODUCED_NOT_FIXED
cost       $0.000000 over 7 model calls
wall clock 1.9s

observed   charged postage fee despite basket total exceeding $50 and site stating free postage over $50
  candidate  shopcart/pricing.py:shipping_for  (0.95)
  repro 1    tests/test_shipping_threshold.py: REPRODUCED
  fix 1      : rejected
  fix 2      : rejected
  fix 3      : rejected

--- for the client ---
You were charged postage even though your basket was over $50 and the site says
free postage over $50 - we've reproduced exactly that issue. We've found where
the problem is happening in our checkout process. We don't have a safe fix
deployed yet, but we're working on it. We'll update you as soon as we have a
solution ready.
```

Two lines vary and neither is a problem: the run id is random, and the `fix`
lines may name `shopcart/pricing.py` instead of being blank, depending on which
recorded reply the cassette store serves. **Everything else is what you should
see — including the three rejected fixes, which are the product working, not
failing.** The replayed model proposed a diff against a version of
`shopcart/pricing.py` that does not exist; the sandbox refused to apply it
(`git apply --check` rejected it), the gate refused to accept it, and the run
came back `REPRODUCED_NOT_FIXED` with an honest client reply rather than a
plausible-looking patch. That is [§6](#6-the-safety-property) happening in front
of you on the very first command you run.

The full loop *with* an accepted fix — red test, green test, green suite — is
scored by `make eval-fixtures`; see [§5](#5-tests-and-eval).

</details>

### Running it on a real repository, for real

`make demo` calls no model, so it can only answer prompts somebody already
recorded — the seeded repo and its recorded complaint. To point it at an actual
project you need a model, and either credential works:

```bash
# Claude Haiku 4.5 on the Anthropic API — the key does not expire
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

# or the same model through Bedrock — sandbox keys last 12h
#   LLM_PROVIDER=bedrock, plus AWS_* in .env; `make check-bedrock` proves it works
```

Then, from the command line:

```bash
make run REPO=owner/name REPORT="the checkout charged me postage even though it says free over \$50"
```

or in the browser:

```bash
make api      # backend on :8000
make web      # UI on :5173 — paste any GitHub URL and the complaint
```

What happens on a real run, in order: the repository is cloned shallow into
`~/.cache/repro/repos`, copied into a throwaway sandbox, its declared
dependencies are installed into a per-run virtualenv (without which every
generated test would fail at import), and only then does the agent read the
complaint, search the code, write a failing test, and try to fix it. The page
names the model that is answering and the run's hard spending cap, and says so
plainly when there is no credential and it is about to replay instead.

Private repositories need `GITHUB_TOKEN` set on the backend. The token is used
for the clone and then wiped from the checkout's git config.

### Reading the repository before you run on it

The **Code** tab shows the checkout the agent is about to search — the file
list, the code with line numbers, and the existing test suite marked as such. It
works the same for a pasted GitHub repository (cloned here, and the run reuses
that clone) and for a seeded one.

For a seeded repository it also shows **the complaint written for it**, read from
`eval/dataset.yaml` so the screen cannot drift from what the eval scores, along
with the ground truth: which file holds the bug, or that there is nothing to
patch and the agent is supposed to say so. Three of the nine cases are that kind.
One click puts the complaint in the box.

During a run, every file path the agent names is a link into that panel: the
localiser saying `shopcart/pricing.py` is a claim, and being one click from the
code is what lets anyone watching check it.

---

## 3. Architecture

From [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):

```
  client complaint (free text)  +  a GitHub repo (or a local path)
              |
              v
  [PREPARE]  clone --depth 1 -> copy into a sandbox -> per-run virtualenv
             (not a graph node: it happens once, before START, and the
              agent has no capability that can reach it)
              |
              v
  +-----------------------------------------------------------------+
  |                      LangGraph  ·  typed state                   |
  |                                                                  |
  |   [INTAKE]  extract ReportFacts ------> confidence < 0.6         |
  |      |                                  or missing critical      |
  |      |                                          |                |
  |      |                                          v                |
  |      |                                   [CLARIFY]  <= 1 round   |
  |      |                                   3 plain-English Qs      |
  |      |                                   --> PAUSE, ask human    |
  |      v                                                           |
  |   [LOCALISE]  code search + git log ---> <=5 typed Hypotheses    |
  |      |                                   (paths + 2-line reason, |
  |      |                                    never file contents)   |
  |      v                                                           |
  |   [REPRO] write failing test -> RUN IT IN SANDBOX               |
  |      |         |                                                 |
  |      |         +-- test went GREEN? we did NOT reproduce.        |
  |      |             revise hypothesis, loop  ---------+           |
  |      |             (hard cap 3, counter in state)    |           |
  |      |                                               |           |
  |      +-- test went RED for the right reason ---------|--+        |
  |                                                      |  |        |
  |              +---------------------------------------+  |        |
  |              v                                          v        |
  |        [REPORT] verdict=NOT_REPRODUCED            [FIX] patch    |
  |              ^                                          |        |
  |              |                                     RUN TWICE:    |
  |              |                                  1. repro test    |
  |              |                                     must go GREEN |
  |              |                                  2. existing suite|
  |              |                                     must stay GRN |
  |              |                                          |        |
  |              +-- 3 attempts, still not both green ------+        |
  |                                                         |        |
  |        [REPORT] dev PR body + plain-language client reply        |
  +-----------------------------------------------------------------+
              |
              v
      RunRecord (full audit trail, token cost, replayable)
```

| Node | Agent class | Input | Output | Model |
|---|---|---|---|---|
| `intake` | Extraction · Parse & Transform | raw complaint | `ReportFacts` | Haiku |
| `clarify` | Information · Answer & Advise | `ReportFacts.missing` | ≤3 `ClarifyingQuestion` | Haiku |
| `localise` | Decision-Support · Guide & Recommend | facts + search hits | ≤5 `Hypothesis` | Haiku |
| `repro` | Transaction · Do & Automate | hypotheses | `TestArtifact` + real `ExecutionResult` | Haiku, Sonnet on attempt 3 |
| `fix` | Transaction · Do & Automate | red test + hypothesis | `Patch` + two `ExecutionResult`s | Haiku, Sonnet on attempt 3 |
| `report` | Creative/Generative · Create & Draft | whole `RunRecord` | `Handover` (two audiences) | Haiku |

Every loop is bounded in plain Python, never by asking the model whether it is
nearly done: `MAX_CLARIFY_ROUNDS=1`, `MAX_REPRO_ATTEMPTS=3`,
`MAX_FIX_ATTEMPTS=3`, `MAX_TOTAL_LLM_CALLS=40`, `MAX_RUN_USD=0.25` — all in
[`src/repro/contracts.py`](src/repro/contracts.py).

The agent has exactly four capabilities: read a file, write a file under
`tests/`, run pytest, apply a unified diff. There is no shell tool, no network
tool and no install tool. Guardrails, the sandbox model and the stack rationale
are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 4. What is in `src/`

Every directory under `src/repro/`, one line each:

| Directory | Purpose |
|---|---|
| `src/repro/agents/` | The five graph nodes and their system prompts — intake, clarify, localiser, repro, fix, reporter |
| `src/repro/graph/` | LangGraph state, routers, bounded loops, the sandbox seam, and `run()` |
| `src/repro/llm/` | The provider seam: Bedrock, the Anthropic API, structured output, budget guards, and record/replay cassettes |
| `src/repro/sandbox/` | Everything that touches reality — disposable workspace, cloning, dependency install, pytest runner, patcher, read-only preview |
| `src/repro/retrieval/` | Local BM25-style code search over the workspace. No vector DB, by design |
| `src/repro/api/` | FastAPI app: `POST /runs`, the SSE event stream, `GET /healthz` |
| `src/repro/agentcore/` | The Bedrock AgentCore entrypoint — the graph plus four lines |

And the top-level modules:

| File | Purpose |
|---|---|
| [`src/repro/contracts.py`](src/repro/contracts.py) | **Frozen** typed contracts, every loop bound, and the invariant checker |
| [`src/repro/settings.py`](src/repro/settings.py) | Model ids, per-token prices, region. Ids are constants, never built |
| [`src/repro/clients.py`](src/repro/clients.py) | Which provider a run uses, and whether one is available at all |
| [`src/repro/cli.py`](src/repro/cli.py) | The command line behind `make demo` and `make run` |
| [`src/repro/demo_cases.py`](src/repro/demo_cases.py) | The seeded complaints, read from the golden dataset so the UI cannot drift |

Outside `src/`:

| Path | Purpose |
|---|---|
| `fixtures/` | Nine seeded demo repos, their planted bugs, and the real-sounding complaints |
| `eval/` | Golden dataset (`dataset.yaml`), the metrics harness (`run_eval.py`), and committed `RunRecord` fixtures |
| `tests/` | 572 tests. Contract, invariant, sandbox, graph, API and replay-fidelity suites |
| `web/` | Vite + React UI: live SSE timeline, code browser, cost ticker |
| `scripts/` | `check_bedrock.py` (credential preflight), `record_cassettes.py`, deploy verifiers |
| `docs/` | Problem statement, architecture, evaluation, deployment |

---

## 5. Tests and eval

Both are free, offline and deterministic. Neither calls a model.

```bash
make test            # 572 tests, ~50-70s
make eval-fixtures   # the six-metric table, exits non-zero on a safety violation
make lint            # exactly what CI runs
```

`make test` ends with (the wall-clock figure varies by machine):

```
572 passed, 2 warnings in 70.10s (0:01:10)
```

`make eval-fixtures` prints exactly this and exits `0`:

```
Repro — golden dataset (records from eval/fixtures)

The six metrics (docs/EVALUATION.md, slide 7)

#  Metric                       Result                    Target       Pass  Detail
-  ---------------------------  ------------------------  -----------  ----  --------------------------------------------------------------
1  Schema validation pass rate  n/a                       >= 95%       -     no counted structured calls (replay client does not re-prompt)
2  Tool-call success rate       100%                      >= 98%       yes   23/23 sandbox calls returned a result
3  Reproduction rate            6/6                       >= 5/6       yes   genuinely-buggy cases where a red test was produced
4  False-fix rate               0%                        0%           yes   0 patch(es) emitted without a verified red->green
5  Loop discipline              1.00 mean repro attempts  mean <= 1.8  yes   0% of runs hit the repro cap (0/9)
6  Token cost per run           $0.0120                   <= $0.03     yes   1.20 cents per resolved complaint
```

followed by a per-case breakdown of all nine cases. It also writes
`eval/results.json` and `eval/results.md`.

**The three modes of the harness**, and which one to trust for what:

| Command | What it scores | Model | Exit code |
|---|---|---|---|
| `make eval-fixtures` | Committed `RunRecord`s in `eval/fixtures/` | none | **0 today.** What CI runs on every push |
| `make eval` | Runs the agent over the whole dataset | replayed cassettes | **1 today** — cassettes exist only for `shopcart`; the other eight report a cassette miss and are not scored |
| `make eval-live` | Runs the agent for real | Bedrock, **costs money** | — |

The dataset is nine cases, and the mix is the point. Six are reproducible bugs
behind vague complaints; the other three have `expected_files: []`, meaning **the
agent is supposed to produce nothing**:

| Case | Expected verdict | Why it is in the set |
|---|---|---|
| `notekeeper-underspecified` | `needs_clarification` | The complaint is "it's broken". The agent must *ask*, not guess |
| `delivery-working-days` | `not_reproduced` | The client misunderstood the feature. The code is correct |
| `statusboard-client-network` | `not_reproduced` | The fault is on the client's own network, outside the repo |

An agent that always finds something to fix scores well on the first six and
fails every one of these. Full method in
[`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## 6. The safety property

**No patch is ever emitted without a test that failed before it and passes after,
plus the project's full pre-existing suite staying green.**

It is not a prompt, a policy or a convention. It is a function:

### The enforcing function

[**`RunRecord.check_invariants`** — `src/repro/contracts.py:277`](src/repro/contracts.py#L277)

```python
def check_invariants(self) -> list[str]:
    bad: list[str] = []
    reproduced = any(a.reproduced for a in self.repro_attempts)
    accepted = [f for f in self.fix_attempts if f.accepted]

    if self.verdict == Verdict.REPRODUCED_AND_FIXED:
        if not reproduced:
            bad.append("verdict=fixed but no repro attempt was marked reproduced")
        if not accepted:
            bad.append("verdict=fixed but no fix attempt was accepted")
    if accepted and not reproduced:
        bad.append("a patch was accepted without a reproduction: forbidden")
    for f in self.fix_attempts:
        if f.accepted and not (f.target_test.green and f.suite.green):
            bad.append(f"fix {f.attempt_no} accepted without red->green + green suite")
    if len(self.repro_attempts) > MAX_REPRO_ATTEMPTS:
        bad.append("repro loop exceeded its cap")
    if len(self.fix_attempts) > MAX_FIX_ATTEMPTS:
        bad.append("fix loop exceeded its cap")
    if self.usage.usd > MAX_RUN_USD:
        bad.append(f"run cost {self.usage.usd} exceeded MAX_RUN_USD")
    return bad
```

That is the function verbatim, docstring elided. An empty list means sound;
everything below either feeds it or checks it.

### The four places it is enforced

| Where | Line | What it does |
|---|---|---|
| `agents/fix.py` | [`59`](src/repro/agents/fix.py#L59) | `accepted = target_test.green and suite.green` — **there is no other route to `True`.** The model never sets this field |
| `agents/_common.py` | [`81`](src/repro/agents/_common.py#L81) | `is_reproduction()` — `True` only when the test *ran* and *failed*. A test that passed, errored on import, or timed out is not a reproduction |
| `graph/build.py` | [`491`](src/repro/graph/build.py#L491) | `enforce_invariants()` — the last gate before a record leaves the process. On a violation the verdict is forced to `ABORTED_BUDGET`, **every patch body is replaced with an empty diff**, and the evidence of what went wrong is kept |
| `api/main.py` | [`256`](src/repro/api/main.py#L256) | `publish()` re-checks before a record is readable over HTTP, because "the graph would never" is not a property an API can rely on |

`ExecutionResult.green` ([`contracts.py:167`](src/repro/contracts.py#L167)) is
itself strict: exit code 0, *and* not timed out, *and* zero failures, *and* zero
errors.

### The tests that prove it

| Test | File | Proves |
|---|---|---|
| `test_cannot_claim_fixed_without_reproduction` | [`tests/test_contracts.py:61`](tests/test_contracts.py#L61) | A `REPRODUCED_AND_FIXED` verdict with no reproduction is a violation |
| `test_cannot_accept_a_patch_that_leaves_the_suite_red` | [`tests/test_contracts.py:66`](tests/test_contracts.py#L66) | Repro test green + suite red ⇒ `"red->green"` violation |
| `test_budget_overrun_is_an_invariant_violation` | [`tests/test_contracts.py:83`](tests/test_contracts.py#L83) | Cost above `MAX_RUN_USD` is a violation |
| `test_a_green_target_test_with_a_broken_suite_is_not_accepted` | [`tests/test_graph.py:229`](tests/test_graph.py#L229) | End to end: a patch that fixes the bug and breaks the project is refused all three attempts, verdict `REPRODUCED_NOT_FIXED` |
| **`test_a_run_with_violated_invariants_comes_back_with_no_patch`** | [**`tests/test_graph.py:473`**](tests/test_graph.py#L473) | **The one to read.** A deliberately lying reporter node claims a fix with nothing reproduced. The run comes back with every patch body emptied, no handover, every violation logged with the run id — and the returned record is *itself* sound |
| `test_false_fix_is_caught_even_if_the_invariant_checker_goes_blind` | [`tests/test_eval.py:139`](tests/test_eval.py#L139) | The eval harness derives `false_fix` from the record's own fields, so it still catches a false fix with `check_invariants` monkeypatched to return `[]`. The checker is the thing under test; the harness never delegates to it |

CI runs `pytest` **and** `eval/run_eval.py` on every push
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). The harness exits `2`
on a false fix or an unsound record, which fails the PR. **A bug that would ship
a wrong patch is a red build, not a demo-day surprise.**

---

## 7. Cost, measured

| What | Cost |
|---|---|
| `make demo`, `make test`, `make eval-fixtures`, `make lint` | **$0.00** — no model is called |
| **One real end-to-end run** (`shopcart`, Claude Haiku 4.5 on Bedrock `us-east-1`, 7 model calls) | **$0.014147 ≈ 1.4 cents** |
| Golden dataset, mean over nine scored records | $0.0120 ≈ 1.2 cents per run |
| Hard per-run ceiling (`MAX_RUN_USD`, enforced as an invariant) | $0.25 |

The 1.4-cent figure is the measured one: it was written by
`scripts/record_cassettes.py` during a real Bedrock recording and is committed at
[`eval/recorded_runs.json`](eval/recorded_runs.json) alongside the model id and
region it was billed at. Cost is summed from the `usage` block of every response
and priced from `settings.PRICES` — Haiku at $1/$5 per Mtok, Sonnet at $3/$15,
and Sonnet is used only on the final attempt of a loop that is about to fail
anyway.

**Honest caveat on the $0.0120 mean:** `eval/fixtures/` holds hand-written
`RunRecord`s, built so the harness could be developed and CI-wired before the
graph landed. Their token counts are calibrated against that one real
measurement, not independently billed. Only `shopcart` at $0.014147 is a
measurement. `make eval-live` bills the whole dataset for real.

One resolved client complaint costs about **two cents of model time** against a
developer hour.

---

## 8. Limitations

Stated plainly, from *"What we are explicitly NOT building"* in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):

- **Python + pytest only.** Not multi-language.
- **One repository, one run.** Not a GitHub App, no webhooks, no multi-repo. The
  only thing we do with GitHub is clone.
- **No system-level setup in the sandbox** — no database, no Docker, no compiler
  toolchain. A project whose tests need one will error, and an error is never
  counted as a reproduction.
- **No fine-tuning, no RAG over external docs, no vector database.** Retrieval is
  local BM25-style scoring over one temp directory.
- **No auto-merge, ever.** The output is a patch and a pull request body. A human
  presses merge.

Two further bounds on a real repository:

- Dependencies are installed only from what the project *declares*
  (`requirements*.txt`, `pyproject.toml`, `setup.py`), inside a throwaway
  virtualenv, under `REPRO_INSTALL_TIMEOUT_S`. A failed install degrades the run,
  with the reason in the record, rather than ending it.
- A very large repository is refused rather than half-indexed —
  `REPRO_MAX_REPO_MB`, default 500 MB.

And one about the offline demo: `LLM_PROVIDER=fake` can only answer prompts that
were recorded. It works for the seeded `shopcart` case and misses on anything
else, which is why `make eval` currently scores one case and `make eval-fixtures`
is what CI runs.

---

## Docs

- [Problem statement](docs/PROBLEM_STATEMENT.md) — pressure-tested, with citations
- [Architecture](docs/ARCHITECTURE.md) — the graph, bounds, guardrails, stack rationale
- [Evaluation](docs/EVALUATION.md) — how we verify correctness; the six metrics
- [Deploy](docs/DEPLOY.md) — AWS sandbox, AgentCore, budget discipline
- [Render](docs/RENDER.md) — the backend as a URL that stays up, from a cold start with no accounts
- [Seeded bugs](fixtures/BUGS.md) — ground truth for all nine demo repos
