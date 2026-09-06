"""
The command line. `make demo` and `make record` both come through here.

    LLM_PROVIDER=fake python -m repro.cli run --repo <path> --report "<complaint>"

DEPLOY.md stage 1 lists `make demo` as the free, replayed run, and its failure
playbook makes it the fallback for the worst case there is: the lease is gone,
the account is frozen, and the demo has to happen anyway. So this path must work
on a clean clone with no credentials, no network and nothing but the committed
cassettes -- which is exactly what `LLM_PROVIDER=fake` gives it.

The provider comes from `repro.clients.llm_for`, never from a branch of its own:
two callers that disagree about what `LLM_PROVIDER=fake` means is how a "free"
command quietly starts costing money.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_REPO = "fixtures/demo_repos/shopcart"


def load_env() -> None:
    """Make .env effective for `bedrock`, harmless for `fake`.

    override=False so an exported key beats the file: DEPLOY.md's 12-hour
    routine is to re-export fresh keys, and those must win over a stale file.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)
    for key, value in list(os.environ.items()):
        if key.startswith("AWS_") and not value.strip():
            del os.environ[key]


def read_report_text(args: argparse.Namespace) -> str:
    """The complaint, verbatim. Whitespace collapsed, nothing else changed."""
    if args.report is not None:
        raw = args.report
    elif args.report_file is not None:
        raw = Path(args.report_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raise SystemExit(
            "no report given: pass --report \"...\", --report-file PATH, or pipe it in.\n"
            "  make demo takes it from scripts/demo_report.py"
        )
    text = " ".join(raw.split())
    if not text:
        raise SystemExit("the report is empty; there is nothing to investigate")
    return text


def summarise(record, verbose: bool) -> None:
    """What a person watching the demo needs to see, in the order it happened."""
    print(f"\nrun        {record.run_id}")
    print(f"verdict    {record.verdict.value.upper()}")
    print(f"cost       ${record.usage.usd:.6f} over {record.usage.calls} model calls")
    print(f"wall clock {record.wall_clock_s:.1f}s")

    if record.facts and record.facts.observed_behaviour:
        print(f"\nobserved   {record.facts.observed_behaviour}")
    for question in record.questions:
        print(f"\nasked the client: {question.question}")

    for hypothesis in record.hypotheses[:3]:
        symbol = f":{hypothesis.symbol}" if hypothesis.symbol else ""
        print(f"  candidate  {hypothesis.file_path}{symbol}  ({hypothesis.confidence:.2f})")

    for attempt in record.repro_attempts:
        state = "REPRODUCED" if attempt.reproduced else "not reproduced"
        print(f"  repro {attempt.attempt_no}    {attempt.test.path}: {state}")
    for attempt in record.fix_attempts:
        state = "ACCEPTED" if attempt.accepted else "rejected"
        print(f"  fix {attempt.attempt_no}      {', '.join(attempt.patch.files_touched)}: {state}")

    accepted = [f for f in record.fix_attempts if f.accepted]
    if accepted and verbose:
        print("\n--- patch ---")
        print(accepted[0].patch.unified_diff)

    if record.handover:
        print("\n--- for the client ---")
        print(record.handover.client_reply)
        if verbose:
            print("\n--- for the developer ---")
            print(record.handover.dev_summary)

    violations = record.check_invariants()
    if violations:
        print("\nINVARIANTS VIOLATED:")
        for violation in violations:
            print(f"  - {violation}")


def cmd_run(args: argparse.Namespace) -> int:
    load_env()

    from repro.clients import llm_for
    from repro.contracts import ClientReport
    from repro.graph.build import run

    report = ClientReport(
        run_id=args.run_id or uuid.uuid4().hex[:12],
        raw_text=read_report_text(args),
        # Left as given, usually relative. Workspace resolves it against the cwd,
        # and a relative path keeps this machine's checkout directory out of the
        # prompts -- and so out of the cassette keys.
        repo_path=args.repo,
    )
    provider = args.provider or os.getenv("LLM_PROVIDER", "fake")
    print(f"provider   {provider}    repo {report.repo_path}")

    record = run(report, llm_for(provider))

    if args.json:
        print(json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        summarise(record, args.verbose)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repro", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    runner = subcommands.add_parser("run", help="Investigate one client report.")
    runner.add_argument("--repo", default=DEFAULT_REPO,
                        help=f"Path to the project to investigate (default {DEFAULT_REPO}).")
    runner.add_argument("--report", default=None, help="The complaint, verbatim.")
    runner.add_argument("--report-file", default=None, help="Read the complaint from a file.")
    runner.add_argument("--run-id", default=None, help="Defaults to a random id.")
    runner.add_argument("--provider", default=None, choices=["fake", "bedrock"],
                        help="Overrides LLM_PROVIDER.")
    runner.add_argument("--json", action="store_true", help="Print the RunRecord as JSON.")
    runner.add_argument("-v", "--verbose", action="store_true",
                        help="Also print the patch and the developer summary.")
    runner.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
