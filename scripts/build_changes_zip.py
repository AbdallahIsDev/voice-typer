#!/usr/bin/env python3
"""Build changes.zip per §17 of the directive."""

import os
import subprocess
import zipfile
from pathlib import Path

REPO = Path("/home/z/my-project/voice-typer")
os.chdir(REPO)

# 1. Tracked modified+deleted files (vs HEAD)
tracked = subprocess.run(["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True).stdout.split()
# 2. Untracked new files (?? lines from git status --porcelain)
porcelain = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
untracked = [ln[3:] for ln in porcelain.splitlines() if ln.startswith("??")]
# Strip the trailing "/" for directory entries (zip will recurse separately; but for untracked dirs we need their files)
expanded_untracked = []
for p in untracked:
    if p.endswith("/"):
        # directory — expand to its tracked-as-untracked files
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True
        ).stdout
        for ln in result.splitlines():
            if ln.startswith("??"):
                fp = ln[3:]
                if fp.startswith(p) or os.path.dirname(fp) == p.rstrip("/"):
                    expanded_untracked.append(fp)
    else:
        expanded_untracked.append(p)
# Combine + dedupe
files = list(dict.fromkeys(tracked + expanded_untracked))

# 3. REQUIRED files (even if "unchanged" — they ARE changed this session, but ensure present)
REQUIRED = ["SUMMARY.md", "worklog.md", "review.md"]
for r in REQUIRED:
    if r not in files:
        files.append(r)

# 4. Exclude list — must NOT contain these even if they appear
EXCLUDE_PATTERNS = [
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "dist/",
    "build/",
    "out/",
    "target/",
    ".next/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".git/",
    "sub-worklog-",
    "AGENTS.md",
    ".env",
    "secrets",
    "keys",
    "src-tauri/bin/",  # generated stubs (gitignored but untracked-new might leak)
    "src-tauri/resources/native/",  # generated stubs
    "src-tauri/resources/prewarm",  # generated stubs
    ".husky/",
    ".vscode/",
    ".idea/",
]


def excluded(p):
    return any(pat in p for pat in EXCLUDE_PATTERNS)


final_files = [f for f in files if not excluded(f)]

# 5. Validate every file exists (for deleted files, we skip — they are recorded
# in archive/deleted_files.txt but not in the zip)
missing = [f for f in final_files if not (REPO / f).exists() and not (REPO / f).is_symlink()]
# Deleted files will appear in git diff --name-only HEAD as deleted; exclude them
# from the zip (they're listed in archive/deleted_files.txt)
deleted = [f for f in final_files if not (REPO / f).exists()]
final_files = [f for f in final_files if (REPO / f).exists() or (REPO / f).is_symlink()]
print(f"Tracked modified: {len(tracked)}")
print(f"Untracked new: {len(expanded_untracked)}")
print(f"Deleted (in archive/deleted_files.txt, not zipped): {len(deleted)}: {deleted}")
print(f"Missing (should not happen): {missing}")
print(f"Final zip contents: {len(final_files)} files")

# 6. Build the zip preserving directory structure
ZIP_PATH = REPO / "changes.zip"
if ZIP_PATH.exists():
    ZIP_PATH.unlink()
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(final_files):
        full = REPO / f
        if full.is_file() or full.is_symlink():
            zf.write(full, arcname=f)
            # print(f"  + {f}")
        else:
            print(f"  SKIP (not a file): {f}")
print(f"Wrote {ZIP_PATH}")
print(f"Size: {ZIP_PATH.stat().st_size} bytes")

# 7. Self-consistency check: every module any changed file imports should be present (best-effort)
print("\n=== Zip self-consistency check ===")
with zipfile.ZipFile(ZIP_PATH, "r") as zf:
    names = zf.namelist()
print(f"Files in zip: {len(names)}")
required_dirs = sorted({os.path.dirname(n) for n in names if "/" in n})
print(f"Distinct directories: {len(required_dirs)}")
# Quick sanity: no node_modules, no .venv, no target/, no sub-worklog
bad = [n for n in names if any(p in n for p in EXCLUDE_PATTERNS)]
print(f"Forbidden entries: {len(bad)} (should be 0)")
if bad:
    print(f"  BAD: {bad[:5]}")
# Required files present?
for r in REQUIRED:
    print(f"  {r}: {'present' if r in names else 'MISSING'}")
