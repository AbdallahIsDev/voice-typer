"""Shared app-level test helpers for VoiceTyperApp and audio test data.

XS-42: this module exports two factory functions that were previously
copy-pasted across at least 6 test files:

- :func:`make_voice_typer_app` — builds a real ``VoiceTyperApp``
  instance with hardware/GUI dependencies mocked out. Mirrors the
  ``app`` fixture in ``tests/app/conftest.py`` and the ``_make_app``
  helpers in ``tests/test_api_doc_accuracy.py`` /
  ``tests/test_config_editor_lock.py`` / ``tests/test_dictation_pipeline_review_fixes.py``.
- :func:`make_sine` — generates a 1-D float32 numpy sine wave. Mirrors
  the ``make_sine`` / ``_make_sine`` helpers in
  ``tests/test_audio_processor.py``, ``tests/test_recorder_double_resample.py``,
  and ``tests/test_recording_audio_processor.py``.

Usage
-----

::

    from tests.fixtures.app_helpers import make_voice_typer_app, make_sine

    def test_app_starts(tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        assert app is not None

    def test_audio_pipeline():
        tone = make_sine(440, 1.0, sr=16000, amp=0.5)
        assert tone.shape == (16000,)

Migration status (XS-42 scoped)
-------------------------------

Only 2 of the 26 test files listed in XS-42 have been migrated to import
from this module so far:

- ``tests/test_api_doc_accuracy.py`` (uses :func:`make_voice_typer_app`)
- ``tests/test_audio_processor.py`` (uses :func:`make_sine`)

The remaining 24 files (including ``tests/test_clipboard_paste_restore.py``
whose ``_make_cm`` / ``_make_snapshot`` helpers are clipboard-specific
and do not match the factories exported here) are documented as
Remaining Work in the XS-FIX-2 return.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_voice_typer_app(tmp_config_dir: Any, monkeypatch: Any) -> Any:
    """Build a ``VoiceTyperApp`` with mocked hardware/GUI dependencies.

    This is the canonical app-construction helper for tests that need a
    real ``VoiceTyperApp`` instance (NOT a ``MagicMock``). It mirrors
    the ``app`` fixture in ``tests/app/conftest.py`` and the ``_make_app``
    helpers that were copy-pasted across multiple test files.

    Pre-configures:

    - ``is_autostart_enabled`` / ``enable_autostart`` /
      ``disable_autostart`` / ``list_microphones`` patched on
      ``voice_typer.server.app`` so the constructor doesn't touch the
      real autostart registry or microphone probing.
    - ``instance.config.esc_cancel_enabled = False`` for deterministic
      test behavior (the default is True; tests that exercise the ESC
      cancel path should re-enable it).
    - ``instance.config.voice_biometric_consent = True`` so the
      recording path can be exercised (NEW-PRIV-009).
    - ``instance.models.transcriber = MagicMock(is_loaded=True)`` and
      ``_sync_registry_from_fields()`` so ``_start_dictation``'s
      ``ensure_active_engine_loaded()`` doesn't try to create a fresh
      ``TranscriptionEngine`` (ARCH-REFAC-003).

    Parameters
    ----------
    tmp_config_dir : pathlib.Path
        The temporary config directory (typically from the project-wide
        ``tmp_config_dir`` fixture in ``tests/conftest.py``). Unused
        inside the helper itself but required as a positional argument
        so callers explicitly pass the fixture — this mirrors the
        signature of the copy-pasted ``_make_app`` helpers and keeps
        the dependency on the config-directory monkeypatch visible.
    monkeypatch : pytest.MonkeyPatch
        The active ``monkeypatch`` fixture for the calling test.

    Returns
    -------
    voice_typer.server.app.VoiceTyperApp
        A constructed app instance, ready for test interaction.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # NEW-PRIV-009 (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings > Privacy before recording).
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is created in _do_startup (background), not
    # __init__. Set a mock transcriber for tests that need it.
    # ARCH-REFAC-003: with the @property delegate removed, assigning to
    # instance.models.transcriber no longer auto-syncs the registry —
    # call _sync_registry_from_fields() so the registry knows about the
    # mock and _start_dictation's ensure_active_engine_loaded() doesn't
    # try to create a fresh TranscriptionEngine.
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    instance.models._sync_registry_from_fields()
    return instance


def make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5):
    """Generate a 1-D float32 numpy sine wave.

    Mirrors the ``make_sine`` / ``_make_sine`` helpers that were
    copy-pasted across at least 3 audio test files. The signature and
    float32 output dtype match the originals exactly so the migrated
    tests behave identically.

    Parameters
    ----------
    freq : float
        Frequency in Hz.
    duration_s : float
        Duration in seconds.
    sr : int, optional
        Sample rate in Hz. Default 16000 (Whisper's native rate).
    amp : float, optional
        Peak amplitude in [0, 1]. Default 0.5 (~-6 dBFS).

    Returns
    -------
    numpy.ndarray
        1-D float32 array of shape ``(int(sr * duration_s),)``.
    """
    import numpy as np

    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# Sentinel type alias used only in the type hints above. We use ``Any``
# for ``monkeypatch`` and the return type so the module stays importable
# without pytest installed at type-check time. We do NOT actually import
# pytest at module top so importing this module doesn't transitively
# pull in the full pytest runtime (which would slow down test
# collection).


__all__ = [
    "make_voice_typer_app",
    "make_sine",
]
