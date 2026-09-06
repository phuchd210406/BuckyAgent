"""The fake must not lie about the real thing. OWNER: Engineer B.

Engineer A writes graph tests against `FakeWorkspace` and `ScriptedRunner`. The
moment either drifts from the real `Workspace` / `run_pytest`, those tests are
still green but no longer say anything about the system that ships. So drift is
a build failure here, in both directions:

  * a public member on Workspace that FakeWorkspace lacks -> A's test would
    AttributeError only in production;
  * a public member on FakeWorkspace that Workspace lacks -> A writes against a
    capability the real sandbox does not have.
"""
from __future__ import annotations

import inspect

import pytest

from repro.agents._common import is_reproduction
from repro.contracts import SANDBOX_TIMEOUT_S, ExecutionResult
from repro.sandbox.fake import (
    FakeWorkspace,
    ScriptedRunner,
    green_result,
    import_error_result,
    red_result,
    timeout_result,
)
from repro.sandbox.runner import run_pytest
from repro.sandbox.workspace import Workspace

#: Helpers that exist only on the fake. Anything else extra is drift.
FAKE_ONLY = frozenset({"written_files"})

#: Constructing differs by design: the fake has no tree to copy, so its
#: arguments are optional. Names, order and kinds must still match.
DEFAULTS_MAY_DIFFER = frozenset({"__init__"})

#: `__enter__` is annotated with its OWN class on each side. That is correct,
#: not drift, so compare its parameters and check the return type separately.
SELF_RETURNING = frozenset({"__enter__"})


def public_members(cls: type) -> dict[str, str]:
    """{name: 'property' | 'method'} for the public API, plus the with-protocol."""
    out: dict[str, str] = {}
    for name, member in vars(cls).items():
        if name.startswith("_") and name not in ("__init__", "__enter__", "__exit__"):
            continue
        if isinstance(member, property):
            out[name] = "property"
        elif callable(member):
            out[name] = "method"
    return out


REAL = public_members(Workspace)
FAKE = public_members(FakeWorkspace)


# ---------------------------------------------------------------------------
# The drift alarm
# ---------------------------------------------------------------------------
def test_the_fake_exposes_everything_the_real_one_does():
    missing = sorted(set(REAL) - set(FAKE))
    assert not missing, (
        f"FakeWorkspace is missing {missing}. Engineer A's tests would pass here and "
        f"AttributeError in production."
    )


def test_the_fake_invents_nothing_beyond_its_declared_helpers():
    extra = sorted(set(FAKE) - set(REAL) - FAKE_ONLY)
    assert not extra, (
        f"FakeWorkspace has public members the real Workspace lacks: {extra}. Either "
        f"add them to Workspace or list them in FAKE_ONLY so nobody mistakes them "
        f"for the real API."
    )


@pytest.mark.parametrize("name", sorted(REAL))
def test_members_are_the_same_kind(name: str):
    assert FAKE[name] == REAL[name], (
        f"{name} is a {REAL[name]} on Workspace but a {FAKE[name]} on FakeWorkspace"
    )


@pytest.mark.parametrize("name", sorted(n for n, kind in REAL.items() if kind == "method"))
def test_method_signatures_match_exactly(name: str):
    real = inspect.signature(getattr(Workspace, name))
    fake = inspect.signature(getattr(FakeWorkspace, name))

    if name in SELF_RETURNING:
        assert list(fake.parameters) == list(real.parameters)
        assert real.return_annotation == "'Workspace'"
        assert fake.return_annotation == "'FakeWorkspace'"
        return

    if name in DEFAULTS_MAY_DIFFER:
        strip = [(p.name, p.kind, p.annotation) for p in real.parameters.values()]
        assert [(p.name, p.kind, p.annotation) for p in fake.parameters.values()] == strip
        return

    assert fake == real, f"{name} has drifted:\n  Workspace{real}\n  FakeWorkspace{fake}"


@pytest.mark.parametrize("name", sorted(n for n, kind in REAL.items() if kind == "property"))
def test_property_return_annotations_match(name: str):
    real = inspect.signature(getattr(Workspace, name).fget)
    fake = inspect.signature(getattr(FakeWorkspace, name).fget)
    assert fake.return_annotation == real.return_annotation


def test_the_scripted_runner_matches_run_pytest():
    """The fake runner is swapped in for the real function; same call shape."""
    real = inspect.signature(run_pytest)
    fake = inspect.signature(ScriptedRunner.__call__)
    without_self = fake.replace(parameters=list(fake.parameters.values())[1:])

    assert without_self == real, (
        f"ScriptedRunner has drifted from run_pytest:\n  {real}\n  {without_self}"
    )


