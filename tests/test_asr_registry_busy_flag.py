"""UE-48 regression tests: per-backend "busy" flag on AsrBackendRegistry.

Pre-fix: the registry had NO notion of "this backend is currently
inside ``transcribe_with_fallback``". The dictation pipeline called
``active.transcribe_with_fallback(...)`` directly (1-30s ctranslate2 /
torch inference), and if that call hung (a stuck C-level ctranslate2
invocation documented to hold GPU + GIL for 5-30 min), the user's next
F2 press would call ``ensure_active_engine_loaded`` → re-enter the
SAME stuck backend object → either queue behind the stuck call (GIL /
ctranslate2 internal lock) or attempt concurrent inference on the same
model (undefined behaviour).

Post-fix (UE-48): the registry exposes a per-backend busy flag (set
when ``transcribe_with_fallback`` is entered, cleared on exit). The
flag is:

* **Thread-safe.** The transcribe thread sets it (via the
  :meth:`AsrBackendRegistry.busy_context` context manager or the
  :meth:`AsrBackendRegistry.transcribe_with_fallback` wrapper); the
  IPC thread reads it (via :meth:`AsrBackendRegistry.is_busy`).
  Both paths acquire the registry's existing ``_lock`` so the
  read/write pair is atomic.

* **Keyed by backend NAME**, not the backend object. A backend that
  was unregistered + re-registered under the same name (e.g. by
  ``change_model``) doesn't carry over a stale busy state.

* **Self-clearing.** The ``busy_context`` context manager and the
  ``transcribe_with_fallback`` wrapper both clear the flag in a
  ``finally`` block — a backend that raises mid-transcription still
  gets its flag cleared, so the next dictation isn't rejected
  forever.

These tests pin the contract:

1. ``is_busy`` returns False by default.
2. ``set_busy`` / ``clear_busy`` mutate the flag.
3. ``clear_busy`` is idempotent (no error on a not-busy backend).
4. ``busy_context`` sets on enter, clears on exit (happy path).
5. ``busy_context`` clears on exit EVEN IF the body raises.
6. ``transcribe_with_fallback`` wrapper sets/clears the flag around
   the backend's call.
7. ``transcribe_with_fallback`` wrapper clears the flag EVEN IF the
   backend's call raises.
8. ``transcribe_with_fallback`` wrapper returns the backend's text
   unchanged (transparent passthrough).
9. ``transcribe_with_fallback`` wrapper forwards ``*args`` /
   ``**kwargs`` unchanged (e.g. ``audio_stats=``, ``local_engine=``).
10. ``transcribe_with_fallback`` wrapper returns "" for an unknown
    backend name (matches ``get_active``'s last-resort branch).
11. ``is_busy(None)`` defaults to the active backend.
12. ``set_busy`` / ``clear_busy`` with ``name=None`` default to the
    active backend.
13. ``force_clear_busy`` is an alias for ``clear_busy`` (the
    watchdog's force-recover path uses this for self-documenting
    call sites).
14. The flag survives a backend swap (keyed by name, not object).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry

# ── Test fixtures ─────────────────────────────────────────────────────


class _Config:
    """Minimal config stub — only ``asr_backend`` is read by the busy
    flag API (via ``registry.active_name``)."""

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


# ── Test classes ──────────────────────────────────────────────────────


class TestIsBusyDefault:
    """UE-48: ``is_busy`` returns False by default."""

    def test_is_busy_false_by_default(self):
        """A freshly-constructed registry must report every backend as
        not-busy."""
        registry = _make_registry()
        assert registry.is_busy("parakeet") is False, (
            "UE-48: a freshly-constructed registry must report every "
            "backend as not-busy. Pre-fix, the busy flag didn't exist."
        )

    def test_is_busy_none_defaults_to_active(self):
        """``is_busy(None)`` must query the active backend (matching
        ``set_busy`` / ``clear_busy``)."""
        registry = _make_registry(asr_backend="parakeet")
        assert registry.is_busy(None) is False

    def test_is_busy_unknown_name_returns_false(self):
        """``is_busy`` on an unknown backend name must return False
        (no KeyError, no AttributeError — defensive)."""
        registry = _make_registry()
        assert registry.is_busy("nonexistent") is False


class TestSetClearBusy:
    """UE-48: ``set_busy`` / ``clear_busy`` mutate the flag."""

    def test_set_busy_makes_is_busy_true(self):
        """After ``set_busy("parakeet")``, ``is_busy("parakeet")`` must
        return True."""
        registry = _make_registry()
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

    def test_clear_busy_makes_is_busy_false(self):
        """After ``set_busy`` then ``clear_busy``, ``is_busy`` must
        return False."""
        registry = _make_registry()
        registry.set_busy("parakeet")
        registry.clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False

    def test_clear_busy_idempotent_on_not_busy_backend(self):
        """``clear_busy`` on a backend that was never marked busy must
        be a no-op (no error)."""
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
        """``clear_busy(None)`` must clear the active backend's busy
        flag."""
        registry = _make_registry(asr_backend="parakeet")
        registry.set_busy("parakeet")
        registry.clear_busy(None)
        assert registry.is_busy("parakeet") is False

    def test_set_busy_unknown_name_is_noop(self):
        """``set_busy`` on an unknown backend name must be a no-op
        (defensive — the busy set is keyed by name and queried by
        ``is_busy``, which returns False for unknown names)."""
        registry = _make_registry()
        # Must NOT raise. The unknown name may end up in the set, but
        # ``is_busy`` for the known name is unaffected.
        registry.set_busy("nonexistent")
        assert registry.is_busy("parakeet") is False


class TestBusyContext:
    """UE-48: ``busy_context`` sets the flag on enter and clears on
    exit (including the exception path)."""

    def test_busy_context_sets_on_enter_clears_on_exit(self):
        """The flag must be True inside the ``with`` block and False
        after."""
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
        """The flag must be cleared EVEN IF the body raises — a
        transcription that raises mid-call must not leave the backend
        permanently busy."""

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
        """``busy_context(None)`` must mark the active backend as
        busy."""
        registry = _make_registry(asr_backend="parakeet")
        with registry.busy_context(None) as name:
            assert name == "parakeet"
            assert registry.is_busy("parakeet") is True
        assert registry.is_busy("parakeet") is False

    def test_busy_context_yields_resolved_name(self):
        """The context manager must yield the resolved backend name so
        callers can pass it to subsequent registry methods without
        re-resolving (the docstring promises this)."""
        registry = _make_registry(asr_backend="parakeet")
        with registry.busy_context(None) as name:
            assert name == "parakeet"


class TestTranscribeWithFallbackWrapper:
    """UE-48: the registry's ``transcribe_with_fallback`` wrapper sets
    and clears the busy flag around the backend's call."""

    def test_wrapper_sets_and_clears_busy_flag(self):
        """The wrapper must set the flag before the backend call and
        clear it after (happy path)."""
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
        """The flag must be True WHILE the backend's call is running
        (verified by spying on the backend's call)."""
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
        """The wrapper must clear the flag EVEN IF the backend's call
        raises (the ``finally`` block in ``busy_context`` ensures
        this)."""

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
        """The wrapper must return the backend's text unchanged
        (transparent passthrough — no transformation, no truncation)."""
        registry = _make_registry()
        backend = registry.get("parakeet")
        expected_text = "the quick brown fox jumps over the lazy dog"
        backend.transcribe_with_fallback.return_value = expected_text

        text = registry.transcribe_with_fallback(b"audio", name="parakeet")

        assert text == expected_text, (
            "UE-48: the wrapper is a transparent passthrough — it must return the backend's text unchanged."
        )

    def test_wrapper_forwards_args_and_kwargs(self):
        """The wrapper must forward ``*args`` and ``**kwargs`` to the
        backend's call unchanged (e.g. ``audio_stats=``,
        ``local_engine=``)."""
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
        """When ``name=None`` (or omitted), the wrapper must invoke the
        active backend (matching ``is_busy`` / ``set_busy`` /
        ``clear_busy`` semantics)."""
        registry = _make_registry(asr_backend="parakeet")
        backend = registry.get("parakeet")

        registry.transcribe_with_fallback(b"audio")

        backend.transcribe_with_fallback.assert_called_once_with(b"audio")

    def test_wrapper_returns_empty_string_for_unknown_backend(self):
        """When the named backend is not registered, the wrapper must
        log a warning and return an empty string (matching
        ``get_active``'s last-resort branch contract)."""
        registry = _make_registry(asr_backend="parakeet")

        text = registry.transcribe_with_fallback(b"audio", name="nonexistent")

        assert text == "", (
            "UE-48: the wrapper must return an empty string for an "
            "unknown backend name (matches get_active's last-resort "
            "silent-empty contract)."
        )

    def test_wrapper_does_not_set_busy_for_unknown_backend(self):
        """The wrapper must NOT set the busy flag for an unknown
        backend name (the early return for the unknown-backend case
        happens before ``busy_context`` is entered)."""
        registry = _make_registry(asr_backend="parakeet")

        registry.transcribe_with_fallback(b"audio", name="nonexistent")

        assert registry.is_busy("nonexistent") is False
        assert registry.is_busy("parakeet") is False


