# Architecture

## One picture

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

## The five nodes

| Node | Agent class (Session 3 taxonomy) | Input | Output | Model |
|---|---|---|---|---|
| `intake` | Extraction · Parse & Transform | raw complaint | `ReportFacts` | Haiku |
| `clarify` | Information · Answer & Advise | `ReportFacts.missing` | ≤3 `ClarifyingQuestion` | Haiku |
| `localise` | Decision-Support · Guide & Recommend | facts + search hits | ≤5 `Hypothesis` | Haiku |
| `repro` | Transaction · Do & Automate | hypotheses | `TestArtifact` + real `ExecutionResult` | Haiku, Sonnet on attempt 3 |
| `fix` | Transaction · Do & Automate | red test + hypothesis | `Patch` + two `ExecutionResult`s | Haiku, Sonnet on attempt 3 |
| `report` | Creative/Generative · Create & Draft | whole `RunRecord` | `Handover` (two audiences) | Haiku |

Escalating to Sonnet only on the final attempt is a deliberate cost/capability
trade: Haiku at $1/$5 per million handles most runs, and we pay the $3/$15 rate
only for the ~15% of runs that are about to fail anyway.

## The four things that keep it alive at turn fifty

Straight from Session 3's "Building Agents That Hold Up":

1. **Context rot** — each node is single-purpose and exits. Nothing accumulates
   a fifty-turn transcript. The state carries typed objects, not chat history.
2. **Bound every loop** — `MAX_REPRO_ATTEMPTS`, `MAX_FIX_ATTEMPTS`,
   `MAX_CLARIFY_ROUNDS`, `MAX_TOTAL_LLM_CALLS`, `MAX_RUN_USD`. All in
   `contracts.py`, all read by routers in plain Python, none of them consulting
   the model's opinion about whether it is nearly done.
3. **Descriptions are the interface** — every `Field(description=...)` in
   `contracts.py` is prompt text. Review them as prompts.
4. **Keep payloads small** — the model never sees a whole file. `search()`
   returns capped snippets; `ExecutionResult` carries tails only
   (`SANDBOX_MAX_OUTPUT_CHARS = 4000`), because a pytest traceback dump would be
   re-read on every subsequent turn of the loop.

## Guardrails

- **Sandbox.** The target repo is copied to `/tmp/repro-workspaces/<run_id>`.
  The original is never written to. Path traversal outside the workspace raises.
  `subprocess` with a hard timeout — `SANDBOX_TIMEOUT_S` (60s) for the seeded
  fixture, raised with `REPRO_SANDBOX_TIMEOUT_S` for a real project, whose whole
  suite `fix` has to run green and where a timeout looks exactly like a patch
  that broke something.
- **Allow-list, not deny-list.** The agent has exactly four capabilities:
  read a file, write a file under `tests/`, run pytest, apply a unified diff.
  There is no shell tool. There is no network tool. There is no install tool —
  see the next point, which is the one guarantee that changed and why.
- **Dependencies are installed once, before the agent runs.** A real repository's
  tests import third-party packages, and against a bare interpreter every
  generated test errors at collection — which `is_reproduction` correctly
  refuses to call a reproduction, so every real run used to end "not reproduced"
  for a reason that had nothing to do with the client's bug. So `sandbox/deps.py`
  builds one throwaway virtualenv per run and installs what the project
  *declares* (`requirements*.txt`, `pyproject.toml`, `setup.py`). The boundaries
  are the point: it runs once before `START`, from a plan derived by reading
  files rather than from anything a model said; it adds no method to the
  `Sandbox` protocol, so no node can reach it; it is bounded by
  `REPRO_INSTALL_TIMEOUT_S`; it is skipped entirely for a project that declares
  nothing, which keeps the offline demo offline; and a failed install degrades
  the run — with the reason in the record — rather than ending it.
- **The clone is bounded and never the thing under test.** Shallow, single
  branch, hard timeout, size ceiling (`REPRO_MAX_REPO_MB`), cached under
  `~/.cache/repro/repos`. The agent never touches the cache: `Workspace` copies
  out of it, which is why a `GITHUB_TOKEN`-fetched private repo is still only
  ever read.
- **Never auto-merge.** The output is a patch and a PR body. A human presses
  merge. `compliance.human_in_the_loop` is the design, not a setting.
- **Budget kill-switch.** `MAX_RUN_USD` is checked after every model call. The
  run aborts with `Verdict.ABORTED_BUDGET` rather than discovering the overrun
  on the AWS console two days later (Cost Explorer lags ~48h).

## Stack, and why each piece was chosen against the constraints

| Layer | Choice | Why |
|---|---|---|
| Model | Claude Haiku 4.5 — Bedrock `converse`, or the first-party API | Session 1's stated default. $1/$5 per Mtok either way. Two providers because the Bedrock sandbox lease expires every 12 hours and a demo cannot; `clients.resolve_provider` picks whichever has a credential, and the UI is told which, so a replayed run is never mistaken for a live one. |
| Orchestration | LangGraph | Session 2 §4. Flowchart control is exactly right when a wrong branch means shipping a bad patch. |
| Schemas | Pydantic v2 | Session 1: typed object back, not a string to parse that breaks the day a model wraps JSON in a code fence. |
| Sandbox | `subprocess` + `tempfile` | Zero infra. No Docker dependency on the demo laptop. |
| Retrieval | Local BM25-ish scoring over the workspace, capped by `REPRO_MAX_INDEX_FILES` and, past the cap, choosing which files to parse by how well their PATHS match the complaint — alphabetical truncation on a large repo excludes the buggy file before a word is scored | **Deliberately not S3 Vectors or OpenSearch.** The AWS deck: OpenSearch minimum always-on capacity costs hundreds a month. Our corpus is one repo, in a temp dir, for sixty seconds. A vector DB here is a budget bonfire and an architecture smell. |
| API | FastAPI + SSE | Streams `StreamEvent`s so the judge watches the agent think. |
| Frontend | Vite + React on **Vercel free tier** | Keeps the entire UI off the AWS bill. The USD 20 buys tokens and nothing else. |
| Storage | JSON files under `runs/` | DynamoDB on-demand would also be near-free, but files are one fewer thing to break at hour 27, and the audit trail is git-diffable. |
| Deploy | AgentCore Runtime, once, recorded, then `destroy` | Session 2 §6. Serverless, one microVM per session, no Dockerfile to write. |

## What we are explicitly NOT building

Saying this out loud on a slide is a strength, not a weakness — it is the
difference between "partially functional" and "functional prototype with minor
work needed" on the technical-quality rubric.

- Not multi-language. Python + pytest only.
- Not multi-repo, not a GitHub App, not webhooks. One repository, one run, and
  the only thing we do with GitHub is clone.
- No system-level setup in the sandbox: no database, no docker, no compiler
  toolchain. A project whose tests need one will error, and an error is never a
  reproduction.
- No fine-tuning, no RAG over external docs, no vector database.
- No auto-merge, ever.
