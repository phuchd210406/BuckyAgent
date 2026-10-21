"""Plumbing shared by the nodes. OWNER: Engineer A.

Nothing in here is prompt text and nothing in here calls a model. The two
predicates are the product's safety property expressed once, in one place, so
no node can quietly disagree with another about what counts as a reproduction.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from repro.contracts import (
    SANDBOX_MAX_OUTPUT_CHARS,
    ClientReport,
    ExecutionResult,
    LLMResponse,
    TokenUsage,
    Verdict,
)
from repro.graph.state import GraphState
from repro.llm.base import SchemaValidationError

#: At most this many distinct suspected files are shown to the model. The
#: localiser's first guess is usually right; the second covers the case where
#: it named the caller and the bug lives one file over.
MAX_SOURCE_FILES = 2

#: Fields of ClientReport that change on every run and mean nothing to the
#: model. They are stripped from every prompt because FakeLLM keys cassettes on
#: a hash of the exact prompt text: leave a uuid and a timestamp in there and
#: every cassette is unfindable the moment it is written, which silently costs
#: real money on a path that is supposed to be free.
VOLATILE_REPORT_FIELDS = frozenset({"run_id", "received_at"})


@lru_cache(maxsize=1)
def model_failures() -> tuple[type[BaseException], ...]:
    """Exceptions meaning "the model did not give us a usable object".

    Both are recoverable inside a bounded loop: the node spends an attempt and
    tries again, rather than taking the whole run down. TruncatedResponseError
    lives in bedrock.py, so it is imported lazily -- the fake and stub paths
    must not need boto3 to run.
    """
    failures: list[type[BaseException]] = [SchemaValidationError]
    try:
        from repro.llm.bedrock import TruncatedResponseError
    except ImportError:  # pragma: no cover - boto3 absent
        return tuple(failures)
    failures.append(TruncatedResponseError)
    return tuple(failures)


def payload(**parts: Any) -> str:
    """Render the state slices a node needs as JSON for the user turn.

    Engineer C owns how a prompt READS; this owns only what data it carries.
    Keep the parts small -- ARCHITECTURE.md rule 4: nothing goes in uncapped
    (source excerpts included), and anything put in here is re-read on every
    turn of a loop.
    """
    return json.dumps({key: _plain(value) for key, value in parts.items()}, default=str)


def _plain(value: Any) -> Any:
    if isinstance(value, ClientReport):
        # Never the run id or the received-at stamp: see VOLATILE_REPORT_FIELDS.
        return value.model_dump(mode="json", exclude=set(VOLATILE_REPORT_FIELDS))
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def source_excerpts(sandbox: Any, hypotheses: list[Any], *, max_files: int = MAX_SOURCE_FILES) -> list[dict]:
    """The real code of the suspected files: [{"path", "code"}], capped.

    Without this the repro node writes tests against signatures it has to
    guess, and the fix node writes diffs against a file it has never seen --
    so the test fails with a TypeError and the diff's context lines do not
    exist. Each file is capped by the sandbox (SOURCE_MAX_CHARS).

    A sandbox without `read_source` (an older test double) simply yields no
    excerpts: the prompt is poorer, the run still goes ahead.
    """
    read = getattr(sandbox, "read_source", None)
    if read is None:
        return []
    excerpts: list[dict] = []
    seen: set[str] = set()
    for hypothesis in hypotheses or []:
        path = (getattr(hypothesis, "file_path", "") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        code = read(path)
        if code:
            excerpts.append({"path": path, "code": code})
        if len(excerpts) >= max_files:
            break
    return excerpts


def add_usage(state: GraphState, *responses: LLMResponse) -> TokenUsage:
    """This node's spend, added to the run's. The graph re-checks it either way."""
    total = state.get("usage") or TokenUsage()
    for response in responses:
        total = total.merge(response.usage)
    return total


#: `tests/test_x.py:12: in test_name` -- one frame of pytest's --tb=short output.
_FRAME_RE = re.compile(r"^(?P<path>\S[^:]*\.py):(?P<line>\d+): in \S+")
#: `E   TypeError: total() got an unexpected keyword argument 'promo'`
_EXCEPTION_RE = re.compile(r"^E\s+(?P<type>[A-Za-z_][\w.]*):\s?(?P<message>.*)$")

#: Exceptions that, raised from the test's own code, mean the TEST is wrong.
_ALWAYS_BROKEN = frozenset(
    {"NameError", "UnboundLocalError", "ImportError", "ModuleNotFoundError", "SyntaxError"}
)
#: A TypeError is only the test's fault when it is a call-signature mismatch:
#: the test called the project's code with arguments it does not take.
_SIGNATURE_MISMATCH_RE = re.compile(
    r"unexpected keyword argument"
    r"|missing \d+ required (?:positional|keyword-only) arguments?"
    r"|takes (?:from \d+ to )?\d+ positional arguments? but \d+ (?:were|was) given"
    r"|takes no arguments"
    r"|got multiple values for argument"
    r"|got some positional-only arguments passed as keyword arguments"
)
#: An AttributeError is only the test's fault when it names something the
#: project's module or class does not have, i.e. the test invented an API.
_MISSING_API_RE = re.compile(r"^(?:module|type object) '[^']+' has no attribute")


def _is_test_file(path: str) -> bool:
    pure = PurePosixPath(path.replace("\\", "/"))
    return "tests" in pure.parts or "test" in pure.parts or pure.name.startswith("test_")


def broken_test_reason(result: ExecutionResult) -> str | None:
    """Why a FAILING test is the test's fault rather than the bug's, or None.

    pytest counts a test that calls `total(lines, promo=...)` on a function
    that takes no `promo` as "1 failed", exactly like a real assertion failure.
    It is not a reproduction: it proves the test was written against an API
    that does not exist, and a fix "verified" against it fixes nothing.

    The rule is narrow on purpose. The exception must be raised in the TEST
    file's own frame (the deepest frame pytest shows), and must be one that
    only a wrong test produces there: an undefined name, a failed import, a
    call-signature mismatch, or a module/class attribute that does not exist.
    The same TypeError raised inside the project's code is the bug, and counts.
    """
    deepest: str | None = None
    for line in result.stdout_tail.splitlines():
        frame = _FRAME_RE.match(line)
        if frame:
            deepest = frame.group("path")
            continue
        if line.startswith("____"):
            deepest = None  # a new failure's section begins
            continue
        exc = _EXCEPTION_RE.match(line)
        if not exc or deepest is None:
            continue
        frame_path, deepest = deepest, None  # judge each failure once
        if not _is_test_file(frame_path):
            continue
        name = exc.group("type").rsplit(".", 1)[-1]
        message = exc.group("message").strip()
        if (
            name in _ALWAYS_BROKEN
            or (name == "TypeError" and _SIGNATURE_MISMATCH_RE.search(message))
            or (name == "AttributeError" and _MISSING_API_RE.match(message))
        ):
            return f"{name}: {message}"[:300]
    return None


def is_reproduction(result: ExecutionResult) -> bool:
    """True only when the test RAN and FAILED because of the code, not itself.

    The four ways this is False are all different, and conflating them is how a
    patch gets accepted for a bug nobody ever saw:
      * the test passed        -> the reported behaviour did not happen;
      * the test errored       -> it never ran (import or collection error);
      * the test timed out     -> we know nothing at all;
      * the test is broken     -> it failed on its own mistake, e.g. it called
                                  a function with arguments it does not take.
    """
    return (
        not result.timed_out
        and result.errors == 0
        and result.failed > 0
        and broken_test_reason(result) is None
    )


def why_not_reproduced(result: ExecutionResult) -> str:
    """One audit-trail line naming which of the four cases this was."""
    if result.timed_out:
        return "The test did not finish inside the sandbox timeout, so it proves nothing."
    if result.errors:
        return (
            "The test errored before it could run (import or collection error). "
            "That is a broken test, not a reproduction of the client's bug."
        )
    broken = broken_test_reason(result) if result.failed else None
    if broken:
        return (
            f"The test failed on its own mistake ({broken}), raised in the test file itself. "
            "It uses the project's code wrongly, so it is a broken test, not a reproduction. "
            "Check the real signatures in `source`."
        )
    if result.failed:
        return f"The test ran and failed ({result.failed} failed): the reported behaviour occurred."
    return "The test ran and passed, so the behaviour the client described did not occur."


def verdict_for(state: GraphState) -> Verdict:
    """The verdict the evidence in state supports. Read-only, no model."""
    if state.get("verdict") == Verdict.ABORTED_BUDGET:
        return Verdict.ABORTED_BUDGET
    reproduced = any(a.reproduced for a in state.get("repro_attempts", []))
    accepted = any(f.accepted for f in state.get("fix_attempts", []))
    if reproduced and accepted:
        return Verdict.REPRODUCED_AND_FIXED
    if reproduced:
        return Verdict.REPRODUCED_NOT_FIXED
    return Verdict.NOT_REPRODUCED


def errored(message: str) -> ExecutionResult:
    """An ExecutionResult for something that never got as far as running."""
    return ExecutionResult(
        exit_code=1,
        stdout_tail="",
        stderr_tail=message[:SANDBOX_MAX_OUTPUT_CHARS],
        duration_s=0.0,
        errors=1,
    )
