"""``AsrBackendRegistry.load_active``."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from voice_typer.server.asr_registry import AsrBackendRegistry


class _Config:
    """Minimal config stub for the registry tests."""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend
        self.model_size = asr_backend
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False


def _make_registry(*, asr_backend: str = "parakeet") -> tuple[AsrBackendRegistry, MagicMock]:
    """Build a registry with a single registered mock backend whose"""
    registry = AsrBackendRegistry(_Config(asr_backend))
    backend = MagicMock()
    backend.is_loaded = False
    registry.register(asr_backend, backend)
    return registry, backend


class TestLoadActiveDisabledGate:
    """``_disabled_backends``, mirrors ``load_with_fallback``'s"""

    def test_load_active_returns_none_when_active_backend_disabled(self):
        """When the active backend is in ``_disabled_backends``,"""
        registry, _ = _make_registry()
        # Simulate the circuit breaker tripping.
        registry._disabled_backends.add("parakeet")

        result = registry.load_active(progress_callback=lambda msg: None)

        assert result is None, (
            "OI-15: load_active must return None when the active backend "
            "is in _disabled_backends. Pre-fix, it bypassed the "
            "_is_disabled gate and silently re-attempted the load."
        )

    def test_load_active_does_not_call_backend_load_when_disabled(self):
        """When disabled, ``load_active`` must NOT call"""
        registry, backend = _make_registry()
        registry._disabled_backends.add("parakeet")

        registry.load_active(progress_callback=lambda msg: None)

        assert backend.load.call_count == 0, (
            "OI-15: load_active must NOT call backend.load() when the "
            "active backend is disabled. Pre-fix, it would attempt the "
            "load and on success _record_success would discard the "
            "backend from _disabled_backends, silently re-enabling a "
            "user-disabled backend."
        )

    def test_load_active_does_not_call_record_success_when_disabled(self):
        """When disabled, ``load_active`` must NOT call"""
        registry, _ = _make_registry()
        registry._disabled_backends.add("parakeet")

        with (
            patch.object(registry, "_record_success") as mock_success,
            patch.object(registry, "_record_failure") as mock_failure,
        ):
            registry.load_active(progress_callback=lambda msg: None)

        assert not mock_success.called, (
            "OI-15: load_active must NOT call _record_success when the "
            "active backend is disabled, _record_success discards the "
            "backend from _disabled_backends, silently re-enabling it."
        )
        assert not mock_failure.called, (
            "OI-15: load_active must NOT call _record_failure when the "
            "active backend is disabled, the gate returns before any "
            "load attempt, so there's no failure to record."
        )

    def test_load_active_does_not_clear_disabled_state(self):
        """``_disabled_backends``)."""
        registry, _ = _make_registry()
        registry._disabled_backends.add("parakeet")
        assert registry._is_disabled("parakeet")

        registry.load_active(progress_callback=lambda msg: None)

        assert registry._is_disabled("parakeet"), (
            "OI-15: load_active must NOT clear the disabled state. "
            "Pre-fix, a successful load would call _record_success which "
            "discards the backend from _disabled_backends, silently "
            "re-enabling it."
        )

    def test_load_active_logs_warning_when_disabled(self):
        """The disabled-gate path must emit a warning so the operator"""
        registry, _ = _make_registry()
        registry._disabled_backends.add("parakeet")

        with patch("voice_typer.server.asr_registry.log") as mock_log:
            registry.load_active(progress_callback=lambda msg: None)

        warning_calls = [c for c in mock_log.warning.call_args_list if "disabled" in str(c).lower()]
        assert warning_calls, (
            "OI-15: load_active must log a warning mentioning 'disabled' "
            "when the gate fires, so operators can distinguish a "
            "disabled-backend refusal from a silent no-op."
        )


