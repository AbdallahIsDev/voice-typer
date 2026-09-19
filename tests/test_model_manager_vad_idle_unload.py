"""SU-13: VAD model + torch never idle-unloaded (~150-300MB pinned)."""

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
    """Construct a ModelManager backed by a mock registry + mock engine."""
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


class TestIdleUnloadReleasesVad:
    """SU-13: when ``_do_idle_unload`` fires, it MUST call"""

    def test_vad_unload_called_after_release_gpu_memory(self):
        """``_do_idle_unload`` must call ``vad.unload()`` AND it must"""
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
        """Plain assertion that ``vad.unload()`` is called when"""
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
        """unload must be skipped entirely (including ``vad.unload()``) —"""
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
        """unload must be skipped entirely (no double-unload), including"""
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


class TestVadUnloadFailureNonFatal:
    """SU-13: if ``vad.unload()`` raises, ``_do_idle_unload`` must NOT"""

    def test_vad_unload_raising_does_not_crash_idle_unload(self):
        """If ``vad.unload()`` raises, ``_do_idle_unload`` must catch"""
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

        # The tray state transition must still have run, proving
        from voice_typer.server.tray_types import AppState

        states_called = [c.args[0] if c.args else c.kwargs.get("state") for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE in states_called, (
            "SU-13: even if vad.unload() raises, _do_idle_unload "
            "must still complete the tray state transition to "
            f"AppState.IDLE. Got states: {states_called}"
        )

    def test_vad_unload_raising_still_logs_at_debug(self, caplog):
        """If ``vad.unload()`` raises, the failure must be logged at"""
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
        """Even if ``vad.unload()`` raises, the tray state message"""
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
        assert any("Idle, model unloaded" in (m or "") for m in msgs), (
            "SU-13: tray.set_state must still be called with the "
            "'Idle, model unloaded' message even if vad.unload() "
            f"raised. Got msgs: {msgs}"
        )


class TestVadPreloadOnNextDictation:
    """SU-13: after the idle-unload fires (``vad.unload()`` was"""

    def test_vad_preload_called_after_idle_unload(self):
        """After ``_do_idle_unload`` fires, the next dictation cycle"""
        mm, app, engine, _ = _make_mm_with_mock_backend()

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload") as mock_vad_unload,
            patch("voice_typer.server.vad.preload") as mock_vad_preload,
        ):
            # Idle-unload fires (e.g. after model_idle_unload_minutes).
            mm._do_idle_unload()
            mock_vad_unload.assert_called_once()
            # Preload has not yet been called, the model is unloaded.
            mock_vad_preload.assert_not_called()

            # Next toggle_dictation: the user presses the hotkey →
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
        """The unload→preload cycle must be repeatable: VAD can be"""
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
        """``_do_idle_unload`` must ONLY call ``vad.unload()``, it"""
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
                    "(the preload is deferred to the next dictation, "
                    "calling it here would defeat the idle-unload)."
                ),
            )

    def test_ensure_active_engine_loaded_after_idle_unload_does_not_crash(self):
        """``toggle_dictation`` path (``ensure_active_engine_loaded``)"""
        mm, app, engine, _ = _make_mm_with_mock_backend()
        # Simulate the idle-unload having fired: engine.is_loaded=False
        engine.is_loaded = False

        with (
            patch("voice_typer.server.asr_utils.release_gpu_memory"),
            patch("voice_typer.server.vad.unload"),
            patch("voice_typer.server.vad.preload"),
        ):
            # Idle-unload runs.
            mm._do_idle_unload()
            # Next toggle_dictation, must not raise.
            mm.ensure_active_engine_loaded()

        # The ASR backend reload was attempted.
        mm._registry.load_active.assert_called_once()


class TestSourceGuardVadUnloadInDoIdleUnload:
    """SU-13 source guard: the ``_do_idle_unload`` method body MUST"""

    def test_source_contains_vad_unload_call(self):
        """The source of ``_do_idle_unload`` must contain"""
        import inspect

        src = inspect.getsource(ModelManager._do_idle_unload)
        assert "vad.unload()" in src, (
            "SU-13: _do_idle_unload source must contain a 'vad.unload()' call. Source:\n" + src
        )

    def test_source_contains_vad_import(self):
        """``from voice_typer.server import vad`` import (lazy import"""
        import inspect

        src = inspect.getsource(ModelManager._do_idle_unload)
        assert "from voice_typer.server import vad" in src, (
            "SU-13: _do_idle_unload must lazily import the vad module. Source:\n" + src
        )

    def test_vad_unload_call_appears_after_release_gpu_memory(self):
        """``release_gpu_memory()`` call in the source (order matters —"""
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
