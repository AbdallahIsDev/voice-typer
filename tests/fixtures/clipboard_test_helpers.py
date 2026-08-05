"""Shared clipboard test helpers for the ``voice_typer.server.clipboard``
package.

This module owns the SINGLE canonical ``_make_cm`` / ``_make_snapshot``
helpers used by every test file that exercises
:class:`voice_typer.server.clipboard.manager.ClipboardManager` /
:class:`voice_typer.server.clipboard_snapshot.ClipboardSnapshot`.

Before this module existed, at least 5 inline copies of ``_make_cm``
were sprinkled across the test tree:

- ``tests/test_clipboard_paste_restore.py`` (signature: keyword-only
  ``paste_enabled``, ``save_restore``, ``restore_delay_ms``; uses
  ``ClipboardManager.__new__`` to skip ``__init__``)
- ``tests/test_clipboard.py`` (signature: ``**kwargs`` passed straight
  to ``ClipboardManager(**kwargs)``; then mutates ``_last_paste_time``
  and ``_keyboard``)
- ``tests/test_clipboard_borrow_restore.py`` (mirrors paste-restore)
- ``tests/test_clipboard_restore_fixes.py``
- ``tests/test_clipboard_restore_race.py``

The copies had drifted: some used ``ClipboardManager.__new__`` (bypass
the constructor so no pynput import is paid), others called the real
constructor; some pre-populated ``_last_paste_time = -999.0`` (rate
limit bypass), others used ``0.0``; some set
``_clipboard_seq`` / ``_last_copied_text`` / ``_restore_delay_ms``,
others left them at the constructor default.

Centralising the factory here means future additions to
``ClipboardManager`` (e.g. a new ``_pending_restores`` field that tests
need to reset between cases) only need to update ONE place — this
module — and every clipboard test picks up the fix automatically.

The factory mirrors the most-used signature (keyword-only
``paste_enabled`` / ``save_restore`` / ``restore_delay_ms``, plus a
``**overrides`` escape hatch for per-test attribute tweaks). It uses
``ClipboardManager.__new__`` so importing this module doesn't
transitively import pynput at module top (the conftest's autouse
``mock_heavy_imports`` fixture installs a pynput MagicMock before any
test runs, but keeping the import lazy here means collection-time
imports of this fixtures module won't pull pynput into
``sys.modules`` before the fixture runs).

The migration target is documented as Remaining Work: this
module ONLY provides the factories — call sites still use their inline
``_make_cm`` / ``_make_snapshot`` until a follow-up task migrates them.
"""

from __future__ import annotations

import time
from typing import Any


