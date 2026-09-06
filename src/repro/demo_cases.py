"""The seeded cases, as the UI needs them.

Every demo repository has a client complaint written for it, and until now that
complaint lived only in `eval/dataset.yaml` and `fixtures/BUGS.md` — so the one
person who most needs it, whoever is standing in front of the app choosing a
repository, could not see it. Picking `statusboard` from a dropdown tells you
nothing; picking it and reading "the site keeps going down, my colleague can get
on fine though" tells you exactly what you are about to watch.

READ ONLY, and read from the dataset rather than copied out of it. Engineer E
owns `eval/dataset.yaml`; if a complaint is reworded there, the screen must
reword with it or the demo is showing a complaint the eval no longer scores.

The file is re-read when its mtime changes, so editing the dataset does not
need the API restarted — a demo is edited minutes before it is given.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from repro.settings import REPO_ROOT

LOG = logging.getLogger("repro.demo_cases")

DATASET_PATH = REPO_ROOT / "eval" / "dataset.yaml"

#: (path, mtime) -> parsed cases. One entry; the key is what invalidates it.
_CACHE: dict[tuple[str, float], list["DemoCase"]] = {}


@dataclass(frozen=True)
class DemoCase:
    """One seeded repository and the complaint that goes with it."""

    id: str
    repo: str  # as written in the dataset, relative to the repo root
    complaint: str
    expected_verdict: str = ""
    expected_files: tuple[str, ...] = ()
    notes: str = ""

    @property
    def path(self) -> Path:
        """Absolute path to the checkout, so the API can hand it to a run."""
        candidate = Path(self.repo)
        return candidate if candidate.is_absolute() else (REPO_ROOT / candidate)

    @property
    def name(self) -> str:
        return self.path.name

    def as_dict(self) -> dict:
        return {
            "case_id": self.id,
            "path": str(self.path),
            "name": self.name,
            "complaint": self.complaint,
            # Ground truth, and shown as such. Three of the nine cases are
            # supposed to end with no patch at all, and a demo that cannot say
            # so in advance cannot show off the run where the agent refuses.
            "expected_verdict": self.expected_verdict,
            "expected_files": list(self.expected_files),
            "notes": self.notes,
        }


def load_cases(path: Path | None = None) -> list[DemoCase]:
    """Every case in the golden dataset. Never raises: the UI must still open.

    A dataset that is missing, unparseable, or the wrong shape costs the screen
    its complaints and nothing else — the repositories are still listed from
    disk, and a run still works.
    """
    dataset = Path(path or DATASET_PATH)
    try:
        stamp = (str(dataset), dataset.stat().st_mtime)
    except OSError:
        return []
    if stamp in _CACHE:
        return _CACHE[stamp]

    try:
        import yaml

        raw = yaml.safe_load(dataset.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - a broken dataset must not 500 the UI
        LOG.warning("could not read %s: %s", dataset, exc)
        return []

    cases: list[DemoCase] = []
    for entry in raw.get("cases") or []:
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo") or "").strip()
        complaint = " ".join(str(entry.get("complaint") or "").split())
        if not repo or not complaint:
            continue
        cases.append(
            DemoCase(
                id=str(entry.get("id") or Path(repo).name),
                repo=repo,
                complaint=complaint,
                expected_verdict=str(entry.get("expected_verdict") or ""),
                expected_files=tuple(entry.get("expected_files") or ()),
                notes=" ".join(str(entry.get("notes") or "").split()),
            )
        )

    _CACHE.clear()  # one dataset, one entry: this is a cache, not a history
    _CACHE[stamp] = cases
    return cases


def case_for(repo_path: str | Path) -> DemoCase | None:
    """The case whose repository is `repo_path`, matched on the resolved path.

    Matched on the path rather than the directory name so two datasets that
    both contain a `shopcart` cannot silently swap complaints.
    """
    try:
        wanted = Path(repo_path).expanduser().resolve()
    except OSError:  # pragma: no cover - an unresolvable path is simply not a case
        return None
    for case in load_cases():
        try:
            if case.path.resolve() == wanted:
                return case
        except OSError:  # pragma: no cover
            continue
    return None
