# Engineer C — Model Layer, Prompts & Retrieval

**You own everything the model reads and everything that talks to Bedrock. You
are the only person with `LLM_PROVIDER=bedrock` in their `.env`.**

Session 3: *"Treat tool descriptions as the highest-leverage prompt text we
write."* Every `Field(description=...)` in `contracts.py` and every system
prompt in `agents/` is your product. Engineer A writes the orchestration around
your prompts and will not touch their text.

| | |
|---|---|
| **Files you own** | `src/repro/llm/bedrock.py`, `src/repro/retrieval/*`, the `SYSTEM_PROMPT` constants inside `src/repro/agents/*.py`, `src/repro/llm/cassettes/**`, `tests/test_llm.py`, `tests/test_retrieval.py` |
| **Files you may READ but never EDIT** | `src/repro/contracts.py`, `src/repro/graph/*`, `src/repro/sandbox/*` |
| **You depend on** | Engineer B's `Workspace` for C3 only — use `FakeWorkspace` until it lands |
| **People depend on you for** | `BedrockLLM`, recorded cassettes, prompt quality |

Branch `feat/model`. **You are the budget owner.** Nobody else runs against real
Bedrock without telling you.

---

## C1 · The Bedrock client (2 h)

```
Read src/repro/llm/base.py and src/repro/llm/fake.py, then implement BedrockLLM
in src/repro/llm/bedrock.py satisfying the LLMClient protocol.

Use boto3 bedrock-runtime `converse` (not invoke_model — converse gives one
message shape across vendors and we may swap Haiku for Sonnet on retry).

complete(): straightforward. Return LLMResponse with text, stop_reason, and a
TokenUsage built from response["usage"] and priced via settings.usd_for().

structured(): this is the important one.
- Append the JSON schema (schema.model_json_schema()) to the system prompt and
  instruct: return ONE JSON object, no prose, no markdown code fence.
- Strip a leading ```json fence defensively before parsing anyway — models do
  this regardless of instructions.
- CRITICAL: if stop_reason == "max_tokens", the text is truncated and any JSON
  in it is incomplete. Do NOT attempt to parse it. Raise immediately with a
  message saying the ceiling was hit. Session 1 makes this point specifically:
  truncated JSON looks like a model error but is not.
- On ValidationError, re-prompt EXACTLY ONCE, feeding back the pydantic error
  message and the original reply. On the second failure raise SchemaValidationError.
  Never return a partially-filled object.
- Retry on ThrottlingException with exponential backoff, max 3 tries.
- Record a metric: how many structured() calls validated on the first attempt.
  Expose it as a counter attribute. That is metric #1 on our evaluation slide.

Add a RecordingLLM wrapper that, when settings().record is True, writes every
(system, user, schema_name) -> reply to a cassette file using the exact
cassette_key() from fake.py. Do not reimplement the hashing — import it, or the
cassettes will never be found on replay.

Write tests/test_llm.py with a mocked boto3 client (no network) covering:
first-attempt validation, one re-prompt then success, two failures then raise,
max_tokens raising rather than parsing, a fenced reply being parsed correctly,
and usd being computed correctly for a known token count.
```

**Done when:** the mocked tests pass. **Do not make a real Bedrock call until
`scripts/check_bedrock.py` prints OK 3/3.**

---

## C2 · Code search (2 h)

```
Implement search() and rank_candidates() in src/repro/retrieval/index.py.

We deliberately do NOT use a vector database. The AWS deck is explicit that
OpenSearch's minimum always-on capacity costs hundreds a month, and our corpus is
one repo in a temp directory for sixty seconds. Pure Python only.

search(ws, query, k) -> list[(rel_path, snippet, score)]:
- walk .py files via repo_facts.list_source_files (use sandbox/fake.py until B5 lands)
- score with a simple BM25-style term overlap over identifiers, comments and
  docstrings; split identifiers on snake_case and camelCase so a client saying
  "free postage" can reach a symbol named free_shipping_threshold
- maintain a small synonym map from client vocabulary to code vocabulary:
  postage->shipping/delivery, basket->cart, voucher/coupon->promo/discount,
  sign in->login/auth, "didn't go through"->error/exception/fail.
  This is the single highest-value thing in this module — the whole problem is
  that clients and code use different words.
- snippets are the matching function's body capped at 60 lines. Never a whole file.

rank_candidates(hits, k) -> list[Hypothesis]: max MAX_LOCALISE_CANDIDATES,
confidence normalised to 0..1, rationale left empty for the LLM to fill.

