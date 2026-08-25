#!/usr/bin/env python3
"""Update review.md EC-25 entry: replace the Status line (-> Fixed) and the Resolution line (-> Complete).

Approach:
  1. Find the `### [EC-25]` heading (anchored at start of line).
  2. Find the NEXT `### ` heading at start of line — that bounds the EC-25 entry slice.
  3. Within that slice, replace the entire `**Status:** ...` line and the entire
     `**Resolution:** ...` line (each line, in place, exactly once).
  4. Report line numbers before/after and a diff-style preview.

This script only touches the EC-25 entry; it does not modify any other entry.
"""

from __future__ import annotations

import re

REVIEW_PATH = "/home/z/my-project/voice-typer/review.md"

NEW_STATUS = (
    "**Status:** ✅ Fixed (PYTHON CATCH-ALLS) (verified ON LINUX sandbox 2026-08-25) — "
    "All 3 Python catch-all test files DELETED: tests/test_dictation_pipeline_review_fixes.py (619 LOC) "
    "split into NEW tests/app/test_notify_once_flags.py + tests/test_transcription_audio_stats.py + "
    "tests/test_dictation_pipeline_stage_timer.py + NEW tests/fixtures/dictation_pipeline_helpers.py; "
    "tests/test_low_findings_batch.py (467 LOC) split into tests/test_dead_code_stays_removed.py "
    "(appended TestLegacyConfigDirRemoved) + NEW tests/test_sensitive_env_redaction.py + "
    "NEW tests/test_docs_structure.py + NEW tests/test_electron_build.py — all ticket-ID class names "
    "E4-renamed (TestNewDead017→TestLegacyConfigDirRemoved etc.); tests/test_remaining_fixes.py "
    "(267 LOC) split into tests/test_transcription.py (WarmUp) + tests/test_qwen_engine.py (Batch) + "
    "tests/test_model_manager.py (LRU) + tests/test_platform_utils.py + tests/test_docs_structure.py + "
    "NEW tests/test_diagnostics_script.py. 69 tests preserved verbatim across the 3 splits. "
    "Zero import references remain. TS catch-alls (ux-components-behavior, electron-ipc-build-behavior, "
    "pages-improvements) are documented follow-up — out of scope this session."
)

NEW_RESOLUTION = (
    "**Resolution:** Complete for the 3 Python catch-alls — all DELETED, classes moved "
    "to matching domain files with E4-renamed classes; TS catch-alls deferred."
)


def char_to_line(text: str, char_offset: int) -> int:
    """Convert a character offset into `text` to a 1-based line number."""
    return text.count("\n", 0, char_offset) + 1


def locate_entry(text: str) -> tuple[int, int]:
    """Return (entry_start_char, entry_end_char) for the EC-25 entry in `text`.

    `entry_end_char` is the character offset of the start of the NEXT `### ` heading
    (or len(text) if there is no following heading).
    """
    m_heading = re.search(r"^### \[EC-25\].*$", text, re.MULTILINE)
    if not m_heading:
        raise SystemExit("ERROR: EC-25 heading not found in review.md")
    start = m_heading.start()
    after_heading = text[m_heading.end() :]
    m_next = re.search(r"^### ", after_heading, re.MULTILINE)
    end = m_heading.end() + m_next.start() if m_next else len(text)
    return start, end


def locate_line_in_entry(full_text: str, entry_start: int, entry_end: int, prefix: str) -> tuple[int, int]:
    """Return (global_line_number, char_offset_of_match_start) of the first line in
    the entry slice whose content begins with `prefix`."""
    entry = full_text[entry_start:entry_end]
    m = re.search(rf"^{re.escape(prefix)}.*$", entry, re.MULTILINE)
    if not m:
        raise SystemExit(f"ERROR: line starting with {prefix!r} not found in EC-25 entry")
    abs_start = entry_start + m.start()
    return char_to_line(full_text, abs_start), abs_start


