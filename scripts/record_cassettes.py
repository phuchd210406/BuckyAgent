#!/usr/bin/env python3
"""Record cassettes from a real Bedrock run of the golden dataset. OWNER: Engineer C (C4).

    REPRO_RECORD=1 LLM_PROVIDER=bedrock make record        # COSTS MONEY

Runs every case in eval/dataset.yaml end to end against real Bedrock -- real
workspace, real pytest, real patcher -- and writes:

  * src/repro/llm/cassettes/*.json   one per distinct prompt, keyed by
    llm.fake.cassette_key, so `LLM_PROVIDER=fake` replays the same run offline;
  * eval/recorded_runs.json          the verdict and accepted patch of each run,
    which tests/test_replay_fidelity.py asserts replay reproduces.

TWO THINGS THAT DECIDE WHETHER A CASSETTE IS EVER FOUND AGAIN, both learned the
hard way, both about the fact that a cassette key is a hash of the exact prompt:

  * `received_at`. ClientReport stamps it with `now()` by default. Engineer A
    now strips it and `run_id` from every prompt (`agents._common`,
    VOLATILE_REPORT_FIELDS) so they no longer reach a key, but it is still
    PINNED here so the manifest is reproducible, and the full report is written
    to recorded_runs.json so a replay reconstructs what was recorded.
  * `repo_path`. It is rendered into the prompt too, so an absolute path bakes
    THIS machine's checkout directory into every key and the cassettes stop
    working the moment someone else clones the repo somewhere else. The dataset's
    relative path is used verbatim; Workspace resolves it against the cwd.

The session budget guard (task C5) is armed before the first call, so a run that
goes wrong stops spending instead of eating the team's shared $20.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

#: Pinned, so the same dataset always produces the same cassette keys.
RECORDED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

DEFAULT_DATASET = REPO_ROOT / "eval" / "dataset.yaml"
DEFAULT_OUT = REPO_ROOT / "eval" / "recorded_runs.json"


def load_env() -> str:
    """Same .env handling as check_bedrock.py: an exported key beats the file."""
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return "no .env (relying on exported vars)"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return ".env NOT loaded: python-dotenv missing"
    load_dotenv(env_path, override=False)
    for key, value in list(os.environ.items()):
        if key.startswith("AWS_") and not value.strip():
            del os.environ[key]
    return f"{env_path} loaded"


def report_for(case: dict):
    from repro.contracts import ClientReport

    return ClientReport(
        run_id=case["id"],
        raw_text=" ".join(str(case["complaint"]).split()),
        # Relative on purpose: an absolute path would bake this machine into
        # every cassette key. Run from the repo root.
        repo_path=case["repo"],
        received_at=RECORDED_AT,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--case", action="append", default=[], help="Record only these ids.")
    ap.add_argument("--session-budget", type=float, default=0.50,
                    help="Hard stop for the whole recording, in USD (default 0.50).")
    ap.add_argument("--dry-run", action="store_true", help="List what would be recorded.")
    args = ap.parse_args(argv)

    note = load_env()
    # Set BEFORE importing budget: the guard re-reads this on every check, but
    # setting it first means it is armed even for the very first call.
    os.environ["REPRO_SESSION_BUDGET_USD"] = str(args.session_budget)

    import yaml

    from repro.contracts import MAX_RUN_USD
    from repro.graph.build import run
    from repro.llm.bedrock import BedrockLLM, RecordingLLM
    from repro.llm.budget import BudgetExceeded, BudgetGuard, session_guard
    from repro.settings import settings

    cfg = settings()
    cases = yaml.safe_load(args.dataset.read_text())["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in set(args.case)]
    if not cases:
        print(f"no cases selected from {args.dataset}", file=sys.stderr)
        return 2

    cassette_dir = REPO_ROOT / cfg.cassette_dir
    print(f"config     {note}")
    print(f"model      {cfg.model_id}   region {cfg.region}")
    print(f"cassettes  {cassette_dir}")
    print(f"budget     ${MAX_RUN_USD:.2f}/run, ${args.session_budget:.2f} for this session")
    print(f"cases      {len(cases)}: {', '.join(c['id'] for c in cases)}\n")
    if args.dry_run:
        print("--dry-run: nothing recorded, nothing spent.")
        return 0

    records: list[dict] = []
    spent = 0.0
    failures = 0

    for case in cases:
        report = report_for(case)
        client = BedrockLLM(budget=BudgetGuard(label=report.run_id))
        llm = RecordingLLM(client, cassette_dir=cassette_dir, enabled=True)

        started = time.monotonic()
        print(f"--- {report.run_id} ...", flush=True)
        try:
            record = run(report, llm)
            verdict = record.verdict.value
            accepted = [f.patch.unified_diff for f in record.fix_attempts if f.accepted]
            error = None
        except BudgetExceeded as exc:
            print(f"    STOPPED BY THE BUDGET GUARD: {exc}")
            spent += client.budget.usd
            failures += 1
            break
        except Exception as exc:  # noqa: BLE001 - one bad case must not lose the rest
            verdict, accepted, error = None, [], f"{type(exc).__name__}: {exc}"
            record = None
            failures += 1

        cost = client.budget.usd
        spent += cost
        elapsed = time.monotonic() - started

        if error:
            print(f"    FAILED after {elapsed:.1f}s (${cost:.6f}): {error[:160]}")
        else:
            print(f"    {verdict}  in {elapsed:.1f}s  ${cost:.6f}  "
                  f"({client.budget.calls} calls, first-attempt schema rate "
                  f"{client.first_attempt_rate:.0%})")

        records.append({
            "case_id": case["id"],
            "report": report.model_dump(mode="json"),
            "verdict": verdict,
            "accepted_patches": accepted,
            "error": error,
            "expected_verdict": case.get("expected_verdict"),
            "usd": round(cost, 6),
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "model_id": cfg.model_id,
        "region": cfg.region,
        "recorded_at": RECORDED_AT.isoformat(),
        "total_usd": round(spent, 6),
        "runs": records,
    }, indent=2, sort_keys=True) + "\n")

    written = len(list(cassette_dir.glob("*.json"))) if cassette_dir.is_dir() else 0
    print(f"\ncassettes  {written} in {cassette_dir}")
    print(f"manifest   {args.out}")
    print(f"TOTAL SPENT  ${spent:.6f}   (session guard tripped: {session_guard().tripped})")
    if failures:
        print(f"{failures} case(s) did not complete — see the manifest.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
