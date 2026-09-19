"""
Regression test for ``_secure_clear_array`` must be called via
``NameError`` was silently swallowed, SEC-audit-008 audio-buffer
"""

from __future__ import annotations

import contextlib
import inspect

from voice_typer.server.recording import _recorder_split, recorder


class TestSecureClearArrayCallSite:
    """``recorder.py`` matches the CANONICAL owning-module shape (C-ARCH-2):"""

    def test_secure_clear_array_uses_owning_module_import(self):
        """
        recorder.py must import ``_secure_clear_array`` from the owning
        The C-ARCH-2 canonical form (recording-package migration, matching
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
            "debt class, patch the owning module instead."
        )

        # The call must use the imported (bare) name.
        assert "_secure_clear_array(" in inspect.getsource(recorder.Recorder), (
            "Expected a ``_secure_clear_array(...)`` call on Recorder, the secure-clear path went missing."
        )

    def test_secure_clear_array_background_uses_owning_module_import(self):
        """The OTHER secure-clear helper (``_secure_clear_array_background``,"""
        src = inspect.getsource(_recorder_split)

        # The historical package-object bridge must NOT be reintroduced.
        assert "import recording as _recording_pkg" not in src, (
            "The `_recording_pkg` package-object bridge was removed per "
            "C-ARCH-2, do not reintroduce it in _recorder_split.py."
        )

        # The call must use the bare name resolved from the buffer module.
        assert "_secure_clear_array_background(" in src, (
            "Expected a ``_secure_clear_array_background(...)`` call in "
            "_recorder_split.py, the secure-clear path went missing."
        )


class TestSecureClearArrayBehavior:
    """Verify that ``start()`` actually invokes the package-namespace"""

    def test_start_invokes_secure_clear_array(self, monkeypatch):
        """owning recording package chain."""
        from unittest.mock import MagicMock

        from voice_typer.server.recording import Recorder, recorder as recorder_mod

        # Spy on the recorder module's own imported binding (C-ARCH-2:
        call_log: list = []
        original = recorder_mod._secure_clear_array

        def spy(arr):
            call_log.append(arr)
            # Call the real implementation so the array is actually zeroed
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

        with contextlib.suppress(Exception):
            r.start()

        # Clean up any worker threads spawned by start() so they don't
        with contextlib.suppress(Exception):
            r.stop()

        # Pre-fix: this assertion would fail because the bare-name call
        assert len(call_log) >= 1, (
            "Expected _recording_pkg._secure_clear_array to be called at "
            "least once from start(), but it was never invoked. CR-17 "
            "regression: the bare-name call site is raising NameError "
            "and the surrounding try/except is swallowing it, "
            "SEC-audit-008 audio-buffer clearing is a no-op."
        )
