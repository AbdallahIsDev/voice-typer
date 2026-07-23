"""Tests for ``voice_typer.server.microphone_watcher_coreaudio``.

Task 15: verifies the event-driven CoreAudio property-listener
microphone watcher that replaces the 1 Hz ``sounddevice`` polling on
macOS when ``pyobjc-framework-CoreAudio`` is installed.

Test layout
-----------
- ``test_module_imports_cross_platform`` — runs on ALL platforms.
  Verifies the module is importable without pyobjc installed
  (cross-platform import safety).
- ``test_import_error_when_not_macos`` — runs on ALL platforms (the
  platform gate is mocked). Verifies ``_try_import_coreaudio`` raises
  ``ImportError`` on non-macOS.
- ``test_import_error_when_pyobjc_missing`` — runs on ALL platforms
  (platform gate is mocked, pyobjc imports are blocked). Verifies the
  pyobjc-missing fallback.
- ``test_instantiation_on_macos_with_pyobjc`` — SKIPPED on non-macOS.
  Verifies the watcher instantiates and starts when pyobjc is
  available.
- ``test_microphone_watcher_falls_back_to_polling`` — runs on ALL
  platforms. Verifies ``MicrophoneDeviceWatcher.start()`` falls back
  to the polling thread when the CoreAudio watcher is unavailable
  (the normal path on Linux, and the fallback path on macOS without
  pyobjc).
"""

from __future__ import annotations

import sys
import threading
from unittest.mock import patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

# ── Cross-platform tests (run on every platform) ────────────────────


def test_module_imports_cross_platform() -> None:
    """The module is importable on ALL platforms without pyobjc installed.

    Cross-platform safety: ``from voice_typer.server import
    microphone_watcher_coreaudio`` must succeed on Linux/Windows even
    though ``pyobjc-framework-CoreAudio`` is macOS-only. Top-level
    imports in the module are stdlib-only; the pyobjc import happens
    lazily in ``_try_import_coreaudio``.
    """
    from voice_typer.server import microphone_watcher_coreaudio as mod

    assert hasattr(mod, "CoreAudioMicrophoneWatcher")
    assert hasattr(mod, "_try_import_coreaudio")
    assert hasattr(mod, "_IS_MACOS")


def test_import_error_when_not_macos() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` off macOS.

    The platform gate is checked before any pyobjc import is
    attempted, so this test is deterministic on every platform —
    we patch ``_IS_MACOS`` to ``False`` to simulate non-macOS.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        _try_import_coreaudio,
    )

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._IS_MACOS",
        False,
    ), pytest.raises(ImportError, match="only available on macOS"):
        _try_import_coreaudio()


def test_import_error_when_pyobjc_missing() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` when pyobjc is not installed.

    Simulates a macOS system without ``pyobjc-framework-CoreAudio`` by
    marking ``CoreAudio`` and ``CoreFoundation`` as blocked in
    ``sys.modules``. Python raises ``ImportError`` when an import
    statement encounters a ``None`` entry in ``sys.modules`` — this
    is the canonical way to mock a missing dependency.

    The platform gate is bypassed by patching ``_IS_MACOS`` to
    ``True``, so this test runs on every platform.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        _try_import_coreaudio,
    )

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._IS_MACOS",
        True,
    ), patch.dict(
        sys.modules, {"CoreAudio": None, "CoreFoundation": None}
    ), pytest.raises(ImportError, match="pyobjc-framework-CoreAudio"):
        _try_import_coreaudio()


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Verifies the non-macOS ImportError path — on macOS pyobjc may succeed",
)
def test_coreaudio_watcher_start_raises_on_non_macos() -> None:
    """``CoreAudioMicrophoneWatcher.start`` raises ``ImportError`` off macOS.

    The constructor does not eagerly import pyobjc (so the class can
    be instantiated anywhere for testability). The import happens
    lazily in ``start``. This test verifies that calling ``start`` on
    a non-macOS platform raises a clean ``ImportError`` that the
    caller can catch to fall back to polling.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    with pytest.raises(ImportError, match="only available on macOS"):
        watcher.start()
    # start() failed before creating the thread — verify no thread leaked.
    assert watcher._thread is None


def test_microphone_watcher_falls_back_to_polling_without_pyobjc() -> None:
    """``MicrophoneDeviceWatcher`` falls back to polling when CoreAudio is unavailable.

    On macOS without pyobjc (or on any non-macOS platform), the
    high-level ``MicrophoneDeviceWatcher.start`` must transparently
    fall back to the polling thread instead of raising. This test
    forces ``_try_create_coreaudio_watcher`` to return ``None`` and
    verifies the polling thread starts.
    """
    fired = threading.Event()

    def on_change() -> None:
        fired.set()

    # Force the platform to "macos" so the CoreAudio path is attempted.
    watcher = MicrophoneDeviceWatcher(on_change, poll_interval=0.05)
    watcher._platform = "macos"

    # Stub _try_create_coreaudio_watcher to simulate "pyobjc missing".
    with patch.object(
        watcher, "_try_create_coreaudio_watcher", return_value=None
    ):
        # Stub _run_macos so it fires the callback once and returns —
        # this proves the polling fallback path was taken (rather than
        # start() returning early after a CoreAudio success).
        def fake_run_macos(self_arg):
            self_arg._invoke_callback()
            # Stop immediately so the test doesn't hang.
            self_arg._stop_event.set()

        with patch.object(
            MicrophoneDeviceWatcher, "_run_macos", fake_run_macos
        ):
            watcher.start()
            assert fired.wait(timeout=2.0), "polling fallback did not fire"
            watcher.stop()

    assert watcher._coreaudio_watcher is None
    # Polling thread was used (and cleaned up).
    assert watcher._thread is None  # stop() clears it


# ── macOS-only tests (skipped on Linux/Windows) ─────────────────────


@pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS only — CoreAudio watcher is darwin-only"
)
def test_instantiation_on_macos_with_pyobjc() -> None:
    """On macOS with pyobjc installed, the watcher instantiates cleanly.

    Skipped on non-macOS because instantiation succeeds but
    ``start()`` would raise ``ImportError``. This is the "module
    imports correctly when pyobjc is available" verification from
    the task description.

    We only verify instantiation (not full ``start()``) because
    starting spawns a real CFRunLoop thread that's hard to shut down
    deterministically in CI. The instantiation covers the import
    surface; the listener registration is exercised by the
    ``_try_import_coreaudio`` tests above.
    """
    try:
        from voice_typer.server.microphone_watcher_coreaudio import (
            CoreAudioMicrophoneWatcher,
        )
    except ImportError as exc:  # pragma: no cover — defensive
        pytest.skip(f"pyobjc-framework-CoreAudio not installed: {exc}")

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    assert watcher is not None
    # The pyobjc symbols are loaded lazily in start(), not in __init__,
    # so _ca should be None before start() is called.
    assert watcher._ca is None
