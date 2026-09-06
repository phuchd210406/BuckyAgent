# Repro — from "it's broken" to a failing test

> A solo maintainer at a small agency, on the Monday morning a client emails
> *"the checkout is broken, please fix ASAP"*, needs a way to turn that one
> sentence into a test that fails for the reason the client is describing —
> before anyone touches the code — because 92% of studied bug reports are
> missing at least one step needed to reproduce them (arXiv:2301.01235), and
> developers spend 35–50% of their time validating and debugging
> (ACM Queue, "The Debugging Mindset").

Repro is an agentic system that reads a non-technical complaint, finds the
suspect code, **writes a test that fails for the reported reason**, and only
then proposes a patch — which it accepts solely when that test goes green *and*
the project's existing suite stays green. If it cannot reproduce the bug, it
says so and asks the client a question, in their own language, instead of
guessing.

**The safety property:** no patch is ever emitted without a verified
red→green plus a green suite. It is enforced in
[`RunRecord.check_invariants`](src/repro/contracts.py) and proved by
[`tests/test_contracts.py`](tests/test_contracts.py).

## Quickstart

```bash
git clone <your-repo-url> && cd repro
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
make test                     # contract + invariant tests
make demo                     # a full agent run, replayed from cassettes, $0.00
```

### Running it on a real repository, for real

`make demo` calls no model: it replays recorded answers, which only exist for
the seeded repo and its recorded complaint. To point it at an actual project you
need a model, and either credential works:

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

## Layout

| Path | Purpose |
|---|---|
| `src/repro/contracts.py` | **Frozen** typed contracts + loop bounds + the invariant checker |
| `src/repro/settings.py` | Model ids, prices, region. Ids are constants, never built |
| `src/repro/llm/base.py` | The `LLMClient` protocol — the only seam to any provider |
| `src/repro/llm/structured.py` | Ask for an object, refuse a truncated reply, one repair round |
| `src/repro/llm/bedrock.py` | Bedrock `converse` client, retries, throttle backoff |
| `src/repro/llm/anthropic_api.py` | The same models on the first-party API. No expiring lease |
| `src/repro/llm/fake.py` | Record/replay + scripted clients. Free, deterministic tests |
| `src/repro/clients.py` | Which provider a run uses, and whether one is available at all |
| `src/repro/sandbox/github.py` | Clone a real repository: bounded, cached, token-aware |
| `src/repro/sandbox/deps.py` | A per-run virtualenv, so a real project's tests can import |
| `src/repro/sandbox/` | Disposable workspace, pytest runner, patch apply/revert |
| `src/repro/retrieval/` | Local code search. No vector DB, by design |
| `src/repro/agents/` | The five nodes and their system prompts |
| `src/repro/graph/` | LangGraph state, routers, bounded loops, `run()` |
| `src/repro/api/` | FastAPI + SSE stream for the web UI |
| `src/repro/agentcore/` | The AgentCore entrypoint — the graph plus four lines |
| `fixtures/` | Seeded demo repos, planted bugs, real-sounding client complaints |
| `eval/` | Golden dataset and the metrics harness |
| `scripts/check_bedrock.py` | Preflight: credentials, region, model access |
| `tasks/` | Per-engineer work plans |

## Docs

- [Problem statement](docs/PROBLEM_STATEMENT.md) — pressure-tested, with citations
- [Architecture](docs/ARCHITECTURE.md) — the graph, bounds, guardrails, stack rationale
- [Evaluation](docs/EVALUATION.md) — how we verify correctness; the six metrics
- [Deploy](docs/DEPLOY.md) — AWS sandbox, AgentCore, budget discipline

## Limitations

Python + pytest only. One repo per run. No auto-merge, ever — the output is a
patch and a pull request body that a human approves.

On a real repository, two things bound what it can do. Dependencies are
installed from what the project declares (`requirements*.txt`, `pyproject.toml`,
`setup.py`); a project that needs a database, a compiler toolchain or a service
running will have tests that error, and an error is never counted as a
reproduction. And a very large repository is refused rather than half-indexed —
`REPRO_MAX_REPO_MB`, default 500 MB.
