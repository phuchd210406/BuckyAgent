#!/usr/bin/env python3
"""Print one seeded client complaint from fixtures/BUGS.md, verbatim.

`make demo` needs the complaint as a single argv string. It used to inline

    sed -n 's/.*Client wrote:\\*\\* //p' fixtures/BUGS.md | head -1

which silently truncated the report at the first newline — the shopcart
complaint wraps over three lines, so the agent was being handed
"...and it charged me" and never saw the "$50" threshold that is the whole bug.
Getting that wrong does not fail loudly; it just quietly demos a worse product.

Exits non-zero with a readable message when the fixture does not contain what
we expect, so `make demo` fails at the fixture rather than passing an empty
--report to the agent.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `* **Client wrote:** "…"`, the quote running to the closing double quote.
COMPLAINT = re.compile(r'\*\*Client wrote:\*\*\s*"(?P<text>.*?)"', re.DOTALL)


def complaints(markdown: str) -> list[str]:
    """Every complaint in file order, with newlines and indentation collapsed."""
    return [" ".join(m.group("text").split()) for m in COMPLAINT.finditer(markdown)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bugs", default="fixtures/BUGS.md", type=Path)
    ap.add_argument("--index", default=0, type=int, help="Which complaint, in file order.")
    args = ap.parse_args(argv)

    if not args.bugs.is_file():
        print(f"{args.bugs}: not found (run make from the repo root)", file=sys.stderr)
        return 2
    found = complaints(args.bugs.read_text(encoding="utf-8"))
    if not found:
        print(f'{args.bugs}: no `**Client wrote:** "…"` block found', file=sys.stderr)
        return 2
    try:
        print(found[args.index])
    except IndexError:
        print(f"{args.bugs}: no complaint at index {args.index} ({len(found)} found)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
