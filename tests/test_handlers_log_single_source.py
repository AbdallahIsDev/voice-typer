"""YJ-46: assert ``handlers/_log.py`` is the single source of the IPC handler logger."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Resolve the handlers directory relative to this test file so the test
_HANDLERS_DIR = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "handlers"

# Match ``logging.getLogger(`` anywhere in source. We deliberately do
_GETLOGGER_RE = re.compile(r"\blogging\.getLogger\s*\(")


def _discover_handler_files() -> list[tuple[str, Path]]:
    """Return ``(module_name, path)`` for every ``*.py`` under handlers/."""
    if not _HANDLERS_DIR.is_dir():
        pytest.fail(f"handlers dir not found: {_HANDLERS_DIR}")
    out: list[tuple[str, Path]] = []
    for p in sorted(_HANDLERS_DIR.glob("*.py")):
        out.append((p.name, p))
    return out


def test_handlers_dir_exists_and_has_files() -> None:
    """Sanity guard: if the handlers dir moves, fail loudly with context."""
    files = _discover_handler_files()
    assert files, f"expected handler files under {_HANDLERS_DIR}, found none"
    # Sanity: we know the canonical single-source module exists.
    names = [name for name, _ in files]
    assert "_log.py" in names, "_log.py must exist as the single-source logger"
    assert "_base.py" in names, "_base.py must exist"


@pytest.mark.parametrize(
    "module_name, path",
    _discover_handler_files(),
    ids=[name for name, _ in _discover_handler_files()],
)
def test_no_inline_get_logger(module_name: str, path: Path) -> None:
    """No handler module (except ``_log.py``) may call ``logging.getLogger(``."""
    if module_name == "_log.py":
        pytest.skip("_log.py is the single-source module, getLogger lives here")

    source = path.read_text(encoding="utf-8")
    matches = _GETLOGGER_RE.findall(source)
    assert not matches, (
        f"{module_name} must import `log` from `voice_typer.server.handlers._log` "
        f"instead of calling `logging.getLogger(...)` inline. "
        f"Found {len(matches)} inline calls."
    )


def test_no_handler_module_uses_import_logging_only_for_logger() -> None:
    """Bonus YJ-46 invariant: a handler that still imports ``logging``"""
    for module_name, path in _discover_handler_files():
        if module_name == "_log.py":
            continue
        source = path.read_text(encoding="utf-8")
        if not re.search(r"^\s*import\s+logging\b", source, re.MULTILINE):
            continue
        # ``logging`` is imported, make sure at least one non-getLogger
        non_getlogger_usages = re.findall(r"\blogging\.(?!getLogger\b)\w+", source)
        assert non_getlogger_usages, (
            f"{module_name} imports `logging` but does not use it for anything "
            f"other than getLogger (which is banned). Remove the unused import."
        )
