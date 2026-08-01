#!/usr/bin/env python3
"""Package changes.zip with all changed files from this session.

Reads `git status --porcelain` to get the exact list of modified/added/deleted
files, then builds a ZIP archive preserving the original directory structure.
Includes SUMMARY.md, worklog.md, review.md, archive/deleted_files.txt.
Excludes node_modules, .venv, __pycache__, build artifacts, .git, etc.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

REPO = Path("/home/z/my-project/voice-typer")
OUT = Path("/home/z/my-project/download/changes.zip")

# Get changed files from git
result = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=REPO,
    capture_output=True,
    text=True,
    check=True,
)

changed_files: list[str] = []
for line in result.stdout.splitlines():
    if not line.strip():
        continue
    # Format: " XY path" where XY is status code
    status = line[:2]
    path = line[3:].strip()
    # Handle renames (status "R ") — path is "old -> new"
    if " -> " in path:
        path = path.split(" -> ")[1]
    # Skip if path is a directory (ends with /)
    if path.endswith("/"):
        continue
    changed_files.append(path)

# Always include these metadata files
metadata_files = [
    "SUMMARY.md",
    "worklog.md",
    "review.md",
    "archive/deleted_files.txt",
]

# Excluded patterns
EXCLUDE_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".pyrefly_cache",
    ".ruff_cache",
    ".turbo",
    ".nx",
}
EXCLUDE_EXTS = {".log", ".pyc", ".pyo"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", ".env"}


def should_include(path: str) -> bool:
    parts = Path(path).parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return False
    if Path(path).name in EXCLUDE_FILES:
        return False
    return Path(path).suffix not in EXCLUDE_EXTS


# Build the final list
files_to_zip: set[str] = set()
for f in changed_files:
    if should_include(f):
        files_to_zip.add(f)
for f in metadata_files:
    files_to_zip.add(f)

# Also include new files in directories that are new (e.g. voice_typer/server/config/)
# git status shows "?? voice_typer/server/config/" for new directories — need to expand
new_dir_result = subprocess.run(
    ["git", "status", "--porcelain", "--untracked-files=all"],
    cwd=REPO,
    capture_output=True,
    text=True,
    check=True,
)
for line in new_dir_result.stdout.splitlines():
    if not line.startswith("??"):
        continue
    path = line[3:].strip()
    if should_include(path):
        files_to_zip.add(path)

# Now zip them
OUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for rel in sorted(files_to_zip):
        full = REPO / rel
        if full.is_file():
            zf.write(full, rel)
            print(f"  + {rel}")
        elif full.exists() and full.is_dir():
            # Include all files in the new directory
            for p in full.rglob("*"):
                if p.is_file() and should_include(str(p.relative_to(REPO))):
                    rel_p = str(p.relative_to(REPO))
                    zf.write(p, rel_p)
                    print(f"  + {rel_p}")
        else:
            print(f"  ! MISSING: {rel}")

print(f"\nArchive: {OUT}")
print(f"Size: {OUT.stat().st_size:,} bytes")
print(f"Files: {len(zf.namelist()) if 'zf' in dir() else '?'}")