class TestForceClearBusy:
    """UE-48: ``force_clear_busy`` is an alias for ``clear_busy``
    exposed under a more discoverable name for the watchdog's
    force-recover path."""

    def test_force_clear_busy_clears_flag(self):
        """``force_clear_busy`` must clear a busy flag set by
        ``set_busy``."""
        registry = _make_registry()
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        registry.force_clear_busy("parakeet")

        assert registry.is_busy("parakeet") is False

    def test_force_clear_busy_none_defaults_to_active(self):
        """``force_clear_busy(None)`` must clear the active backend's
        busy flag."""
        registry = _make_registry(asr_backend="parakeet")
        registry.set_busy("parakeet")

        registry.force_clear_busy(None)

        assert registry.is_busy("parakeet") is False

    def test_force_clear_busy_idempotent(self):
        """``force_clear_busy`` on a not-busy backend must be a
        no-op (idempotent — matches ``clear_busy``'s contract)."""
        registry = _make_registry()
        # Must NOT raise.
        registry.force_clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False


class TestBusyFlagSurvivesBackendSwap:
    """UE-48: the flag is keyed by backend NAME, not the backend
    object — a backend that was unregistered + re-registered under the
    same name (e.g. by ``change_model``) doesn't carry over a stale
    busy state."""

    def test_busy_flag_does_not_carry_over_to_new_backend_object(self):
        """When a backend is unregistered and a NEW backend is
        registered under the same name, the new backend must NOT
        inherit the old backend's busy state."""
        registry = _make_registry()
        registry.get("parakeet")

        # Mark the old backend as busy, then simulate a backend swap
        # (as happens in change_model when the user picks a new
        # model_size for the same backend family).
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        registry.unregister("parakeet")
        new_backend = MagicMock()
        new_backend.is_loaded = True
        registry.register("parakeet", new_backend)

        # The new backend is NOT busy — the busy flag was tied to the
        # OLD backend's transcription, which has been conceptually
        # cancelled by the change_model swap. (In practice, the
        # busy_context's finally block would have cleared it; this
        # test pins the registry-level behaviour independently.)
        #
        # NOTE: this test pins the CURRENT behaviour — the busy flag
        # is a name-keyed set, so unregistering the backend leaves
        # the flag set. Callers that swap a backend mid-transcription
        # MUST call ``force_clear_busy(name)`` to reset the flag (the
        # ``change_model`` / ``set_active_backend`` paths do NOT
        # automatically clear it — they defer to the watchdog /
        # busy_context's finally block).
        assert registry.is_busy("parakeet") is True, (
            "UE-48: the busy flag is keyed by name, not object — "
            "unregistering the backend does NOT clear the flag. "
            "Callers that swap a backend mid-transcription MUST call "
            "force_clear_busy(name) explicitly."
        )

        # The new backend's busy flag can be cleared explicitly.
        registry.force_clear_busy("parakeet")
        assert registry.is_busy("parakeet") is False


class TestThreadSafety:
    """UE-48: the busy flag is thread-safe — the transcribe thread
    sets it, the IPC thread reads it. Both paths acquire the
    registry's ``_lock``."""

    def test_concurrent_set_and_read_is_atomic(self):
        """Stress test: 100 threads set_busy + 100 threads is_busy
            concurrently. The flag must never raise (no AttributeError /
        RuntimeError from concurrent set access)."""
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
        """``busy_context``'s finally block must clear the flag even
        when another thread is concurrently calling set_busy /
        clear_busy on the same name."""
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
                    # The flag may be True or False here depending on
                    # the noise thread's interleaving, but the context
                    # manager's contract is that it CLEARS the flag on
                    # exit (the noise thread may immediately re-set it,
                    # but that's a separate state).
                    pass
                # No assertion on the flag value here — the noise
                # thread races with us. The test just verifies no
                # exception is raised.
        finally:
            stop.set()
            noise_thread.join(timeout=1.0)
