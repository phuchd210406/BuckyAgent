# Engineer A — Graph & Orchestration

**You own the wiring. You do not own any prompt text and you do not own any
code that touches the filesystem.**

| | |
|---|---|
| **Files you own** | `src/repro/graph/*`, `src/repro/agents/*` (function bodies, orchestration only), `src/repro/agentcore/agent.py`, `tests/test_graph.py`, `tests/test_routers.py` |
| **Files you may READ but never EDIT** | `src/repro/contracts.py`, `src/repro/llm/base.py`, `src/repro/sandbox/*`, `src/repro/retrieval/*` |
| **You depend on** | nothing at runtime — you use `ScriptedLLM` and a fake sandbox until hour 16 |
| **People depend on you for** | `build_graph(llm)` returning a compiled graph |

## Working rules

- Every node returns **only the keys it changed**. A node that returns the whole
  state will silently clobber a concurrent write.
- Routers are **plain Python**. A router must never call the model to ask
  whether it should loop again. Read the counter, compare to the cap, return an
  edge name.
- Until hour 16, you never import `boto3` and you never run pytest for real. You
  build against `ScriptedLLM` and a stub sandbox that returns canned
  `ExecutionResult`s. This is what makes you independent of Engineers B and C.
- Branch `feat/graph`. PR to main. CI must be green.

---

## A1 · Graph skeleton with fake everything (90 min)

```
Read src/repro/contracts.py and src/repro/graph/state.py in full, then read
docs/ARCHITECTURE.md for the intended graph shape.

Implement build_graph(llm) in src/repro/graph/build.py using LangGraph's
StateGraph over the existing GraphState TypedDict. For now, every node must be
a trivial stub that returns a hardcoded partial state update — do not call the
LLM and do not touch the filesystem yet. I want the wiring correct before any
node does real work.

Nodes: intake, clarify, localise, repro, fix, report.
Edges:
  START -> intake
  intake -> conditional: "clarify" if the facts need it, else "localise"
  clarify -> END  (the run pauses for a human; this is not a loop)
  localise -> repro
  repro -> conditional: "fix" if reproduced, "repro" if attempts remain, else "report"
  fix -> conditional: "report" if accepted, "fix" if attempts remain, else "report"
  report -> END

Write the three routing functions as separate named functions in
src/repro/graph/build.py so they can be unit-tested without the graph. Each
router reads counters from state and the caps from contracts.py. No router may
call the model.

Then write tests/test_routers.py covering, for each router, the boundary case
at the cap and one case either side of it. Use plain dicts as state.

Do not edit contracts.py or state.py. If you think you need a new state field,
stop and tell me instead of adding it.
```

**Done when:** `pytest tests/test_routers.py` passes and
`build_graph(ScriptedLLM([])).invoke({...})` reaches `report` without error.

---

## A2 · Loop bounds under adversarial conditions (60 min)

```
The single most expensive failure mode in this project is a loop that never
terminates and burns the team's shared USD 20 budget. I want that made
impossible, not unlikely.

In tests/test_graph.py, write tests that prove the graph terminates when the
model behaves as badly as possible:
  1. repro never reproduces -> exactly MAX_REPRO_ATTEMPTS repro nodes run, then
     the graph reaches report with verdict NOT_REPRODUCED
  2. fix never gets both greens -> exactly MAX_FIX_ATTEMPTS fix nodes run, then
     report with verdict REPRODUCED_NOT_FIXED
  3. a node returns a state update that tries to reset a counter to 0 -> the
     graph still terminates
  4. total LLM calls exceed MAX_TOTAL_LLM_CALLS -> the graph aborts with
     verdict ABORTED_BUDGET

Use ScriptedLLM from src/repro/llm/fake.py to script the bad behaviour, and a
stub sandbox object that always returns a green ExecutionResult for case 1.

For case 3 and 4 you will need to add enforcement to build.py — counters must be
incremented by the graph, not trusted from the node's return value, and there
must be a budget check between nodes. Implement that enforcement.

Assert on the exact call count, not just on termination. A test that only
asserts "it finished" would pass even if the cap were 300.
```