def main() -> None:
    with open(REVIEW_PATH, encoding="utf-8") as f:
        content = f.read()

    entry_start, entry_end = locate_entry(content)
    heading_line_before = char_to_line(content, entry_start)
    status_line_before, _ = locate_line_in_entry(content, entry_start, entry_end, "**Status:**")
    resolution_line_before, _ = locate_line_in_entry(content, entry_start, entry_end, "**Resolution:**")

    # Snapshot old lines for diff preview.
    old_lines_all = content.split("\n")
    old_status_line = old_lines_all[status_line_before - 1]
    old_resolution_line = old_lines_all[resolution_line_before - 1]

    # Apply the two replacements within the entry slice only.
    entry = content[entry_start:entry_end]
    new_entry, status_n = re.subn(r"^\*\*Status:\*\*.*$", NEW_STATUS, entry, count=1, flags=re.MULTILINE)
    new_entry, resolution_n = re.subn(
        r"^\*\*Resolution:\*\*.*$", NEW_RESOLUTION, new_entry, count=1, flags=re.MULTILINE
    )
    if status_n != 1:
        raise SystemExit(f"ERROR: expected 1 Status match, got {status_n}")
    if resolution_n != 1:
        raise SystemExit(f"ERROR: expected 1 Resolution match, got {resolution_n}")

    new_content = content[:entry_start] + new_entry + content[entry_end:]

    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    # Re-derive line numbers after the edit.
    entry_start2, entry_end2 = locate_entry(new_content)
    heading_line_after = char_to_line(new_content, entry_start2)
    status_line_after, _ = locate_line_in_entry(new_content, entry_start2, entry_end2, "**Status:**")
    resolution_line_after, _ = locate_line_in_entry(new_content, entry_start2, entry_end2, "**Resolution:**")

    new_lines_all = new_content.split("\n")
    new_status_line = new_lines_all[status_line_after - 1]
    new_resolution_line = new_lines_all[resolution_line_after - 1]

    # Sanity: no other entry touched. The file should differ ONLY in the two replaced lines.
    if len(old_lines_all) != len(new_lines_all):
        raise SystemExit(
            f"ERROR: line count changed from {len(old_lines_all)} to {len(new_lines_all)} "
            "— script should only modify content of two lines, not add/remove lines."
        )
    diffs = [
        (i, old, new)
        for i, (old, new) in enumerate(zip(old_lines_all, new_lines_all, strict=False), start=1)
        if old != new
    ]
    if len(diffs) != 2:
        raise SystemExit(
            f"ERROR: expected exactly 2 changed lines, got {len(diffs)}: {[(i, o[:40], n[:40]) for i, o, n in diffs]}"
        )

    # Report.
    print("=== EC-25 STATUS UPDATE REPORT ===")
    print()
    print("BEFORE EDIT:")
    print(f"  EC-25 heading line  : {heading_line_before}")
    print(f"  Resolution line     : {resolution_line_before}")
    print(f"  Status line         : {status_line_before}")
    print()
    print("AFTER EDIT:")
    print(f"  EC-25 heading line  : {heading_line_after}")
    print(f"  Resolution line     : {resolution_line_after}")
    print(f"  Status line         : {status_line_after}")
    print()
    print("DIFF PREVIEW:")
    print(f"--- review.md  (Resolution line {resolution_line_before})")
    print(f"+++ review.md  (Resolution line {resolution_line_after})")
    print(f"- {old_resolution_line}")
    print(f"+ {new_resolution_line}")
    print()
    print(f"--- review.md  (Status line {status_line_before})")
    print(f"+++ review.md  (Status line {status_line_after})")
    print(f"- {old_status_line}")
    print(f"+ {new_status_line}")
    print()
    print(f"Total lines: {len(old_lines_all)} (unchanged). Exactly 2 lines modified.")
    print("No other review.md entry was touched.")


if __name__ == "__main__":
    main()
