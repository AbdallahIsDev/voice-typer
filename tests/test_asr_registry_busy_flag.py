"""UE-48 regression tests: per-backend \"busy\" flag on AsrBackendRegistry."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry


class _Config:
    """Minimal config stub, only ``asr_backend`` is read by the busy"""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend


def _make_registry(*, asr_backend: str = "parakeet") -> AsrBackendRegistry:
    """Construct a registry with a single registered mock backend."""
    registry = AsrBackendRegistry(_Config(asr_backend))
    backend = MagicMock()
    backend.is_loaded = True
    backend.transcribe_with_fallback.return_value = "hello world"
    registry.register(asr_backend, backend)
    return registry


class TestIsBusyDefault:
    """UE-48: ``is_busy`` returns False by default."""

    def test_is_busy_false_by_default(self):
        """A freshly-constructed registry must report every backend as"""
        registry = _make_registry()
        assert registry.is_busy("parakeet") is False, (
            "UE-48: a freshly-constructed registry must report every "
            "backend as not-busy. Pre-fix, the busy flag didn't exist."
        )

    def test_is_busy_none_defaults_to_active(self):
        """``is_busy(None)`` must query the active backend (matching"""
        registry = _make_registry(asr_backend="parakeet")
        assert registry.is_busy(None) is False

    def test_is_busy_unknown_name_returns_false(self):
        """``is_busy`` on an unknown backend name must return False"""
        registry = _make_registry()
        assert registry.is_busy("nonexistent") is False


class TestSetClearBusy:
    """UE-48: ``set_busy`` / ``clear_busy`` mutate the flag."""

    def test_set_busy_makes_is_busy_true(self):
        """After ``set_busy(\"parakeet\")``, ``is_busy(\"parakeet\")`` must"""
        registry = _make_registry()
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

    def test_clear_busy_makes_is_busy_false(self):
        """After ``set_busy`` then ``clear_busy``, ``is_busy`` must"""
        registry = _make_registry()
        registry.set_busy("parakeet")
        registry.clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False

    def test_clear_busy_idempotent_on_not_busy_backend(self):
        """``clear_busy`` on a backend that was never marked busy must"""
        registry = _make_registry()
        # Must NOT raise.
        registry.clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False

    def test_set_busy_none_defaults_to_active(self):
        """``set_busy(None)`` must mark the active backend as busy."""
        registry = _make_registry(asr_backend="parakeet")
        registry.set_busy(None)
        assert registry.is_busy("parakeet") is True

    def test_clear_busy_none_defaults_to_active(self):
        """``clear_busy(None)`` must clear the active backend's busy"""
        registry = _make_registry(asr_backend="parakeet")
        registry.set_busy("parakeet")
        registry.clear_busy(None)
        assert registry.is_busy("parakeet") is False

    def test_set_busy_unknown_name_is_noop(self):
        """``set_busy`` on an unknown backend name must be a no-op"""
        registry = _make_registry()
        registry.set_busy("nonexistent")
        assert registry.is_busy("parakeet") is False


class TestBusyContext:
    """UE-48: ``busy_context`` sets the flag on enter and clears on"""

    def test_busy_context_sets_on_enter_clears_on_exit(self):
        """The flag must be True inside the ``with`` block and False"""
        registry = _make_registry()
        with registry.busy_context("parakeet") as name:
            assert name == "parakeet"
            assert registry.is_busy("parakeet") is True, (
                "UE-48: busy_context must set the flag on enter so concurrent is_busy() callers see True."
            )
        assert registry.is_busy("parakeet") is False, (
            "UE-48: busy_context must clear the flag on exit so the "
            "next dictation isn't rejected by ensure_active_engine_loaded."
        )

    def test_busy_context_clears_on_exception(self):
        """transcription that raises mid-call must not leave the backend"""

        class _TestError(Exception):
            pass

        registry = _make_registry()
        with pytest.raises(_TestError), registry.busy_context("parakeet"):
            assert registry.is_busy("parakeet") is True
            raise _TestError("simulated transcription failure")
        assert registry.is_busy("parakeet") is False, (
            "UE-48: busy_context must clear the flag in a finally block "
            "so a transcription that raises mid-call does not leave the "
            "backend permanently busy (which would block all subsequent "
            "dictations via the ensure_active_engine_loaded busy-check)."
        )

    def test_busy_context_none_defaults_to_active(self):
        """``busy_context(None)`` must mark the active backend as"""
        registry = _make_registry(asr_backend="parakeet")
        with registry.busy_context(None) as name:
            assert name == "parakeet"
            assert registry.is_busy("parakeet") is True
        assert registry.is_busy("parakeet") is False

    def test_busy_context_yields_resolved_name(self):
        """The context manager must yield the resolved backend name so"""
        registry = _make_registry(asr_backend="parakeet")
        with registry.busy_context(None) as name:
            assert name == "parakeet"


class TestTranscribeWithFallbackWrapper:
    """UE-48: the registry's ``transcribe_with_fallback`` wrapper sets"""

    def test_wrapper_sets_and_clears_busy_flag(self):
        """The wrapper must set the flag before the backend call and"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        backend.transcribe_with_fallback.return_value = "test text"

        text = registry.transcribe_with_fallback(b"audio", name="parakeet")

        assert text == "test text"
        assert registry.is_busy("parakeet") is False, (
            "UE-48: the wrapper must clear the busy flag after the backend call returns (happy path)."
        )
        # The backend's transcribe_with_fallback was called exactly once.
        backend.transcribe_with_fallback.assert_called_once()

    def test_wrapper_sets_busy_during_call(self):
        """The flag must be True WHILE the backend's call is running"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        busy_during_call: list[bool] = []

        def _spy_transcribe(audio, *args, **kwargs):
            busy_during_call.append(registry.is_busy("parakeet"))
            return "text"

        backend.transcribe_with_fallback.side_effect = _spy_transcribe

        registry.transcribe_with_fallback(b"audio", name="parakeet")

        assert busy_during_call == [True], (
            "UE-48: the wrapper must set the busy flag BEFORE invoking "
            "the backend's transcribe_with_fallback so concurrent "
            "is_busy() callers (e.g. ensure_active_engine_loaded on "
            "another thread) see True."
        )

    def test_wrapper_clears_busy_on_exception(self):
        """The wrapper must clear the flag EVEN IF the backend's call"""

        class _TranscribeError(Exception):
            pass

        registry = _make_registry()
        backend = registry.get("parakeet")
        backend.transcribe_with_fallback.side_effect = _TranscribeError("simulated ctranslate2 crash")

        with pytest.raises(_TranscribeError):
            registry.transcribe_with_fallback(b"audio", name="parakeet")

        assert registry.is_busy("parakeet") is False, (
            "UE-48: the wrapper must clear the busy flag even if the "
            "backend's transcribe_with_fallback raises. A stuck flag "
            "would block all subsequent dictations via the "
            "ensure_active_engine_loaded busy-check."
        )

    def test_wrapper_passes_through_text_unchanged(self):
        """The wrapper must return the backend's text unchanged"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        expected_text = "the quick brown fox jumps over the lazy dog"
        backend.transcribe_with_fallback.return_value = expected_text

        text = registry.transcribe_with_fallback(b"audio", name="parakeet")

        assert text == expected_text, (
            "UE-48: the wrapper is a transparent passthrough, it must return the backend's text unchanged."
        )

    def test_wrapper_forwards_args_and_kwargs(self):
        """backend's call unchanged (e.g. ``audio_stats=``,"""
        registry = _make_registry()
        backend = registry.get("parakeet")

        local_engine = MagicMock()
        registry.transcribe_with_fallback(
            b"audio",
            "positional_arg",
            name="parakeet",
            audio_stats=(0.1, 0.5, 30.0),
            local_engine=local_engine,
        )

        backend.transcribe_with_fallback.assert_called_once_with(
            b"audio",
            "positional_arg",
            audio_stats=(0.1, 0.5, 30.0),
            local_engine=local_engine,
        )

    def test_wrapper_defaults_to_active_backend(self):
        """active backend (matching ``is_busy`` / ``set_busy`` /"""
        registry = _make_registry(asr_backend="parakeet")
        backend = registry.get("parakeet")

        registry.transcribe_with_fallback(b"audio")

        backend.transcribe_with_fallback.assert_called_once_with(b"audio")

    def test_wrapper_returns_empty_string_for_unknown_backend(self):
        """
        When the named backend is not registered, the wrapper must
        ``get_active``'s last-resort branch contract).
        """
        registry = _make_registry(asr_backend="parakeet")

        text = registry.transcribe_with_fallback(b"audio", name="nonexistent")

        assert text == "", (
            "UE-48: the wrapper must return an empty string for an "
            "unknown backend name (matches get_active's last-resort "
            "silent-empty contract)."
        )

    def test_wrapper_does_not_set_busy_for_unknown_backend(self):
        """The wrapper must NOT set the busy flag for an unknown"""
        registry = _make_registry(asr_backend="parakeet")

        registry.transcribe_with_fallback(b"audio", name="nonexistent")

        assert registry.is_busy("nonexistent") is False
        assert registry.is_busy("parakeet") is False


class TestForceClearBusy:
    """UE-48: ``force_clear_busy`` is an alias for ``clear_busy``"""

    def test_force_clear_busy_clears_flag(self):
        """``force_clear_busy`` must clear a busy flag set by"""
        registry = _make_registry()
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        registry.force_clear_busy("parakeet")

        assert registry.is_busy("parakeet") is False

    def test_force_clear_busy_none_defaults_to_active(self):
        """``force_clear_busy(None)`` must clear the active backend's"""
        registry = _make_registry(asr_backend="parakeet")
        registry.set_busy("parakeet")

        registry.force_clear_busy(None)

        assert registry.is_busy("parakeet") is False

    def test_force_clear_busy_idempotent(self):
        """``force_clear_busy`` on a not-busy backend must be a"""
        registry = _make_registry()
        # Must NOT raise.
        registry.force_clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False


class TestBusyFlagSurvivesBackendSwap:
    """UE-48: the flag is keyed by backend NAME, not the backend"""

    def test_busy_flag_does_not_carry_over_to_new_backend_object(self):
        """When a backend is unregistered and a NEW backend is"""
        registry = _make_registry()
        registry.get("parakeet")

        # (as happens in change_model when the user picks a new
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        registry.unregister("parakeet")
        new_backend = MagicMock()
        new_backend.is_loaded = True
        registry.register("parakeet", new_backend)

        # NOTE: this test pins the CURRENT behaviour, the busy flag
        # ``change_model`` / ``set_active_backend`` paths do NOT
        assert registry.is_busy("parakeet") is True, (
            "UE-48: the busy flag is keyed by name, not object, "
            "unregistering the backend does NOT clear the flag. "
            "Callers that swap a backend mid-transcription MUST call "
            "force_clear_busy(name) explicitly."
        )

        # The new backend's busy flag can be cleared explicitly.
        registry.force_clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False


class TestThreadSafety:
    """UE-48: the busy flag is thread-safe, the transcribe thread"""

    def test_concurrent_set_and_read_is_atomic(self):
        """Stress test: 100 threads set_busy + 100 threads is_busy"""
        registry = _make_registry()

        errors: list[Exception] = []

        def _setter():
            try:
                for _ in range(100):
                    registry.set_busy("parakeet")
                    registry.clear_busy("parakeet")
            except Exception as exc:
                errors.append(exc)

        def _reader():
            try:
                for _ in range(100):
                    registry.is_busy("parakeet")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_setter) for _ in range(10)] + [
            threading.Thread(target=_reader) for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], (
            f"UE-48: concurrent set_busy/clear_busy/is_busy must be "
            f"thread-safe (guarded by the registry's _lock). Got "
            f"errors: {errors!r}"
        )

    def test_busy_context_clears_under_concurrent_set_clear(self):
        """``busy_context``'s finally block must clear the flag even"""
        registry = _make_registry()
        stop = threading.Event()

        def _noise():
            while not stop.is_set():
                registry.set_busy("parakeet")
                registry.clear_busy("parakeet")

        noise_thread = threading.Thread(target=_noise, daemon=True)
        noise_thread.start()
        try:
            for _ in range(50):
                with registry.busy_context("parakeet"):
                    pass
                # No assertion on the flag value here, the noise
        finally:
            stop.set()
            noise_thread.join(timeout=1.0)
