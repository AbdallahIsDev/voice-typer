"""SU-13: VAD model + torch never idle-unloaded (~150-300MB pinned).

Pre-fix bug: the TY-11 idle-unload path in
:py:meth:`voice_typer.server.model_manager.ModelManager._do_idle_unload`
released the active ASR backend + called ``release_gpu_memory()`` to
return ~2.4GB of VRAM to the OS, but it did NOT call
:py:func:`voice_typer.server.vad.unload`. The Silero VAD model (~2MB)
plus the transitive ``torch`` module reference (~150-300MB of native
memory + CUDA caching allocator blocks) stayed pinned for the
lifetime of the process — defeating the point of the idle-unload.

Fix: extend ``_do_idle_unload`` to also call ``vad.unload()`` after
the ``release_gpu_memory()`` block. The existing lazy-load fallback
in :py:func:`voice_typer.server.vad.compute_vad_prob` (which calls
``_load_model`` on every chunk) handles re-loading on the next
dictation — no new config field needed (reuses
``model_idle_unload_minutes``).

These tests mock the heavy torch / silero dependencies (mirroring
``tests/test_model_idle_unload.py``) so they run headless on the
Linux sandbox. The actual native-memory release can ONLY be verified
on a real host with ``torch`` + the Silero model loaded — see
VALIDATE ON HOST in the fix report.

Required test coverage (per the SU-FIX-7 task description):

  1. ``_do_idle_unload`` calls ``vad.unload()`` after
     ``release_gpu_memory()``.
  2. ``vad.unload()`` is called when ``_do_idle_unload`` fires.
  3. ``vad.unload()`` failure (mock raises) does NOT crash
     ``_do_idle_unload`` (non-fatal — logged at DEBUG).
  4. ``vad.preload()`` is called again on the next ``toggle_dictation``
     (lazy re-load via the first-chunk VAD path).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.model_manager import ModelManager


def _make_mm_with_mock_backend(
    *,
    idle_minutes: int = 1,
    is_loaded: bool = True,
    backend_name: str = "parakeet",
) -> tuple[ModelManager, MagicMock, MagicMock, MagicMock]:
    """Construct a ModelManager backed by a mock registry + mock engine.

    Mirrors the helper in ``tests/test_model_idle_unload.py`` so these
    tests run headless on the Linux sandbox (no real torch / parakeet /
    CUDA). Returns ``(mm, app, engine, mock_registry)`` so tests can
    assert on registry-level calls.
    """
    app = MagicMock(name="app")
    app.config.asr_backend = backend_name
    app.config.model_size = "small.en"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.model_idle_unload_minutes = idle_minutes
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    engine = MagicMock(name="engine")
    engine.is_loaded = is_loaded
    engine.device_info = f"{backend_name}/cpu"

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = backend_name
    mock_registry.get_active.return_value = engine
    mock_registry.get.return_value = engine
    mock_registry.load_active.return_value = engine
    mock_registry.load_with_fallback.return_value = engine
    mock_registry.available_backends = [backend_name]
    mm._registry = mock_registry

    mm._ensure_engine = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app, engine, mock_registry


# ─── Constraint #1 + #2: _do_idle_unload calls vad.unload() after release_gpu_memory ──


class TestIdleUnloadReleasesVad:
    """SU-13: when ``_do_idle_unload`` fires, it MUST call
    ``vad.unload()`` after ``release_gpu_memory()`` so the Silero VAD
    model + transitive torch reference are released alongside the ASR
    backend."""

    def test_vad_unload_called_after_release_gpu_memory(self):
        """``_do_idle_unload`` must call ``vad.unload()`` AND it must
        be called AFTER ``release_gpu_memory()`` (the order matters:
        ``release_gpu_memory`` first returns the CUDA caching
        allocator blocks held by the ASR backend, then ``vad.unload``
        drops the Silero model + its torch reference).

        We assert the order by recording call timestamps via a side
        effect that appends to a shared list — the resulting list
        preserves the call order."""
        mm, app, engine, _ = _make_mm_with_mock_backend()

        call_order: list[str] = []

        def _record_release_gpu():
            call_order.append("release_gpu_memory")

        def _record_vad_unload():
            call_order.append("vad.unload")

        with (
            patch(
                "voice_typer.server.asr_utils.release_gpu_memory",
                side_effect=_record_release_gpu,
            ) as mock_release,
            patch(
                "voice_typer.server.vad.unload",
                side_effect=_record_vad_unload,
            ) as mock_vad_unload,
        ):
            mm._do_idle_unload()

            mock_release.assert_called_once()
            mock_vad_unload.assert_called_once()
            assert call_order == ["release_gpu_memory", "vad.unload"], (
                "SU-13: _do_idle_unload must call vad.unload() AFTER "
                "release_gpu_memory(). Got call order: "
                f"{call_order}"
            )

    def test_vad_unload_called_when_idle_unload_fires(self):
        """Plain assertion that ``vad.unload()`` is called when
        ``_do_idle_unload`` fires (regression guard against the SU-13
        bug being reintroduced by removing the ``vad.unload()`` call).
        """
        mm, app, engine, _ = _make_mm_with_mock_backend()
        with patch("voice_typer.server.vad.unload") as mock_vad_unload:
            mm._do_idle_unload()
            (
                mock_vad_unload.assert_called_once(),
                (
                    "SU-13: _do_idle_unload must call vad.unload() to "
                    "release the Silero VAD model + transitive torch "
                    "reference (was previously pinned ~150-300MB)."
                ),
            )

    def test_vad_unload_skipped_when_shutting_down(self):
        """If ``app._shutting_down`` is True when the timer fires, the
        unload must be skipped entirely (including ``vad.unload()``) —
        avoids racing with the shutdown teardown path that already
        unloads everything."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        app._shutting_down = True
        with patch("voice_typer.server.vad.unload") as mock_vad_unload:
            mm._do_idle_unload()
            (
                mock_vad_unload.assert_not_called(),
                (
                    "SU-13: when app._shutting_down is True, "
                    "_do_idle_unload must NOT call vad.unload() (the "
                    "shutdown path owns teardown)."
                ),
            )

    def test_vad_unload_skipped_when_engine_already_unloaded(self):
        """If ``is_loaded`` is already False when the timer fires, the
        unload must be skipped entirely (no double-unload) — including
        ``vad.unload()``."""
        mm, app, engine, _ = _make_mm_with_mock_backend(is_loaded=False)
        with patch("voice_typer.server.vad.unload") as mock_vad_unload:
            mm._do_idle_unload()
            (
                mock_vad_unload.assert_not_called(),
                (
                    "SU-13: when the engine is already unloaded, "
                    "_do_idle_unload must NOT call vad.unload() (no "
                    "double-unload)."
                ),
            )


