"""Shared app-level test helpers for VoiceTyperApp and audio test data.

XS-42: this module exports two factory functions that were previously
copy-pasted across at least 6 test files:

- :func:`make_voice_typer_app` — builds a real ``VoiceTyperApp``
  instance with hardware/GUI dependencies mocked out. Mirrors the
  ``app`` fixture in ``tests/app/conftest.py`` and the ``_make_app``
  helpers in ``tests/test_api_doc_accuracy.py`` /
  ``tests/test_config_editor_lock.py``.
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

Migration status
----------------

Test files migrated to import from this module so far:

- ``tests/test_api_doc_accuracy.py`` (uses :func:`make_voice_typer_app`)
- ``tests/test_audio_processor.py`` (uses :func:`make_sine`)
- ``tests/test_golden_path_dictation.py`` (uses :func:`make_voice_typer_app`
  + :func:`join_model_load_thread` + :func:`make_sine`)
- ``tests/test_config_editor_lock.py`` (uses :func:`make_voice_typer_app`)
- ``tests/test_config_mutation_lock_wiring.py`` (uses
  :func:`make_voice_typer_app`)

The remaining files (including ``tests/test_clipboard_paste_restore.py``
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
    - ``voice_typer.server.config._enforce_windows_owner_only_acl``
      no-op'd so ``Config.save()`` never spawns the real Windows
      ``icacls`` subprocess during a test. On a real Windows host the
      config module reads the true platform (NOT the test-forced one),
      so every save() would otherwise fire real icacls calls that
      interfere with tests that fake/track subprocess (they consume
      fake-editor waits, pollute Popen-call assertions, and break
      Popen patches that don't implement ``communicate()``). On POSIX
      the real helper is itself a no-op, so the patch is
      behavior-identical there.
    - ``instance.config.esc_cancel_enabled = False`` for deterministic
      test behavior (the default is True; tests that exercise the ESC
      cancel path should re-enable it).
    - ``instance.config.voice_biometric_consent = True`` so the
      recording path can be exercised (NEW-PRIV-009).
    - ``instance.models.transcriber = MagicMock(is_loaded=True)`` so
      ``_start_dictation``'s ``ensure_active_engine_loaded()`` doesn't
      try to create a fresh ``TranscriptionEngine``. The ``transcriber``
      attribute is a @property whose setter delegates to
      ``self._registry.register(...)`` so the assignment keeps the
      registry in sync automatically (ARCH-REFAC-003).

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
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
    # No-op the Windows-only icacls ACL enforcement in Config.save(). On a
    # real Windows host, ``config.is_windows()`` reads the true platform
    # (not the test-forced one), so EVERY save() fires real icacls
    # subprocess calls. Those interfere with tests that fake/track the
    # subprocess layer: they consume fake-editor waits (blowing the
    # timeout budget), pollute no-bare-Popen assertions (icacls is
    # spawned via subprocess.run, which internally constructs the patched
    # Popen), and break Popen patches whose fakes lack ``communicate()``.
    # The ACL tightening is incidental best-effort hardening, and on
    # POSIX the real helper is itself a no-op, so patching it here is
    # behavior-identical on every platform.
    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        lambda *a, **k: None,
    )

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings > Privacy before recording).
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is created in _do_startup (background), not
    # __init__. Set a mock transcriber for tests that need it.
    # The ``transcriber`` attribute is a @property whose setter delegates
    # to ``self._registry.register("whisper", ...)`` — so this assignment
    # keeps the registry in sync automatically and ensure_active_engine_loaded()
    # won't try to create a fresh TranscriptionEngine.
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Never duck the developer's REAL system volume. The volume
    # backend factory is also globalized on
    # (``mock_heavy_imports_session``), but an inert ducker here keeps
    # any test that starts dictation from touching the hardware even
    # if a later test re-exposes ``get_volume_backend``.
    instance._volume_ducker = MagicMock()
    instance._volume_ducker.initialize.return_value = False
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


def join_model_load_thread(app: Any, timeout: float = 2.0) -> None:
    """Best-effort join of ``app.models._model_load_thread`` after a test.

    Mirrors the teardown logic in ``tests/app/conftest.py:59-61`` (the
    ``app`` fixture). ``VoiceTyperApp.__init__`` schedules the model
    load on a background daemon thread (``_do_startup`` →
    ``_model_load_thread``). Without joining that thread at the end of
    a test, the loader can keep running after the test's VoiceTyperApp
    instance has been torn down — touching freed attributes and causing
    flaky failures in unrelated later tests (see the
    ``tests/app/conftest.py`` docstring).

    Tests that construct a real ``VoiceTyperApp`` via
    :func:`make_voice_typer_app` should call this helper in a
    ``try/finally`` (or use it from a yield-style fixture's teardown
    branch) to match the ``app`` fixture's behaviour::

        from tests.fixtures.app_helpers import (
            make_voice_typer_app,
            join_model_load_thread,
        )

        def test_thing(tmp_config_dir, monkeypatch):
            app = make_voice_typer_app(tmp_config_dir, monkeypatch)
            try:
                ...  # exercise app
            finally:
                join_model_load_thread(app)

    Best-effort: if the thread is ``None`` or has already finished, the
    ``join`` is a no-op. The ``timeout`` bounds how long we wait —
    matching the 2.0s default used by the canonical ``app`` fixture.

    Parameters
    ----------
    app : voice_typer.server.app.VoiceTyperApp
        The app instance whose background loader thread should be
        joined. Accepts any object whose ``models`` attribute may carry
        a ``_model_load_thread`` field — duck-typed so test fakes that
        don't set ``models`` (or set it to ``None``) are tolerated.
    timeout : float, optional
        Maximum seconds to wait for the loader thread to finish.
        Default 2.0 (matches ``tests/app/conftest.py``).
    """
    models = getattr(app, "models", None)
    if models is None:
        return
    loader = getattr(models, "_model_load_thread", None)
    if loader is None:
        return
    if not getattr(loader, "is_alive", lambda: False)():
        return
    loader.join(timeout=timeout)


# Sentinel type alias used only in the type hints above. We use ``Any``
# for ``monkeypatch`` and the return type so the module stays importable
# without pytest installed at type-check time. We do NOT actually import
# pytest at module top so importing this module doesn't transitively
# pull in the full pytest runtime (which would slow down test
# collection).


__all__ = [
    "make_voice_typer_app",
    "make_sine",
    "join_model_load_thread",
]
