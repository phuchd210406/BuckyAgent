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

## Quickstart (no AWS account needed)

```bash
git clone <your-repo-url> && cd repro
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # LLM_PROVIDER=fake by default
make test                     # contract + invariant tests
make demo                     # a full agent run, replayed from cassettes, $0.00
```

`LLM_PROVIDER=fake` replays recorded model responses, so the whole pipeline runs
deterministically, offline, for free. Set `LLM_PROVIDER=bedrock` with credentials
to run live.

## Layout

| Path | Purpose |
|---|---|
| `src/repro/contracts.py` | **Frozen** typed contracts + loop bounds + the invariant checker |
| `src/repro/settings.py` | Model ids, prices, region. Ids are constants, never built |
| `src/repro/llm/base.py` | The `LLMClient` protocol — the only seam to any provider |
| `src/repro/llm/bedrock.py` | Bedrock `converse` client, structured output, retries |
| `src/repro/llm/fake.py` | Record/replay + scripted clients. Free, deterministic tests |
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

Python + pytest only. One repo per run. No dependency installation in the
sandbox. No auto-merge, ever — the output is a patch and a pull request body
that a human approves.
