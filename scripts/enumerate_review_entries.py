#!/usr/bin/env python3
"""Enumerate every entry heading in review.md in order of appearance.

An "entry heading" is a level-3 markdown heading (`### `) whose title begins
with an entry identifier of one of these forms:
  - `### PREFIX-N — ...`     (e.g. `### T-1 — ...`, `### XE-19-4 — ...`)
  - `### [PREFIX-N] — ...`   (e.g. `### [XV-95] — ...`)

PREFIX is one or more uppercase letters/digits; N is one or more uppercase
letters/digits. The heading text after the identifier is irrelevant — we
count by heading position.

Output: writes /home/z/my-project/voice-typer/scripts/review_entries.json
containing a list of dicts: [{ordinal, line, raw, prefix, num, title}, ...].
Also prints a summary.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REVIEW = Path("/home/z/my-project/voice-typer/review.md")
OUT = Path("/home/z/my-project/voice-typer/scripts/review_entries.json")

# Match: 3 hashes, whitespace, then either `PREFIX-N` or `[PREFIX-N]`, then
# anything (the title). PREFIX = [A-Z][A-Z0-9]*;  N = [A-Z0-9]+.
ENTRY_RE = re.compile(r"^###\s+(?:\[([A-Z][A-Z0-9]*-[A-Z0-9]+)\]|([A-Z][A-Z0-9]*-[A-Z0-9]+))\b")


def main() -> None:
    lines = REVIEW.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    for i, line in enumerate(lines, start=1):
        m = ENTRY_RE.match(line)
        if not m:
            continue
        ident = m.group(1) or m.group(2)
        # title = everything after the identifier on the same line
        rest = line[m.end() :]
        # strip leading space + em-dash variations
        title = rest.lstrip(" -—–\t")
        entries.append(
            {
                "ordinal": len(entries) + 1,
                "line": i,
                "raw": line,
                "id": ident,
                "title": title,
            }
        )

    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Total entries: {len(entries)}")
    if entries:
        print(f"First: #{entries[0]['ordinal']} line {entries[0]['line']} -> {entries[0]['raw'][:80]}")
        print(f"Last:  #{entries[-1]['ordinal']} line {entries[-1]['line']} -> {entries[-1]['raw'][:80]}")
    # show entries 298-302 and 598-602 if present
    for n in [298, 299, 300, 301, 302, 598, 599, 600, 601, 602]:
        if 1 <= n <= len(entries):
            e = entries[n - 1]
            print(f"  #{n} (line {e['line']}): {e['raw'][:120]}")


if __name__ == "__main__":
    main()