tests/test_retrieval.py: assert that the query built from the shopcart client
complaint ("charged me postage even though the site says free postage over $50")
ranks shopcart/pricing.py first. That test is the module's whole point — if it
fails, the agent will never find the bug.
```

**Done when:** the shopcart ranking test passes, plus a test that an empty repo
returns `[]` without raising.

---

## C3 · The five system prompts (3 h — your highest-value hours)

```
Write the SYSTEM_PROMPT constant in each of the five files under
src/repro/agents/. Engineer A has left them as "TODO: Engineer C owns this".
Do not change any function body — only the prompt constants.

Shared rules for all five: state the role in one line, state the output contract,
state what NOT to do, and give one worked example. Keep each under 250 tokens —
the system prompt is resent on every step of the loop, so every token is billed
repeatedly.

intake — extract ReportFacts. Emphasise: only what the reporter SAID. If they
  did not state expected behaviour, expected_behaviour is null and
  "expected_behaviour" goes in missing. Inventing plausible steps is the
  failure mode to prevent; a recent study found 12.3% of LLM bug-report
  summaries contained fabricated content, and a fabricated step sends the whole
  run down a wrong path.

clarify — at most 3 questions for a NON-TECHNICAL person. Ban: file names,
  stack traces, "console", "network tab", "reproduce". Include a good/bad pair:
  BAD "What HTTP status did the checkout endpoint return?"
  GOOD "When you tried to pay, did you see an error message, or did the page
        just not do anything?"

localiser — given facts plus ranked snippets, produce up to 5 Hypotheses. Rank
  by how directly the code explains the OBSERVED behaviour, not by how
  interesting the code is. Rationale must quote the client's own words.

repro_agent — write ONE pytest test that FAILS because of the reported bug.
  Hard rules: import only from the project and stdlib; no network; no mocks of
  the code under test; assert the behaviour the CLIENT described, not the
  behaviour the current implementation has. Say explicitly: a test that passes
  is a failed reproduction — you have not proven the bug exists. Include the
  shopcart worked example.

fix — the smallest patch that makes the failing test pass without changing any
  other test's behaviour. Ban: touching the test file, weakening existing
  assertions, adding try/except to swallow the symptom, changing unrelated
  formatting. Say that if the correct fix requires changing an existing test,
  return no patch and explain why — that is a design decision for a human.

reporter — two outputs, two audiences. dev_summary: markdown PR body with root
  cause, the diff rationale, and the evidence trail. client_reply: plain
  language, addressed to the reporter, echoing their own words back so they know
  they were understood, and honest about the verdict. If the verdict is not
  REPRODUCED_AND_FIXED, the client reply must NOT imply a fix was shipped.
  Include one worked example of each.
```

**Done when:** each prompt is under 250 tokens and you have read all five aloud.
If a prompt does not fit in one screen, it is too long.

---

## C4 · Cassette recording (60 min, hour 20 — spends real money, ~$0.50)

```
With verified credentials and the region confirmed by scripts/check_bedrock.py,
run the full pipeline against real Bedrock in record mode for all 8 eval cases:

  REPRO_RECORD=1 LLM_PROVIDER=bedrock make record

Then verify replay fidelity: run the same 8 cases with LLM_PROVIDER=fake and
assert every RunRecord verdict and every accepted patch is byte-identical to the
recorded run. Write that as tests/test_replay_fidelity.py.

Commit the cassettes. Print the total USD spent and post it in the channel.

If a prompt changes after this point, its cassette key changes and replay breaks
with "No cassette". That is intentional. After hour 20, treat prompt edits as
requiring a re-record, and tell the team before you make one.
```

**Done when:** `LLM_PROVIDER=fake make eval` reproduces the recorded results
exactly, offline, with wifi turned off. **This is the team's insurance policy.**

---

## C5 · Cost instrumentation (45 min)

```
Add src/repro/llm/budget.py with a BudgetGuard that accumulates TokenUsage
across a run, exposes .usd, and raises BudgetExceeded when usd > MAX_RUN_USD.
Wire it into BedrockLLM so every call updates it.

Also add a session-level guard reading a REPRO_SESSION_BUDGET_USD env var
(default 1.00) that hard-stops the process, so a runaway experiment cannot eat
the team's shared USD 20 while someone is at lunch.

Write a test that the guard raises at the boundary, not one call after it.
```

**Done when:** a scripted run that would exceed the cap raises before the
offending call is made, not after.