class TestLoadActiveNotDisabled:
    """The disabled-gate must NOT block the legitimate load path. When"""

    def test_load_active_proceeds_when_not_disabled(self):
        """When the active backend is NOT disabled, ``load_active``"""
        registry, backend = _make_registry()
        assert not registry._is_disabled("parakeet")

        result = registry.load_active(progress_callback=lambda msg: None)

        backend.load.assert_called_once()
        assert result is backend

    def test_load_active_calls_record_success_when_not_disabled(self):
        """When NOT disabled and load succeeds, ``load_active`` must"""
        registry, _ = _make_registry()
        # Pre-seed a non-tripping failure count to verify _record_success
        registry._failure_counts["parakeet"] = 1

        registry.load_active(progress_callback=lambda msg: None)

        assert registry.failure_count("parakeet") == 0, (
            "OI-15: on the happy path (not disabled, load succeeds), "
            "load_active must call _record_success to reset the failure "
            "counter. The disabled gate must not block this."
        )


class TestLoadActiveAfterResetFailures:
    """The disabled gate must not block legitimate circuit-breaker"""

    def test_load_active_works_after_reset_failures(self):
        """
        After ``reset_failures(name)`` clears the disabled state,
        ``load_active`` must proceed normally (the gate must not block
        """
        registry, backend = _make_registry()
        registry._disabled_backends.add("parakeet")
        registry._failure_counts["parakeet"] = registry._MAX_CONSECUTIVE_FAILURES
        assert registry._is_disabled("parakeet")

        # Documented recovery path.
        registry.reset_failures("parakeet")
        assert not registry._is_disabled("parakeet")

        result = registry.load_active(progress_callback=lambda msg: None)

        backend.load.assert_called_once()
        assert result is backend

    def test_load_active_blocked_for_one_disabled_but_not_others(self):
        """If backend A is disabled but backend B is not, the gate must"""
        registry = AsrBackendRegistry(_Config(asr_backend="parakeet"))
        parakeet = MagicMock()
        parakeet.is_loaded = True
        whisper = MagicMock()
        whisper.is_loaded = True
        registry.register("parakeet", parakeet)
        registry.register("whisper", whisper)

        # Disable parakeet only.
        registry._disabled_backends.add("parakeet")
        assert registry._is_disabled("parakeet")
        assert not registry._is_disabled("whisper")

        result = registry.load_active(progress_callback=lambda msg: None)
        assert result is None
        parakeet.load.assert_not_called()

        registry._config.asr_backend = "whisper"
        result = registry.load_active(progress_callback=lambda msg: None)
        assert result is whisper
        whisper.load.assert_called_once()


class TestLoadActiveDisabledGateSource:
    """accidentally removes it."""

    def test_load_active_source_contains_is_disabled_check(self):
        src = inspect.getsource(AsrBackendRegistry.load_active)
        assert "_is_disabled" in src, (
            "OI-15: load_active must call _is_disabled(self.active_name) "
            "at the top of the method. The gate appears to have been "
            "removed."
        )
        assert "return None" in src, (
            "OI-15: load_active must `return None` when the active backend is disabled (the gate's early-exit)."
        )

    def test_load_active_gate_before_get_active(self):
        """
        The ``_is_disabled`` gate must come BEFORE backend resolution —
        NAME (``self.get(``): ``load_active`` must NOT route through
        """
        src = inspect.getsource(AsrBackendRegistry.load_active)
        gate_idx = src.find("_is_disabled")
        resolve_idx = src.find("self.get(")
        assert gate_idx != -1 and resolve_idx != -1, (
            "OI-15: load_active must contain both _is_disabled and self.get(, one is missing."
        )
        assert "self.get_active()" not in src, (
            "load_active must resolve the backend by name (self.get), not "
            "via the get_active() readiness filter (fail-loud None for "
            "unloaded backends would brick every load path)."
        )
        assert gate_idx < resolve_idx, (
            "OI-15: the _is_disabled gate must come BEFORE backend "
            "resolution in load_active. If resolution runs first, a "
            "successful backend.load() would execute before the gate fires."
        )
