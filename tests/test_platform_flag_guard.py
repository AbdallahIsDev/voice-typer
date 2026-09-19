"""Drift guard: production code must use ``platform_utils`` predicates."""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "voice_typer" / "server"

# Exact posix-style relative paths allowed to carry inline checks.
_ALLOWLISTED_FILES = frozenset(
    {
        "platform_utils.py",
        "server_platform/platform_flags.py",
    }
)
# Everything under these top-level directories is allowlisted.
_ALLOWLISTED_TOP_DIRS = frozenset({"crash_handler"})

# Matches ``sys.platform == ...`` / ``sys.platform != ...`` (including
_INLINE_CHECK_RE = re.compile(
    r"sys\.platform\s*[!=]="
    r"|sys\.platform\.startswith\("
    # Token stream is joined WITHOUT separators, so a membership check
    r"|sys\.platform(?:not)?in\("
)

# Token types scanned for inline checks. Comments (tokenize.COMMENT)
_SCANNED_TOKEN_TYPES = frozenset(
    {
        tokenize.NAME,
        tokenize.OP,
        tokenize.NUMBER,
    }
)


def _relative_posix(path: Path) -> str:
    return path.relative_to(SERVER_DIR).as_posix()


def _is_allowlisted(rel_posix: str) -> bool:
    if rel_posix in _ALLOWLISTED_FILES:
        return True
    return rel_posix.split("/")[0] in _ALLOWLISTED_TOP_DIRS


def _code_text_by_line(source: str) -> dict[int, str]:
    """Concatenate the code tokens of each line into one string."""
    lines: dict[int, str] = {}
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type not in _SCANNED_TOKEN_TYPES:
            continue
        start_row = tok.start[0]
        # Multi-line tokens cannot occur for NAME/OP/NUMBER; be safe
        lines[start_row] = lines.get(start_row, "") + tok.string
    return lines


def test_no_inline_sys_platform_comparisons_outside_allowlist() -> None:
    """Every ``sys.platform`` comparison under ``voice_typer/server``"""
    assert SERVER_DIR.is_dir(), f"server package not found at {SERVER_DIR}"

    violations: list[str] = []
    for py_file in sorted(SERVER_DIR.rglob("*.py")):
        rel = _relative_posix(py_file)
        if _is_allowlisted(rel):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, SyntaxError) as exc:  # pragma: no cover
            raise AssertionError(f"could not read {rel}: {exc}") from exc
        try:
            code_lines = _code_text_by_line(source)
        except tokenize.TokenError as exc:
            raise AssertionError(f"could not tokenize {rel}: {exc}") from exc
        for lineno in sorted(code_lines):
            if _INLINE_CHECK_RE.search(code_lines[lineno]):
                original_line = source.splitlines()[lineno - 1].strip()
                violations.append(f"voice_typer/server/{rel}:{lineno}: {original_line}")

    assert not violations, (
        "Inline sys.platform comparisons found outside the allowlist "
        "(platform_utils.py, crash_handler/**, "
        "server_platform/platform_flags.py). Use the centralized "
        "helpers instead:\n"
        "    from voice_typer.server.platform_utils import is_windows, is_macos, is_linux\n" + "\n".join(violations)
    )


def test_allowlist_files_still_exist() -> None:
    """The allowlist entries must point at real files, otherwise the"""
    assert (SERVER_DIR / "platform_utils.py").is_file()
    assert (SERVER_DIR / "crash_handler").is_dir()
    assert (SERVER_DIR / "server_platform" / "platform_flags.py").is_file()


def test_guard_detects_a_new_inline_comparison() -> None:
    """token-type regression)."""
    snippet = 'x = 1\nif sys.platform != "win32":\n    x = 2\n'
    code_lines = _code_text_by_line(snippet)
    hits = [n for n, text in code_lines.items() if _INLINE_CHECK_RE.search(text)]
    assert hits == [2], f"scanner failed to flag the synthetic violation: {hits}"

    # Membership forms are covered too.
    snippet_in = 'x = 1\nif sys.platform not in ("win32", "darwin"):\n    x = 2\n'
    in_lines = _code_text_by_line(snippet_in)
    in_hits = [n for n, text in in_lines.items() if _INLINE_CHECK_RE.search(text)]
    assert in_hits == [2], f"scanner failed to flag the membership-form violation: {in_hits}"

    # Comments / docstrings must NOT be flagged.
    benign = '"""\nsee sys.platform == notes\n"""\n# and sys.platform.startswith here\nx = 1\n'
    benign_lines = _code_text_by_line(benign)
    assert not any(_INLINE_CHECK_RE.search(t) for t in benign_lines.values())


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "--no-cov"])
