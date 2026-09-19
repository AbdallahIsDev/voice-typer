#!/usr/bin/env python3
"""Measure comment-line ratios by top directory. Comment-only metrics tool."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that count as "prod code" for the ratio target.
TOP_DIRS = [
    ("voice_typer/server", ["*.py"]),
    ("voice_typer/client/src", ["*.ts", "*.tsx", "*.js", "*.jsx"]),
    ("src-tauri/src", ["*.rs"]),
    ("scripts", ["*.py", "*.ps1", "*.mjs", "*.js", "*.ts"]),
    (".github/workflows", ["*.yml", "*.yaml"]),
    ("tests", ["*.py"]),
]

SKIP_DIR_NAMES = {
    "__pycache__",
    ".venv",
    ".venv311",
    "node_modules",
    "target",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    ".git",
    "dist",
    "build",
}

# Rough language-aware comment detectors (line-based; good enough for ratios).
PY_COMMENT = re.compile(r"^\s*#")
PY_DOCSTRING = re.compile(r'^\s*("""|\'\'\')')
RS_COMMENT = re.compile(r"^\s*(//|/\*|\*)")
TS_COMMENT = re.compile(r"^\s*(//|/\*|\*|\{/\*)")
YAML_COMMENT = re.compile(r"^\s*#")
PS_COMMENT = re.compile(r"^\s*(#|<#)")


# Lines that are pure code or blank.
def is_blank(line: str) -> bool:
    return not line.strip()


def classify_line(line: str, path: Path) -> str:
    """Return 'comment', 'code', 'blank', or 'mixed'."""
    if is_blank(line):
        return "blank"
    suf = path.suffix.lower()
    stripped = line.lstrip()
    if suf == ".py":
        if stripped.startswith(("#", '"""', "'''")):
            return "comment"
        return "code"
    if suf == ".rs":
        if stripped.startswith(("//", "/*", "*/", "*")):
            return "comment"
        return "code"
    if suf in {".ts", ".tsx", ".js", ".jsx"}:
        if stripped.startswith(("//", "/*", "*/", "*", "{/*")):
            return "comment"
        return "code"
    if suf in {".yml", ".yaml"}:
        if stripped.startswith("#"):
            return "comment"
        return "code"
    if suf == ".ps1":
        if stripped.startswith(("#", "<#")):
            return "comment"
        return "code"
    return "code"


def iter_files(base: Path, patterns: list[str]) -> list[Path]:
    if not base.exists():
        return []
    files: list[Path] = []
    for pat in patterns:
        for p in base.rglob(pat):
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.is_file():
                files.append(p)
    return sorted(set(files))


def measure_tree(base: Path, patterns: list[str]) -> dict:
    total = code = comment = blank = 0
    per_file: dict[str, dict[str, int]] = {}
    for p in iter_files(base, patterns):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        f_total = f_code = f_comment = f_blank = 0
        in_block = False
        for raw in text.splitlines():
            kind = classify_line(raw, p)
            # Crude Python docstring block handling
            if p.suffix.lower() == ".py":
                s = raw.lstrip()
                if not in_block and s.startswith(('"""', "'''")):
                    # one-line docstring
                    q = s[:3]
                    if len(s) >= 6 and s.endswith(q) and len(s) > 3:
                        kind = "comment"
                    else:
                        in_block = True
                        kind = "comment"
                elif in_block:
                    kind = "comment"
                    if '"""' in s or "'''" in s:
                        in_block = False
            f_total += 1
            if kind == "comment":
                f_comment += 1
            elif kind == "blank":
                f_blank += 1
            else:
                f_code += 1
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        per_file[rel] = {
            "total": f_total,
            "code": f_code,
            "comment": f_comment,
            "blank": f_blank,
        }
        total += f_total
        code += f_code
        comment += f_comment
        blank += f_blank
    ratio = (comment / total * 100) if total else 0.0
    # Ratio of comment to (code+comment) is often more meaningful
    code_comment = code + comment
    ratio_cc = (comment / code_comment * 100) if code_comment else 0.0
    return {
        "files": len(per_file),
        "total": total,
        "code": code,
        "comment": comment,
        "blank": blank,
        "ratio_of_total": ratio,
        "ratio_of_code_plus_comment": ratio_cc,
        "per_file": per_file,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25, help="Show top-N densest files")
    ap.add_argument("--min-comments", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report: dict[str, dict] = {}
    for label, pats in TOP_DIRS:
        base = ROOT / label
        report[label] = measure_tree(base, pats)

    if args.json:
        # strip per-file for compact json unless needed
        slim = {k: {kk: vv for kk, vv in v.items() if kk != "per_file"} for k, v in report.items()}
        import json

        print(json.dumps(slim, indent=2))
        return

    print(f"{'directory':<28} {'files':>6} {'lines':>8} {'code':>8} {'comm':>8} {'%tot':>7} {'%c/(c+c)':>9}")
    print("-" * 80)
    for label, v in report.items():
        print(
            f"{label:<28} {v['files']:>6} {v['total']:>8} {v['code']:>8} "
            f"{v['comment']:>8} {v['ratio_of_total']:>6.1f}% {v['ratio_of_code_plus_comment']:>8.1f}%"
        )

    print("\nDensest files (comment lines >= min, by comment count):")
    rows = []
    for label, v in report.items():
        for rel, m in v["per_file"].items():
            if m["comment"] >= args.min_comments:
                rows.append((m["comment"], m["total"], rel, label))
    rows.sort(reverse=True)
    for c, t, rel, _lab in rows[: args.top]:
        print(f"  {c:>5}/{t:<5}  {rel}")


if __name__ == "__main__":
    main()