# ---------------------------------------------------------------------------
# The fake must BEHAVE like the real one where it claims to
# ---------------------------------------------------------------------------
@pytest.fixture
def real_ws(tmp_path):
    (tmp_path / "src").mkdir()
    workspace = Workspace(tmp_path / "src", tmp_path / "roots")
    yield workspace
    workspace.close()


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "/etc/passwd", "shopcart/../../..", "", "   ", "."]
)
def test_the_fake_rejects_every_path_the_real_one_rejects(real_ws, bad: str):
    """A fake that accepts a traversal makes A's security tests meaningless."""
    fake = FakeWorkspace()

    with pytest.raises(ValueError) as real_exc:
        real_ws.resolve_path(bad)
    with pytest.raises(ValueError) as fake_exc:
        fake.resolve_path(bad)

    # Same failure, and the same first line of explanation.
    assert str(real_exc.value).split(":")[0] == str(fake_exc.value).split(":")[0]


def test_the_fake_accepts_what_the_real_one_accepts(real_ws):
    fake = FakeWorkspace()

    for good in ("tests/test_x.py", "a/b/c.py", "pkg/../pkg/mod.py"):
        assert real_ws.resolve_path(good).relative_to(real_ws.path) == fake.resolve_path(
            good
        ).relative_to(fake.path)


def test_read_file_truncates_identically(real_ws):
    """Both call workspace.truncate_head, so this pins them together."""
    fake = FakeWorkspace()
    body = "x" * 50_000

    real_ws.write_file("big.txt", body)
    fake.write_file("big.txt", body)

    for limit in (20, 100, 1000, 8000):
        assert fake.read_file("big.txt", limit) == real_ws.read_file("big.txt", limit)


def test_a_closed_fake_refuses_work_like_a_closed_real_one(real_ws):
    real_ws.close()
    fake = FakeWorkspace()
    fake.close()

    assert real_ws.closed is True and fake.closed is True
    for box in (real_ws, fake):
        with pytest.raises(RuntimeError, match="closed"):
            box.resolve_path("a.py")
    fake.close()  # idempotent, like the real one


# ---------------------------------------------------------------------------
# The prepared results must mean what the product thinks they mean
# ---------------------------------------------------------------------------
def test_green_result_is_the_only_green_one():
    assert green_result().green is True
    assert red_result().green is False
    assert timeout_result().green is False
    assert import_error_result().green is False


def test_the_results_land_where_is_reproduction_expects():
    """`is_reproduction` is the product's gate. The fakes must sort into it correctly.

    Otherwise A's loop tests are green while the real system takes a different
    branch for the same situation.
    """
    assert is_reproduction(red_result()) is True, "a red test IS the reproduction"
    assert is_reproduction(green_result()) is False, "it passed: the bug did not happen"
    assert is_reproduction(timeout_result()) is False, "a timeout proves nothing"
    assert is_reproduction(import_error_result()) is False, "it never ran"


def test_result_counts_are_what_they_claim():
    assert green_result(passed=7).passed == 7
    assert red_result(failed=3, passed=2).failed == 3
    assert red_result(failed=3, passed=2).passed == 2
    assert timeout_result(timeout_s=5).timed_out is True
    assert timeout_result(timeout_s=5).exit_code == -1
    assert import_error_result().errors == 1
    # A timeout knows nothing, so it must not claim counts.
    assert (timeout_result().passed, timeout_result().failed) == (0, 0)


def test_prepared_results_fit_the_contract_field_caps():
    """ExecutionResult caps its tails; a fake that overflowed would raise late."""
    for result in (green_result(), red_result(), timeout_result(), import_error_result()):
        assert isinstance(result, ExecutionResult)


# ---------------------------------------------------------------------------
# ScriptedRunner behaviour
# ---------------------------------------------------------------------------
def test_results_come_back_in_order():
    runner = ScriptedRunner([red_result(), red_result(), green_result()])
    ws = FakeWorkspace()

    outcomes = [runner(ws, "tests/test_a.py").green for _ in range(3)]

    assert outcomes == [False, False, True]
    assert runner.calls == [("tests/test_a.py", SANDBOX_TIMEOUT_S)] * 3


def test_running_out_of_results_is_loud():
    """A silent green here would hide a graph that looped further than expected."""
    runner = ScriptedRunner([green_result()])
    ws = FakeWorkspace()
    runner(ws)

    with pytest.raises(AssertionError, match="ran out of results"):
        runner(ws)


def test_a_default_covers_calls_the_test_does_not_care_about():
    runner = ScriptedRunner([red_result()], default=green_result())
    ws = FakeWorkspace()

    assert runner(ws).green is False
    assert runner(ws).green is True
    assert runner(ws).green is True


def test_calls_are_recorded_for_assertions():
    runner = ScriptedRunner(default=green_result())
    ws = FakeWorkspace()

    runner(ws, "tests/test_one.py::test_a", timeout_s=5)
    runner(ws)  # the whole suite

    assert runner.calls == [("tests/test_one.py::test_a", 5), (None, SANDBOX_TIMEOUT_S)]


def test_queue_appends_and_chains():
    runner = ScriptedRunner().queue(red_result()).queue(green_result(), green_result())
    assert len(runner.results) == 3


def test_run_pytest_alias_is_the_same_callable():
    """Swappable whether the caller has the function or the object."""
    runner = ScriptedRunner([green_result()])
    assert runner.run_pytest(FakeWorkspace()).green is True
