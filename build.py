#!/usr/bin/env python3
"""Regenerate every workflow JSON into out/.

Run this, then commit the diff. The diff is the review: it shows exactly what
changed between two versions of a workflow, which is the thing you cannot get
from a canvas.
"""

from __future__ import annotations

import pathlib
import sys

from flows import dead_lead_reactivation, support_ticket_tagging

OUT = pathlib.Path(__file__).parent / "out"

FLOWS = {
    "dead-lead-reactivation.json": dead_lead_reactivation.build,
    "support-ticket-tagging.json": support_ticket_tagging.build,
}


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    OUT.mkdir(exist_ok=True)
    stale: list[str] = []

    for filename, builder in FLOWS.items():
        path = OUT / filename
        content = builder().to_json()
        if check_only:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                stale.append(filename)
            continue
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent)}")

    if check_only:
        if stale:
            print("These files are out of date. Run: python3 build.py")
            for name in stale:
                print(f"  {name}")
            return 1
        print("out/ matches the source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
