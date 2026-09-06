"""Regression test for ``_secure_clear_array`` must be called via
``_recording_pkg._secure_clear_array``, not bare-name ``_secure_clear_array``.

Pre-fix bug (verified by ruff F821 in review.md):
  - ``recorder.py:1228`` and ``:1233`` used bare-name
    ``_secure_clear_array(self._cached_resampled)`` /
    ``_secure_clear_array(self._cached_no_resample_arr)``.
  - The function is defined in ``recording/buffer.py:37`` and re-exported
    by ``recording/__init__.py:114``, but the bare-name lookup in
    ``recorder.py`` does NOT see it (the module never imports
    ``_secure_clear_array`` directly — per the module docstring, all
    cross-submodule helpers must be routed via ``_recording_pkg.X``).
  - The call sites were wrapped in ``try/except Exception: pass``, so the
    ``NameError`` was silently swallowed — SEC-audit-008 audio-buffer
    clearing was a no-op.

Post-fix:
  - Both call sites use ``_recording_pkg._secure_clear_array(...)`` —
    matching the existing pattern at lines 2575 and 2992 of the same file
    for ``_secure_clear_array_background``.

These tests verify the source-level call-site pattern. They use
``inspect.getsource`` (per the project's TEST-033 convention) so they
catch regressions even without executing the ``start()`` code path that
contains the call sites.
"""

from __future__ import annotations

import contextlib
import inspect

from voice_typer.server.recording import _recorder_split, recorder


class TestSecureClearArrayCallSite:
    """Verify the call-site pattern for ``_secure_clear_array`` in
    ``recorder.py`` matches the CANONICAL owning-module shape (C-ARCH-2):
    the package-object bridge is GONE; call sites consume the submodule
    import directly, and the historical CR-17 NameError regression stays
    impossible because the import is a literal module-top import (a
    removed binding fails at import time, not inside a swallowed
    try/except)."""

    def test_secure_clear_array_uses_owning_module_import(self):
        """recorder.py must import ``_secure_clear_array`` from the owning
        recording package chain at module top and call it by bare name.

        The C-ARCH-2 canonical form (recording-package migration, matching
        ``server_platform``/``prewarm``) removed the ``_recording_pkg.``
        package-object bridge: dual patch paths were the debt class the
        rule forbids, and the literal module-top import keeps the CR-17
        NameError class impossible (a removed binding raises
        ``AttributeError`` at import time instead of being swallowed by
        the try/except).
        """
        src = inspect.getsource(recorder)

        # The owning-module import must be present at module top.
        assert "from voice_typer.server.recording import _secure_clear_array" in src, (
            "recorder.py must import ``_secure_clear_array`` from the "
            "owning recording package chain at module top (C-ARCH-2)."
        )

        # The historical package-object bridge must NOT be reintroduced.
        assert "import recording as _recording_pkg" not in src, (
            "The `_recording_pkg` package-object bridge was removed per "
            "C-ARCH-2. Reintroducing it restores the dual patch-path "
            "debt class — patch the owning module instead."
        )

        # The call must use the imported (bare) name.
        assert "_secure_clear_array(" in inspect.getsource(recorder.Recorder), (
            "Expected a ``_secure_clear_array(...)`` call on Recorder — the secure-clear path went missing."
        )

    def test_secure_clear_array_background_uses_owning_module_import(self):
        """The OTHER secure-clear helper (``_secure_clear_array_background``,
        used at the buffer-reassignment sites in ``_recorder_split.py``) must
        also consume the owning module, not the package object.

        The ``stop()``/``discard()`` bodies were extracted to
        :mod:`voice_typer.server.recording._recorder_split` (Phase 4.5
        god-class decomposition). Under C-ARCH-2 the sites resolve the
        ``recording.buffer`` module object so tests patch the OWNING
        module (``voice_typer.server.recording.buffer._secure_clear_array_background``).
        """
        src = inspect.getsource(_recorder_split)

        # The historical package-object bridge must NOT be reintroduced.
        assert "import recording as _recording_pkg" not in src, (
            "The `_recording_pkg` package-object bridge was removed per "
            "C-ARCH-2 — do not reintroduce it in _recorder_split.py."
        )

        # The call must use the bare name resolved from the buffer module.
        assert "_secure_clear_array_background(" in src, (
            "Expected a ``_secure_clear_array_background(...)`` call in "
            "_recorder_split.py — the secure-clear path went missing."
        )


# ─── Behavior-level test: start() must not silently swallow the call ────


class TestSecureClearArrayBehavior:
    """Verify that ``start()`` actually invokes the package-namespace
    helper, not a no-op ``try/except: pass`` that silently swallows a
    NameError."""

    def test_start_invokes_secure_clear_array(self, monkeypatch):
        """When ``start()`` clears the cached arrays, it must call the
        ``_secure_clear_array`` function recorder.py imports from the
        owning recording package chain.

        Pre-fix (CR-17): the bare-name call raised NameError, which was
        swallowed by the surrounding ``try/except Exception: pass`` — so
        the underlying ``buffer._secure_clear_array`` was NEVER invoked.
        Post-fix: the call uses the module-top import, and a removed
        binding fails at import time instead of being swallowed.
        """
        from unittest.mock import MagicMock

        from voice_typer.server.recording import Recorder, recorder as recorder_mod

        # Spy on the recorder module's own imported binding (C-ARCH-2:
        # the consuming module's binding is the single patch path).
        call_log: list = []
        original = recorder_mod._secure_clear_array

        def spy(arr):
            call_log.append(arr)
            # Call the real implementation so the array is actually zeroed
            # (in case any downstream code checks for that).
            return original(arr)

        monkeypatch.setattr(recorder_mod, "_secure_clear_array", spy)

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        r = Recorder(config, audio_processor=None)

        # Populate the cached arrays so the secure-clear path runs.
        r._cached_resampled = r._cached_resampled = __import__("numpy").zeros(8, dtype=__import__("numpy").float32)
        r._cached_no_resample_arr = __import__("numpy").zeros(8, dtype=__import__("numpy").float32)

        # start() (which runs the secure-clear path before clearing state)
        # should call the package-namespace helper for each cached array.
        # We don't actually start a stream — just exercise the start()
        # method body up to the point where it tries to open an
        # InputStream (which will fail since sounddevice is mocked).
        # start() may fail later (no real device) — that's fine;
        # we only care that the secure-clear call sites ran first.
        with contextlib.suppress(Exception):
            r.start()

        # Clean up any worker threads spawned by start() so they don't
        # trip the "no leaked worker threads" checks in later tests.
        with contextlib.suppress(Exception):
            r.stop()

        # Pre-fix: this assertion would fail because the bare-name call
        # raised NameError, which was swallowed by ``try/except: pass``,
        # so ``call_log`` stayed empty.
        assert len(call_log) >= 1, (
            "Expected _recording_pkg._secure_clear_array to be called at "
            "least once from start(), but it was never invoked. CR-17 "
            "regression: the bare-name call site is raising NameError "
            "and the surrounding try/except is swallowing it — "
            "SEC-audit-008 audio-buffer clearing is a no-op."
        )
