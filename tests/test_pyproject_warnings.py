"""XS-71: Python-side guard that no blanket ``ignore::ResourceWarning``
filter is added to ``pyproject.toml``.

Mirrors the TS assertion at
``voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/electron-ipc-build-behavior.test.tsx:1051``
(RW-1 rewrite of TestNoBlanketResourceWarningFilter). The TS test runs
only under ``vitest``; this Python guard runs under ``pytest`` so a
contributor who only runs the Python suite still catches the regression.

A blanket ``ignore::ResourceWarning`` filter would hide real file-handle
/ socket leaks in the 24/7 long-running tray process. Targeted filters
(e.g. ``ignore::ResourceWarning:sounddevice``) are still allowed — only
the bare ``"ignore::ResourceWarning"`` form is rejected.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def test_no_blanket_resource_warning_filter() -> None:
    """No line in ``pyproject.toml`` may start with ``"ignore::ResourceWarning"``.

    Reads the raw TOML text (not the parsed structure) so the guard fires
    even if a future contributor adds the filter outside the
    ``[tool.pytest.ini_options].filterwarnings`` array.
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith('"ignore::ResourceWarning"'):
            msg = (
                f"Blanket 'ignore::ResourceWarning' filter found at "
                f"pyproject.toml:{lineno}: {stripped}"
            )
            raise AssertionError(msg)