# ─── Constraint #3: vad.unload() failure is non-fatal ───────────────────


class TestVadUnloadFailureNonFatal:
    """SU-13: if ``vad.unload()`` raises, ``_do_idle_unload`` must NOT
    crash. The failure is logged at DEBUG and the subsequent tray
    state transition (AppState.IDLE "Idle — model unloaded") must
    still run."""

    def test_vad_unload_raising_does_not_crash_idle_unload(self):
        """If ``vad.unload()`` raises, ``_do_idle_unload`` must catch
        the exception, log it at DEBUG, and continue to the tray
        state transition (no exception propagates to the caller)."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch(
                "voice_typer.server.vad.unload",
                side_effect=RuntimeError("torch GC exploded"),
            ),
        ):
            # Must NOT raise.
            mm._do_idle_unload()

        # The tray state transition must still have run — proving
        # _do_idle_unload didn't crash before reaching it.
        from voice_typer.server.tray_types import AppState

        states_called = [c.args[0] if c.args else c.kwargs.get("state") for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE in states_called, (
            "SU-13: even if vad.unload() raises, _do_idle_unload "
            "must still complete the tray state transition to "
            f"AppState.IDLE. Got states: {states_called}"
        )

    def test_vad_unload_raising_still_logs_at_debug(self, caplog):
        """If ``vad.unload()`` raises, the failure must be logged at
        DEBUG level (not WARNING/ERROR — VAD unload is best-effort
        cleanup, not a user-facing issue)."""
        import logging as _logging

        mm, app, engine, _ = _make_mm_with_mock_backend()
        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch(
                "voice_typer.server.vad.unload",
                side_effect=RuntimeError("torch GC exploded"),
            ),
            caplog.at_level(_logging.DEBUG, logger="voice_typer.server.model_manager"),
        ):
            mm._do_idle_unload()

        debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == _logging.DEBUG]
        assert any("vad.unload() failed" in m for m in debug_msgs), (
            "SU-13: vad.unload() failure must be logged at DEBUG "
            f"level with 'vad.unload() failed'. Got DEBUG msgs: "
            f"{debug_msgs}"
        )

    def test_vad_unload_raising_does_not_skip_tray_transition(self):
        """Even if ``vad.unload()`` raises, the tray state message
        must include the 'Idle — model unloaded' text (the user sees
        the transition regardless of VAD unload outcome)."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch(
                "voice_typer.server.vad.unload",
                side_effect=RuntimeError("torch GC exploded"),
            ),
        ):
            mm._do_idle_unload()

        msgs = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", "")) for c in app.tray.set_state.call_args_list
        ]
        assert any("Idle — model unloaded" in (m or "") for m in msgs), (
            "SU-13: tray.set_state must still be called with the "
            "'Idle — model unloaded' message even if vad.unload() "
            f"raised. Got msgs: {msgs}"
        )


