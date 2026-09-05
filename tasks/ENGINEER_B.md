# Engineer B — Sandbox, Execution & Patching

**You own everything that touches the real world. You never call a model.**

This is the most independent workstream in the project: no LLM, no network, no
AWS, no other engineer's code. It is also the workstream where a bug is most
dangerous, because your `ExecutionResult` is the ground truth that the entire
safety property rests on. If `green` is ever wrong, the agent ships bad patches
and the whole pitch collapses.

| | |
|---|---|
| **Files you own** | `src/repro/sandbox/*`, `tests/test_sandbox.py`, `tests/test_runner.py`, `tests/test_patcher.py` |
| **Files you may READ but never EDIT** | `src/repro/contracts.py`, `fixtures/**` |
| **You depend on** | nothing at all |
| **People depend on you for** | `Workspace`, `run_pytest`, `apply_patch`, `revert_patch` |

Branch `feat/sandbox`. Because you have no dependencies, you should be the first
person with a green PR on main — aim for hour 4.

---

## B1 · Workspace isolation (90 min)

```
Read src/repro/contracts.py and src/repro/sandbox/workspace.py.

Implement the Workspace class. It copies a source repo into a fresh temp
directory under root, and the agent is allowed to mutate the copy and nothing else.

Requirements:
- __init__ copies the tree, skipping .git, .venv, __pycache__, node_modules
- .path returns the workspace root as a Path
- write_file(rel_path, contents) creates parent dirs and returns the absolute path
- read_file(rel_path, max_chars) returns at most max_chars, tail-truncated with a
  marker if it had to cut. Never return an unbounded file.
- close() removes the tree, is idempotent, and never raises
- support use as a context manager

SECURITY — this is the important part. Every path argument must be resolved and
checked to be inside the workspace root. Reject "../../etc/passwd",
"/etc/passwd", and a symlink inside the workspace pointing outside it. Raise
ValueError with a clear message. The model will eventually be choosing these
paths, so treat every one as hostile input.

Then write tests/test_sandbox.py proving:
  1. the SOURCE repo is byte-identical before and after a workspace that wrote,
     deleted and modified files (hash the tree)
  2. each of the three traversal attacks above raises ValueError
  3. close() twice does not raise
  4. two workspaces from the same source do not share state
  5. read_file truncates and marks

Use fixtures/demo_repos/shopcart as the source in tests.
```

**Done when:** all five tests pass. Test 1 is the one that matters — a bug there
means the agent corrupts the user's actual repository.

---

## B2 · The pytest runner (2 h)

```
Implement run_pytest(ws, target, timeout_s) in src/repro/sandbox/runner.py,
returning the ExecutionResult contract.

Requirements:
- run `python -m pytest` with cwd = ws.path, via subprocess, capturing both streams
- a FAILING TEST IS NOT AN ERROR. Never raise on a non-zero exit code. Return it.
- hard timeout: on expiry, kill the whole process group (not just the child, or
  pytest's workers survive), set timed_out=True, exit_code=-1, and return normally
- parse pytest's summary line into passed/failed/errors. Handle all of:
  "3 passed", "1 failed, 5 passed", "2 errors", "no tests ran", and a collection
  error where the summary line is absent. When you cannot parse, set counts to 0
  and put the reason in stderr_tail — never guess.
- truncate stdout and stderr to the LAST SANDBOX_MAX_OUTPUT_CHARS characters.
  The tail matters because that is where the assertion is; the head is import
  noise. Prefix with a truncation marker.
- pass a clean environment: no inherited PYTHONPATH, no AWS_* variables. The
  sandboxed test must not be able to read the team's credentials.

Then write tests/test_runner.py using tiny throwaway repos you build in tmp_path,
covering: all-pass, one-fail, import error, syntax error in a test file,
infinite loop (assert timed_out is True and it returns within timeout+2s),
empty test directory, and a test that prints 200KB to stdout (assert the result
is capped).

Assert on .green explicitly for each case. .green must be True only in the
all-pass case.
```

**Done when:** all seven cases pass and the infinite-loop test consistently
returns in bounded time. **Run that one twenty times in a loop before you call
it done** — a flaky timeout at hour 27 will look like an agent bug and you will
lose two hours.

---

## B3 · Patch application (90 min)

```
Implement apply_patch and revert_patch in src/repro/sandbox/patcher.py.

apply_patch(ws, patch) -> (applied: bool, message: str):
- prefer `git apply --check` then `git apply` inside the workspace if the
  workspace is a git repo; fall back to a pure-Python unified diff applier if not
- ATOMIC: if any hunk fails, the workspace must be exactly as it was before the
  call. Snapshot the affected files first and restore on failure.
- validate that every path in the diff is inside the workspace before applying
  anything (same traversal rules as B1)
- never raise on a malformed diff; return (False, reason)

revert_patch restores the pre-patch state.

tests/test_patcher.py must cover: clean apply, apply then revert returns to the
original hash, a diff with a bad hunk leaves the tree untouched, a diff
referencing ../../ is rejected, and a diff touching a file that does not exist
is rejected. Hash the whole tree before and after for the atomicity tests.
```

**Done when:** the atomicity test passes — a failed patch must leave no trace.

---

## B4 · Fake sandbox for other people's tests (45 min)

```
Engineer A is building the graph against your interfaces and needs to simulate
sandbox outcomes without running real tests. Create src/repro/sandbox/fake.py
with FakeWorkspace and a scripted runner that returns a queue of prepared
ExecutionResult objects, matching the real signatures exactly.

Include convenience constructors: green_result(), red_result(failed=1),
timeout_result(), import_error_result().

Add a test that asserts FakeWorkspace and Workspace expose the same public
method names with the same signatures, using inspect.signature. If the two ever
drift, Engineer A's tests start lying about the real system, so I want that
drift to break the build.

Post in the team channel the moment this lands — it unblocks A's test suite.
```

**Done when:** the signature-parity test passes. **Ship this by hour 6** —
Engineer A is waiting on it.

---

## B5 · Repo facts for localisation (60 min, only after B1–B4 are merged)

```
Add src/repro/sandbox/repo_facts.py with two pure functions:

list_source_files(ws, max_files=300) -> list[str]
  Repo-relative .py paths, excluding tests/, sorted, capped.

recent_changes(ws, limit=20) -> list[tuple[str, str, str]]
  (sha, iso_date, path) for recently modified files from `git log --name-only`.
  Return [] if the workspace is not a git repo — do not raise.

Rationale for Engineer C: a bug reported today is far more likely to live in a
file changed last week than in one untouched for two years, so this is a cheap
strong prior for ranking hypotheses.

Cap and sort everything. These outputs go into a prompt, so unbounded output is
a cost bug as well as a correctness one.
```

**Done when:** both functions work on `fixtures/demo_repos/shopcart` and on a
non-git directory without raising.
