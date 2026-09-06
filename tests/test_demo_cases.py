"""The seeded complaints, on their way to the screen.

The complaint and the repository only mean anything as a pair: `statusboard`
tells you nothing, and "everything has been really slow for me since tuesday,
my colleague can get on fine though" tells you exactly what the agent is
supposed to conclude. These are read from `eval/dataset.yaml` rather than copied
out of it, because Engineer E owns that file and a reworded complaint must reach
the screen or the demo shows something the eval no longer scores.
"""
from __future__ import annotations

import pytest

from repro.demo_cases import DATASET_PATH, case_for, load_cases
from repro.settings import REPO_ROOT


def test_every_seeded_repository_has_a_complaint():
    cases = load_cases()

    assert len(cases) >= 8, "the golden dataset should carry every seeded case"
    for case in cases:
        assert case.complaint.strip(), f"{case.id} has no complaint"
        assert case.path.is_dir(), f"{case.id} names a repository that is not on disk"


def test_the_complaint_is_the_dataset_s_own_words():
    shopcart = case_for(REPO_ROOT / "fixtures" / "demo_repos" / "shopcart")

    assert shopcart is not None
    assert "free postage over $50" in shopcart.complaint
    assert shopcart.expected_verdict == "reproduced_and_fixed"
    assert shopcart.expected_files == ("shopcart/pricing.py",)


def test_the_cases_that_should_produce_no_patch_say_so():
    """Three of them are the point: an agent that always finds something fails here."""
    no_patch = [case for case in load_cases() if not case.expected_files]

    assert len(no_patch) >= 3
    assert {case.expected_verdict for case in no_patch} <= {
        "not_reproduced",
        "needs_clarification",
    }


def test_a_repository_is_matched_by_path_not_by_name(tmp_path):
    """Two datasets could each hold a `shopcart`; only one is the one on screen."""
    (tmp_path / "shopcart").mkdir()

    assert case_for(tmp_path / "shopcart") is None
    assert case_for(REPO_ROOT / "fixtures" / "demo_repos" / "shopcart") is not None


def test_the_dataset_is_re_read_when_it_changes(tmp_path):
    """A demo is edited minutes before it is given; restarting the API is friction."""
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(
        "cases:\n  - id: one\n    repo: fixtures/demo_repos/shopcart\n    complaint: first\n"
    )
    assert load_cases(dataset)[0].complaint == "first"

    dataset.write_text(
        "cases:\n  - id: one\n    repo: fixtures/demo_repos/shopcart\n    complaint: second\n"
    )
    # mtime resolution is coarse enough on some filesystems to need a nudge.
    import os

    stat = dataset.stat()
    os.utime(dataset, (stat.st_atime, stat.st_mtime + 10))

    assert load_cases(dataset)[0].complaint == "second"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "cases:\n",
        "cases: not-a-list\n",
        "cases:\n  - id: broken\n",  # no repo, no complaint
        "{{{ not yaml at all",
    ],
)
def test_a_broken_dataset_costs_the_complaints_and_nothing_else(tmp_path, text):
    """The repositories are still listed from disk and a run still works."""
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(text)

    assert load_cases(dataset) == []


def test_a_missing_dataset_is_not_an_error(tmp_path):
    assert load_cases(tmp_path / "nowhere.yaml") == []


def test_the_dataset_lives_where_this_module_looks_for_it():
    """A moved dataset would silently empty the screen; fail here instead."""
    assert DATASET_PATH.is_file(), f"{DATASET_PATH} is gone or has moved"