def make_clipboard_manager(
    *,
    paste_enabled: bool = True,
    save_restore: bool = True,
    restore_delay_ms: int = 150,
    **overrides: Any,
) -> Any:
    """Build a ``ClipboardManager`` with mocked keyboard and cached flags set.

    Mirrors the ``_make_cm`` helper in
    ``tests/test_clipboard_paste_restore.py`` (which in turn mirrors
    ``tests/test_clipboard_borrow_restore.py``). Uses
    ``ClipboardManager.__new__`` to bypass the constructor so we don't
    pay the pynput-import cost or probe the real clipboard at
    construction time.

    Pre-populates the instance attributes that the production
    ``paste()`` / ``copy()`` paths read without re-deriving them from
    config:

    - ``paste_enabled`` — gates the paste path (default ``True``).
    - ``_keyboard`` — ``MagicMock()`` so keystroke primitives record
      calls without touching real input.
    - ``_last_paste_time = 0.0`` — well before any rate-limit window
      (tests that need to assert rate-limit rejection override it with
      ``time.monotonic()`` after construction).
    - ``_clipboard_seq = 0`` — fresh clipboard sequence counter.
    - ``_last_copied_text = ""`` — no prior copy in flight.
    - ``_clipboard_save_restore_enabled = save_restore`` — gates the
      snapshot/restore path (default ``True``).
    - ``_restore_delay_ms = restore_delay_ms`` — delay before the
      daemon thread runs ``_delayed_restore`` (default 150ms; tests
      that need fast feedback pass ``restore_delay_ms=10``).

    Parameters
    ----------
    paste_enabled : bool, optional
        Value for ``self.paste_enabled``. Default ``True``.
    save_restore : bool, optional
        Value for ``self._clipboard_save_restore_enabled``. Default
        ``True``.
    restore_delay_ms : int, optional
        Value for ``self._restore_delay_ms``. Default 150 (matches the
        production default). Use ``10`` for fast tests.
    **overrides : Any
        Additional attribute assignments on the returned instance.
        Convenient for scalar attributes like ``_last_paste_time``.
        Nested overrides (e.g. ``_keyboard.press``) should be set by
        mutating the returned instance directly.

    Returns
    -------
    voice_typer.server.clipboard.manager.ClipboardManager
        A constructed (via ``__new__``) ClipboardManager instance.
    """
    from voice_typer.server.clipboard.manager import ClipboardManager

    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = paste_enabled
    cm._keyboard = _make_magic_mock()
    cm._last_paste_time = 0.0  # not rate-limited
    cm._clipboard_seq = 0
    cm._last_copied_text = ""
    cm._clipboard_save_restore_enabled = save_restore
    cm._restore_delay_ms = restore_delay_ms
    for key, value in overrides.items():
        setattr(cm, key, value)
    return cm


def make_clipboard_snapshot(
    *,
    platform: str = "linux-x11",
    mime: str = "text/plain",
    content: bytes | None = None,
    captured_at: float | None = None,
) -> Any:
    """Build a ``ClipboardSnapshot`` for a single text format.

    Mirrors the ``_make_snapshot`` helper in
    ``tests/test_clipboard_paste_restore.py``. Returns a
    :class:`voice_typer.server.clipboard_snapshot.ClipboardSnapshot`
    with a single ``(mime, content_bytes)`` entry in ``items`` — the
    shape the Linux X11 / Wayland restore path expects. Tests that
    need a multi-format Windows snapshot should construct the
    dataclass directly (this helper is Linux-text-shaped only, matching
    the canonical call site's default).

    Parameters
    ----------
    platform : str, optional
        Platform tag carried by the snapshot. Default ``"linux-x11"``
        (matches the canonical call site). One of ``"windows"``,
        ``"macos"``, ``"linux-x11"``, ``"linux-wayland"``.
    mime : str, optional
        MIME type of the single text entry. Default ``"text/plain"``.
    content : bytes, optional
        Content bytes for the single text entry. Default
        ``b"prior clipboard content"`` (matches the canonical call
        site).
    captured_at : float, optional
        ``time.monotonic()`` timestamp stored on the snapshot. Default
        ``time.monotonic()`` (call-time). Tests that need a fixed
        timestamp pass it explicitly.

    Returns
    -------
    voice_typer.server.clipboard_snapshot.ClipboardSnapshot
        A constructed snapshot dataclass instance.
    """
    from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

    if content is None:
        content = b"prior clipboard content"
    if captured_at is None:
        captured_at = time.monotonic()
    return ClipboardSnapshot(
        platform=platform,
        items=[(mime, content)],
        captured_at=captured_at,
    )


def _make_magic_mock() -> Any:
    """Local import of ``MagicMock`` so module top-level imports stay cheap.

    ``ClipboardManager`` doesn't need MagicMock at module-import time
    — only when a test calls :func:`make_clipboard_manager`. Importing
    lazily here keeps ``tests.fixtures.clipboard_test_helpers``
    importable in environments where ``unittest.mock`` is slow to
    import (e.g. when collection is running on a stripped-down CI
    image that defers C-extension imports).
    """
    from unittest.mock import MagicMock

    return MagicMock()


__all__ = [
    "make_clipboard_manager",
    "make_clipboard_snapshot",
]