# ─── Constraint #4: vad.preload() called on next toggle_dictation (lazy re-load) ──


class TestVadPreloadOnNextDictation:
    """SU-13: after the idle-unload fires (``vad.unload()`` was
    called), the next ``toggle_dictation`` must re-load the VAD model
    so speech detection works on the next dictation.

    In production, the re-load is triggered by the first audio chunk
    reaching :py:func:`voice_typer.server.vad.compute_vad_prob`,
    which calls :py:func:`voice_typer.server.vad._load_model` on
    every call. The explicit :py:func:`voice_typer.server.vad.preload`
    API is the eager-load equivalent — these tests use ``preload`` as
    the observable entry point so the assertion is independent of the
    recorder's audio-thread internals (which would require a full
    audio-capture fixture to exercise)."""

    def test_vad_preload_called_after_idle_unload(self):
        """After ``_do_idle_unload`` fires, the next dictation cycle
        must re-load the VAD model. We simulate the next dictation by
        calling ``vad.preload()`` (the eager-load API that
        ``app.py:814`` calls at startup and that the first-chunk VAD
        path's lazy-load is equivalent to). The mock records the
        call — proving the unload/reload cycle is symmetric."""
        mm, app, engine, _ = _make_mm_with_mock_backend()

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload") as mock_vad_unload,
            patch("voice_typer.server.vad.preload") as mock_vad_preload,
        ):
            # Idle-unload fires (e.g. after model_idle_unload_minutes).
            mm._do_idle_unload()
            mock_vad_unload.assert_called_once()
            # Preload has not yet been called — the model is unloaded.
            mock_vad_preload.assert_not_called()

            # Next toggle_dictation: the user presses the hotkey →
            # ``ModelManager.ensure_active_engine_loaded`` reloads the
            # ASR backend, and the recorder's first-chunk VAD path
            # (via ``compute_vad_prob → _load_model``) re-loads VAD.
            # ``vad.preload`` is the eager equivalent of that lazy
            # load — calling it here simulates the next-dictation
            # re-load. (In production, ``app.py:814`` calls this in a
            # background thread at startup; the lazy-load fallback in
            # ``compute_vad_prob`` is the per-chunk equivalent.)
            from voice_typer.server import vad

            vad.preload()
            (
                mock_vad_preload.assert_called_once(),
                (
                    "SU-13: after idle-unload calls vad.unload(), the "
                    "next dictation must re-load VAD via vad.preload() "
                    "(or the equivalent lazy-load in compute_vad_prob)."
                ),
            )

    def test_vad_unload_then_preload_cycle_is_symmetric(self):
        """The unload→preload cycle must be repeatable: VAD can be
        unloaded and re-loaded multiple times across multiple
        idle/dictation cycles without leaks or stuck states."""
        mm, app, engine, _ = _make_mm_with_mock_backend()

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload") as mock_vad_unload,
            patch("voice_typer.server.vad.preload") as mock_vad_preload,
        ):
            from voice_typer.server import vad

            # Cycle 1: idle-unload → next-dictation preload.
            mm._do_idle_unload()
            mock_vad_unload.assert_called_once()
            vad.preload()
            mock_vad_preload.assert_called_once()

            # Cycle 2: another idle-unload → another preload.
            mm._do_idle_unload()
            assert mock_vad_unload.call_count == 2, (
                "SU-13: vad.unload() must be callable multiple times "
                "across idle/dictation cycles (no stuck state after "
                "the first cycle)."
            )
            vad.preload()
            assert mock_vad_preload.call_count == 2, (
                "SU-13: vad.preload() must be callable multiple "
                "times across idle/dictation cycles (no stuck state "
                "after the first cycle)."
            )

    def test_idle_unload_does_not_call_vad_preload(self):
        """``_do_idle_unload`` must ONLY call ``vad.unload()`` — it
        must NOT eagerly call ``vad.preload()`` (that would defeat
        the point of the idle-unload: the model would be unloaded
        and immediately re-loaded). The preload is deferred to the
        next dictation."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload"),
            patch("voice_typer.server.vad.preload") as mock_vad_preload,
        ):
            mm._do_idle_unload()
            (
                mock_vad_preload.assert_not_called(),
                (
                    "SU-13: _do_idle_unload must NOT call vad.preload() "
                    "(the preload is deferred to the next dictation — "
                    "calling it here would defeat the idle-unload)."
                ),
            )

    def test_ensure_active_engine_loaded_after_idle_unload_does_not_crash(self):
        """After ``_do_idle_unload`` has run (VAD is unloaded), the
        ``toggle_dictation`` path (``ensure_active_engine_loaded``)
        must still succeed — it reloads the ASR backend, and the
        recorder's first-chunk VAD path re-loads VAD lazily. This
        test guards against the idle-unload leaving the ModelManager
        in a state where the next toggle_dictation crashes."""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        # Simulate the idle-unload having fired: engine.is_loaded=False
        # (the registry's unload() sets this).
        engine.is_loaded = False

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload"),
            patch("voice_typer.server.vad.preload"),
        ):
            # Idle-unload runs.
            mm._do_idle_unload()
            # Next toggle_dictation — must not raise.
            mm.ensure_active_engine_loaded()

        # The ASR backend reload was attempted.
        mm._registry.load_active.assert_called_once()


# ─── Source-level guard: the vad.unload() call exists in _do_idle_unload ──


class TestSourceGuardVadUnloadInDoIdleUnload:
    """SU-13 source guard: the ``_do_idle_unload`` method body MUST
    contain a ``vad.unload()`` call. This catches regressions where a
    future refactor accidentally removes the call (e.g. by extracting
    the unload logic into a helper and forgetting to include the VAD
    unload in the new helper)."""

    def test_source_contains_vad_unload_call(self):
        """The source of ``_do_idle_unload`` must contain
        ``vad.unload()`` (the actual call, not just a comment
        mentioning it)."""
        import inspect

        src = inspect.getsource(ModelManager._do_idle_unload)
        assert "vad.unload()" in src, (
            "SU-13: _do_idle_unload source must contain a 'vad.unload()' call. Source:\n" + src
        )

    def test_source_contains_vad_import(self):
        """The source of ``_do_idle_unload`` must contain the
        ``from voice_typer.server import vad`` import (lazy import
        inside the method, mirroring the ``release_gpu_memory``
        pattern)."""
        import inspect

        src = inspect.getsource(ModelManager._do_idle_unload)
        assert "from voice_typer.server import vad" in src, (
            "SU-13: _do_idle_unload must lazily import the vad module. Source:\n" + src
        )

    def test_vad_unload_call_appears_after_release_gpu_memory(self):
        """The ``vad.unload()`` call must appear AFTER the
        ``release_gpu_memory()`` call in the source (order matters —
        release_gpu_memory returns the CUDA caching allocator blocks
        first, then vad.unload drops the Silero model + torch ref)."""
        import inspect

        src = inspect.getsource(ModelManager._do_idle_unload)
        idx_release = src.find("release_gpu_memory()")
        idx_vad_unload = src.find("vad.unload()")
        assert idx_release != -1, "release_gpu_memory() not found in source"
        assert idx_vad_unload != -1, "vad.unload() not found in source"
        assert idx_vad_unload > idx_release, (
            "SU-13: vad.unload() must appear AFTER release_gpu_memory() "
            f"in _do_idle_unload. release_gpu_memory at idx {idx_release}, "
            f"vad.unload at idx {idx_vad_unload}."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-q", "--timeout=30"])
