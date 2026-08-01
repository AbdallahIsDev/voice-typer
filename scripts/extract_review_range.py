#!/usr/bin/env python3
"""Extract entries #FIX_START..#FIX_END from review.md (Fix-Existing mode).

Reads /home/z/my-project/voice-typer/scripts/review_entries.json (produced by
enumerate_review_entries.py), then for each entry in the range, slices the
full markdown block from its heading line up to (but not including) the next
entry heading or the next level-1/level-2 heading, whichever comes first.

Writes a JSON file with one record per entry containing:
  ordinal, line_start, line_end, id, title, raw_block
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REVIEW = Path("/home/z/my-project/voice-typer/review.md")
ENTRIES = Path("/home/z/my-project/voice-typer/scripts/review_entries.json")
OUT = Path("/home/z/my-project/voice-typer/scripts/review_range.json")

FIX_START = 300
FIX_END = 600  # capped to total


def main() -> None:
    entries = json.loads(ENTRIES.read_text(encoding="utf-8"))
    total = len(entries)
    end = min(FIX_END, total)
    start = max(1, FIX_START)
    if start > total:
        print(f"ERROR: FIX_START={FIX_START} > total entries={total}")
        return
    selected = entries[start - 1 : end]

    text_lines = REVIEW.read_text(encoding="utf-8", errors="replace").splitlines()
    # Determine the end line for each selected entry: the line before the next
    # entry heading, OR the line before the next `## ` (level-2) heading,
    # whichever is smaller.
    entry_re = re.compile(r"^###\s+(?:\[([A-Z][A-Z0-9]*-[A-Z0-9]+)\]|([A-Z][A-Z0-9]*-[A-Z0-9]+))\b")
    l2_re = re.compile(r"^##\s+\S")

    records = []
    for e in selected:
        ls = e["line"]  # 1-indexed
        # search forward for next entry heading or level-2 heading
        le = len(text_lines)
        for j in range(ls, len(text_lines)):  # j is 0-indexed
            line = text_lines[j]
            if j + 1 == ls:
                continue  # skip the entry's own heading line
            if entry_re.match(line) or l2_re.match(line):
                le = j  # 0-indexed line that ends the block (exclusive)
                break
        # slice is text_lines[ls-1 : le] (python 0-indexed)
        block = "\n".join(text_lines[ls - 1 : le]).rstrip()
        records.append(
            {
                "ordinal": e["ordinal"],
                "line_start": ls,
                "line_end": le,
                "id": e["id"],
                "title": e["title"],
                "raw_block": block,
            }
        )

    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Total entries: {total}; extracted #{start}..#{len(selected) + start - 1} ({len(selected)} entries)")
    for r in records:
        print(f"  #{r['ordinal']} (lines {r['line_start']}-{r['line_end']}): {r['id']} — {r['title'][:80]}")


if __name__ == "__main__":
    main()
