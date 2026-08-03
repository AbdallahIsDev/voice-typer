"""Regression test for CR-17: ``_secure_clear_array`` must be called via
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
    ``recorder.py`` matches the module-docstring promise (route via
    ``_recording_pkg.X``)."""

    def test_secure_clear_array_uses_recording_pkg_prefix(self):
        """recorder.py source must call ``_recording_pkg._secure_clear_array``,
        not bare ``_secure_clear_array``.

        The module docstring (lines 5-22) explicitly promises that all
        cross-submodule helpers are routed through ``_recording_pkg`` so
        test patches of the form
        ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
        keep affecting production code defined here.
        """
        src = inspect.getsource(recorder.Recorder)

        # The buggy bare-name form must NOT appear in any production call.
        # We check for the call-site pattern (whitespace + opening paren)
        # to avoid matching ``_recording_pkg._secure_clear_array`` (which
        # contains the substring ``_secure_clear_array``).
        bare_call_token = " _secure_clear_array("
        assert bare_call_token not in src, (
            "Bare-name `_secure_clear_array(...)` call found in "
            "recorder.py — should be `_recording_pkg._secure_clear_array(...)`. "
            "CR-17 regression: this raises NameError at runtime (silently "
            "swallowed by the try/except), leaving SEC-audit-008 "
            "audio-buffer clearing as a no-op."
        )

        # Also catch the form at start-of-line (e.g. after a dedent) —
        # ``bare_call_token`` requires a leading space which misses
        # indented-but-not-space-prefixed occurrences.
        assert "\n_secure_clear_array(" not in src, (
            "Bare-name `_secure_clear_array(...)` call found at "
            "start-of-line in recorder.py — same CR-17 regression as above."
        )

        # The correct form must appear in the source.
        assert "_recording_pkg._secure_clear_array(" in src, (
            "Expected `_recording_pkg._secure_clear_array(...)` call not "
            "found in recorder.py. CR-17 fix is missing — the call site "
            "must route through the package namespace per the module "
            "docstring (lines 5-22)."
        )

    def test_secure_clear_array_background_uses_recording_pkg_prefix(self):
        """Sanity check: the OTHER secure-clear helper
        (``_secure_clear_array_background``, used in stop()/discard() at
        the buffer-reassignment sites in ``_recorder_split.py``) must ALSO
        use the ``_recording_pkg.`` prefix.

        The ``stop()``/``discard()`` bodies were extracted to
        :mod:`voice_typer.server.recording._recorder_split` (Phase 4.5
        god-class decomposition), so the
        ``_secure_clear_array_background`` call sites now live in
        ``_recorder_split.py`` (not on ``recorder.Recorder`` directly).
        This test pins the existing pattern there so future refactors
        don't accidentally introduce a bare-name call.
        """
        src = inspect.getsource(_recorder_split)

        bare_call_token = " _secure_clear_array_background("
        assert bare_call_token not in src, (
            "Bare-name `_secure_clear_array_background(...)` call found in "
            "_recorder_split.py — should be "
            "`_recording_pkg._secure_clear_array_background(...)`."
        )
        assert "\n_secure_clear_array_background(" not in src, (
            "Bare-name `_secure_clear_array_background(...)` call found at "
            "start-of-line in _recorder_split.py — should be "
            "`_recording_pkg._secure_clear_array_background(...)`."
        )

        # The correct form must appear (used in both stop() and discard()
        # buffer-reassignment sites inside _recorder_split).
        assert "_recording_pkg._secure_clear_array_background(" in src, (
            "Expected `_recording_pkg._secure_clear_array_background(...)` call not found in _recorder_split.py."
        )

    def test_recording_pkg_alias_is_defined_at_module_top(self):
        """The ``_recording_pkg`` alias must be defined at module top.

        Without this binding, ``_recording_pkg._secure_clear_array(...)``
        would itself raise ``NameError``. The module docstring promises
        the alias is defined at the top of ``recorder.py`` (via
        ``from voice_typer.server import recording as _recording_pkg``).
        """
        src = inspect.getsource(recorder)
        # The import statement that binds the alias.
        assert "import recording as _recording_pkg" in src, (
            "Expected `from voice_typer.server import recording as "
            "_recording_pkg` not found at module top of recorder.py. "
            "Without this binding, `_recording_pkg._secure_clear_array(...)` "
            "would itself raise NameError."
        )

    def test_no_bare_name_secure_clear_array_anywhere_in_module(self):
        """No bare-name ``_secure_clear_array`` reference may appear
        anywhere in the recorder module — not even in comments that
        could mislead future maintainers into re-introducing the bug.

        This is a stricter scan than the call-site check above; it
        catches docstrings, comments, and any other site that might
        re-introduce the bare-name pattern. We allow the qualified form
        (``_recording_pkg._secure_clear_array``) and explicit references
        to the function name as a string (e.g. in docstrings describing
        the function).
        """
        src = inspect.getsource(recorder)
        # Strip qualified references first (so the substring check below
        # doesn't match ``_recording_pkg._secure_clear_array``).
        stripped = src.replace("_recording_pkg._secure_clear_array", "")

        # The remaining bare-name references in actual call positions
        # (`` _secure_clear_array(`` or ``\n_secure_clear_array(``)
        # must not appear. We DO allow mentions of the bare name in
        # prose/comments (e.g. ``_secure_clear_array is defined at...``)
        # — only flag actual call positions.
        assert " _secure_clear_array(" not in stripped, (
            "Unexpected bare-name `_secure_clear_array(` call in recorder.py (after stripping qualified refs)."
        )
        assert "\n_secure_clear_array(" not in stripped, (
            "Unexpected bare-name `_secure_clear_array(` call at "
            "start-of-line in recorder.py (after stripping qualified refs)."
        )


# ─── Behavior-level test: start() must not silently swallow the call ────


class TestSecureClearArrayBehavior:
    """Verify that ``start()`` actually invokes the package-namespace
    helper, not a no-op ``try/except: pass`` that silently swallows a
    NameError."""

    def test_start_invokes_secure_clear_array_via_package_namespace(self, monkeypatch):
        """When ``start()`` clears the cached arrays, it must call
        ``voice_typer.server.recording._secure_clear_array`` (the
        package-namespace re-export from ``.buffer``).

        Pre-fix: the bare-name call raised NameError, which was swallowed
        by the surrounding ``try/except Exception: pass`` — so the
        underlying ``buffer._secure_clear_array`` was NEVER invoked.
        Post-fix: the call routes through ``_recording_pkg.`` and the
        real helper runs.
        """
        from unittest.mock import MagicMock

        from voice_typer.server import recording as rec_mod
        from voice_typer.server.recording import Recorder

        # Spy on the package-namespace helper.
        call_log: list = []
        original = rec_mod._secure_clear_array

        def spy(arr):
            call_log.append(arr)
            # Call the real implementation so the array is actually zeroed
            # (in case any downstream code checks for that).
            return original(arr)

        monkeypatch.setattr(rec_mod, "_secure_clear_array", spy)

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
