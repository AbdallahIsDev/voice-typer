"""CR-2 regression guard — verify ``_delayed_restore`` 4-arg signature.

Finding CR-2 (Critical): ``voice_typer/server/clipboard.py`` spawned
a daemon thread with 4 positional args but the target method
``_delayed_restore`` only accepted 3 (``snapshot, pasted_text,
delay``). Every paste call raised ``TypeError`` which was swallowed
by a broad ``except Exception``, silently leaving the user's
clipboard clobbered with the transcribed text.

Fix-B updates the signature to accept a 4th ``_pending_entry``
argument (the tuple appended to the module-level
``_pending_restores`` list at spawn time). On completion the method
removes its entry so the atexit handler doesn't double-restore.

This test asserts:
1. ``_delayed_restore`` accepts 4 args without ``TypeError``.
2. After a normal completion, the ``_pending_entry`` is removed
   from ``_pending_restores``.
3. After a failure (e.g. snapshot.restore raises), the entry is
   STILL removed (the ``finally`` block must run).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


def _make_manager() -> ClipboardManager:
    """Build a ClipboardManager with all heavy deps mocked out.

    Imports are local so the autouse mock fixture has installed its
    ``sounddevice``/``pynput``/``pyperclip`` mocks first.
    """
    from voice_typer.server.clipboard import ClipboardManager

    return ClipboardManager()


def test_delayed_restore_signature_accepts_four_args() -> None:
    """The signature must accept a 4th ``_pending_entry`` parameter."""
    from voice_typer.server import clipboard as cb

    sig = inspect.signature(cb.ClipboardManager._delayed_restore)
    params = list(sig.parameters.values())
    # self, snapshot, pasted_text, delay, _pending_entry
    assert len(params) >= 5, (
        f"_delayed_restore must accept 4 args + self (5 params), got {len(params)}: {[p.name for p in params]}"
    )
    assert params[4].name == "_pending_entry", (
        f"4th positional arg must be named '_pending_entry', got {params[4].name!r}"
    )


def test_delayed_restore_removes_pending_entry_on_success() -> None:
    """On normal completion, the entry is removed from _pending_restores."""
    from voice_typer.server import clipboard as cb

    mgr = _make_manager()
    snapshot = MagicMock()
    snapshot.restore = MagicMock()
    # Speed up the test: no sleep.
    delay = 0.0
    entry = (mgr, snapshot, "pasted", delay)

    # Pre-populate the registry so we can observe removal.
    cb._pending_restores.append(entry)
    before = list(cb._pending_restores)
    assert entry in before

    # Patch the clipboard-read helper so the "current == pasted_text"
    # defensive check passes.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cb, "_paste_from_clipboard", lambda: "pasted")
        mgr._delayed_restore(snapshot, "pasted", delay, entry)

    assert entry not in cb._pending_restores, (
        "Expected _pending_entry to be removed from _pending_restores after successful completion."
    )
    snapshot.restore.assert_called_once()


def test_delayed_restore_removes_pending_entry_on_failure() -> None:
    """Even if snapshot.restore raises, the entry must still be removed
    (finally block must run)."""
    from voice_typer.server import clipboard as cb

    mgr = _make_manager()
    snapshot = MagicMock()
    snapshot.restore = MagicMock(side_effect=RuntimeError("boom"))
    delay = 0.0
    entry = (mgr, snapshot, "pasted", delay)

    cb._pending_restores.append(entry)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cb, "_paste_from_clipboard", lambda: "pasted")
        # Should NOT raise (errors are logged, not propagated).
        mgr._delayed_restore(snapshot, "pasted", delay, entry)

    assert entry not in cb._pending_restores, "Expected _pending_entry to be removed even on failure (finally block)."


def test_delayed_restore_without_pending_entry_still_works() -> None:
    """Calling without a _pending_entry (legacy 3-arg call) must not raise.

    The 4th arg has a default of None so older callers that haven't
    been updated keep working.
    """
    from voice_typer.server import clipboard as cb

    mgr = _make_manager()
    snapshot = MagicMock()
    snapshot.restore = MagicMock()
    delay = 0.0

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cb, "_paste_from_clipboard", lambda: "pasted")
        # 3-arg call — should not raise.
        mgr._delayed_restore(snapshot, "pasted", delay)

    snapshot.restore.assert_called_once()


def test_delayed_restore_skips_when_entry_already_taken_by_atexit() -> None:
    """CR-84: if the atexit handler has already cleared
    _pending_restores (taken ownership), the daemon thread must
    short-circuit BEFORE calling snapshot.restore() — the platform
    clipboard APIs are not thread-safe."""
    from voice_typer.server import clipboard as cb

    mgr = _make_manager()
    snapshot = MagicMock()
    snapshot.restore = MagicMock()
    delay = 0.0
    entry = (mgr, snapshot, "pasted", delay)

    # Do NOT register the entry — simulate atexit having already taken it.
    assert entry not in cb._pending_restores

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cb, "_paste_from_clipboard", lambda: "pasted")
        mgr._delayed_restore(snapshot, "pasted", delay, entry)

    # Must NOT have called restore — atexit will do that synchronously.
    snapshot.restore.assert_not_called()


def test_spawn_site_passes_four_args() -> None:
    """The paste() method must spawn the thread with 4 positional args
    (including the _pending_entry). This catches a regression where
    the spawn site is reverted to 3 args.
    """
    import re
    from pathlib import Path

    from voice_typer.server import clipboard as cb

    src = Path(cb.__file__).read_text(encoding="utf-8")
    # Find the spawn site.
    m = re.search(
        r"threading\.Thread\(\s*target=self\._delayed_restore,\s*args=\(([^)]+)\)",
        src,
        re.DOTALL,
    )
    assert m is not None, "Could not find _delayed_restore thread spawn site"
    args_blob = m.group(1)
    # Count identifiers / placeholders in the args tuple.
    args = [a.strip() for a in args_blob.split(",") if a.strip()]
    assert len(args) == 4, (
        f"Spawn site must pass 4 args (snapshot, expected, delay, _pending_entry); got {len(args)}: {args}"
    )
    assert "_pending_entry" in args, f"Spawn site must include _pending_entry as 4th arg; got: {args}"