**Done when:** all four tests pass and you can state the worst-case number of
model calls for a single run as a specific integer. Write that integer in a
comment at the top of `build.py` — you will need it for the deck.

---

## A3 · Node bodies against the sandbox interface (2.5 h)

```
Now fill in the real node bodies in src/repro/agents/*.py. Each node signature
is already (state: GraphState, llm: LLMClient) -> dict.

Rules:
- Nodes call llm.structured(...) with a schema from contracts.py. They never
  parse JSON by hand and never use regex on model output.
- Nodes call sandbox functions through the interfaces in
  src/repro/sandbox/{workspace,runner,patcher}.py. Those raise NotImplementedError
  right now — that is expected. Write the calls against the signatures; Engineer
  B is implementing the bodies in parallel and they will land at hour 16.
- The repro node must set reproduced=True ONLY when the ExecutionResult shows
  the test ran and failed. A test that errors on import is NOT a reproduction —
  it is a broken test. Distinguish these two cases explicitly.
- The fix node must set accepted=True ONLY when target_test.green AND suite.green.
  Do not shortcut this; it is the product's safety property.
- Every node returns partial updates only, and adds its TokenUsage to state.

Leave the SYSTEM PROMPT for each node as a module-level constant named
SYSTEM_PROMPT with the value "TODO: Engineer C owns this". Do not write prompt
text — that is not your file to write, and two people writing prompts produces
two voices.

Then extend tests/test_graph.py with one end-to-end test that scripts a full
happy path through ScriptedLLM and a stub sandbox, and asserts the final
RunRecord.check_invariants() returns [].
```

**Done when:** the end-to-end scripted test passes and `check_invariants()` is
empty. **This is the hour-8 integration checkpoint for the whole team.**

---

## A4 · State assembly and RunRecord (60 min)

```
Write a function assemble_run_record(final_state) -> RunRecord in
src/repro/graph/build.py that converts the terminal GraphState into the
RunRecord contract, and a function run(report, llm) that is the single public
entry point everyone else calls:

    from repro.graph.build import run
    record: RunRecord = run(client_report, llm)

run() must:
  - create the workspace, invoke the graph, and always close the workspace in a
    finally block even when the graph raises
  - call record.check_invariants() before returning, and if it is non-empty,
    force verdict to ABORTED_BUDGET, strip any patch from the record, and log
    every violation loudly. Under no circumstances may a record with violated
    invariants be returned with a patch attached.
  - be safe to call concurrently (two runs must not share a workspace)

Add a test that a run whose invariants are violated comes back with no patch.
```

**Done when:** `run()` is importable and Engineer D can build the API on it.

---

## A5 · AgentCore entrypoint + local verification (45 min, hour 25)

```
Finish src/repro/agentcore/agent.py so that POSTing to
http://localhost:8080/invocations with {"report": {...}} returns
{"verdict": ..., "handover": {...}}.

Follow the Session 2 pattern exactly: BedrockAgentCoreApp, @app.entrypoint,
app.run(). Do not restructure the graph for deployment — the whole point is
that the graph is unchanged.

The handler must:
  - validate the incoming payload into a ClientReport and return a 400-shaped
    error dict on a bad payload rather than raising
  - choose the LLM client from LLM_PROVIDER so the same file works locally with
    cassettes and deployed against Bedrock

Then write scripts/smoke_agentcore.sh that starts the server, POSTs one payload,
asserts the response contains a verdict, GETs /ping, and stops the server.
Non-zero exit on any failure.
```

**Done when:** `bash scripts/smoke_agentcore.sh` exits 0 with no AWS credentials
set. Only then does anyone run `agentcore launch`.
