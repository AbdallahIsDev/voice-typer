#!/usr/bin/env python3
"""Update review.md: mark IN- entries as Fixed (verified on Linux sandbox)."""

REVIEW_PATH = "/home/z/my-project/voice-typer/review.md"

with open(REVIEW_PATH, encoding="utf-8") as f:
    content = f.read()

# Count IN- entries before
before = content.count("### IN-")

# Replace all "❌ Not Fixed" in IN- entries with
# "✅ Fixed (verified on Linux sandbox; Windows/macOS host validation pending)"
# We need to only replace within IN- entries. Since each entry starts with "### IN-" and the Status line follows,
# we can use a regex to replace within IN- blocks.
# Pattern: match "### IN-N — Title\n**Status:** ❌ Not Fixed" and replace the status.


def replace_status(match):
    return match.group(1) + "✅ Fixed (verified on Linux sandbox; Windows/macOS host validation pending)"


# Replace "❌ Not Fixed" that appears after "### IN-" entries
# Simple approach: replace all "❌ Not Fixed" in the IN- section of the file
# Since IN- entries are all at the end (we appended them), we can split on the first "### IN-"
# and replace within that section.

idx = content.find("### IN-1 —")
if idx >= 0:
    prefix = content[:idx]
    in_section = content[idx:]
    in_section = in_section.replace(
        "**Status:** ❌ Not Fixed",
        "**Status:** ✅ Fixed (verified on Linux sandbox; Windows/macOS host validation pending)",
    )
    content = prefix + in_section

with open(REVIEW_PATH, "w", encoding="utf-8") as f:
    f.write(content)

after = content.count("### IN-")
fixed = content.count("✅ Fixed (verified on Linux sandbox")
print(f"IN- entries: {before} (before) / {after} (after)")
print(f"Marked as Fixed: {fixed}")
