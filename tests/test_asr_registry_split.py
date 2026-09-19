"""focused tests for the asr_registry split ."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from voice_typer.server.asr.busy_flag import BusyFlag
from voice_typer.server.asr.circuit_breaker import CircuitBreaker
from voice_typer.server.asr.registry import (
    RegistryCore,
)
from voice_typer.server.asr_registry import AsrBackendRegistry


class _Config:
    """Minimal config stub for registry tests."""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend
        self.model_size = "tiny.en"
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False
        self.disabled_backends: list[str] = []


def _make_registry(*, asr_backend: str = "parakeet") -> AsrBackendRegistry:
    """Construct a registry with a single registered mock backend."""
    registry = AsrBackendRegistry(_Config(asr_backend))
    backend = MagicMock()
    backend.is_loaded = True
    registry.register(asr_backend, backend)
    return registry


class TestSplitStructure:
    """the split produces three focused modules + a facade."""

    def test_asr_backend_registry_is_subclass_of_registry_core(self):
        """The facade must subclass ``RegistryCore`` so inherited"""
        assert issubclass(AsrBackendRegistry, RegistryCore), (
            "AsrBackendRegistry must subclass RegistryCore so the "
            "core methods are inherited and patch.object on the facade "
            "instance intercepts calls from inherited methods."
        )

    def test_facade_composes_circuit_breaker(self):
        """The facade must compose a ``CircuitBreaker`` instance"""
        registry = _make_registry()
        assert isinstance(registry._breaker, CircuitBreaker), (
            "AsrBackendRegistry must compose a CircuitBreaker "
            "(accessible via self._breaker) so the breaker state is "
            "shared with the registry via the self._lock."
        )

    def test_facade_composes_busy_flag(self):
        """The facade must compose a ``BusyFlag`` instance"""
        registry = _make_registry()
        assert isinstance(registry._busy, BusyFlag), (
            "AsrBackendRegistry must compose a BusyFlag "
            "(accessible via self._busy) so the busy state is shared "
            "with the registry via the self._lock."
        )

    def test_breaker_and_busy_share_lock_with_registry(self):
        """The breaker and busy flag must share the same ``self._lock``"""
        registry = _make_registry()
        assert registry._breaker._lock is registry._lock, (
            "CircuitBreaker must share the registry's lock so "
            "load_with_fallback's dict reads cannot race with "
            "_record_failure's disabled-set mutation."
        )
        assert registry._busy._lock is registry._lock, (
            "BusyFlag must share the registry's lock so "
            "transcribe_with_fallback's busy-flag set cannot race with "
            "unload's busy-check."
        )

    def test_all_modules_independently_importable(self):
        """Each split module must be independently importable (no"""
        # If we got here, the imports at the top of this file succeeded.
        import importlib

        importlib.import_module("voice_typer.server.asr.registry")
        importlib.import_module("voice_typer.server.asr.circuit_breaker")
        importlib.import_module("voice_typer.server.asr.busy_flag")
        importlib.import_module("voice_typer.server.asr_registry")


class TestPublicApiPreservation:
    """every public API name + signature must be preserved."""

    EXPECTED_METHODS = {
        # Core CRUD
        "register",
        "unregister",
        "get",
        "get_active",
        "active_name",
        "available_backends",
        "create",
        "unload",
        # Load orchestration
        "load_active",
        "load_with_fallback",
        "transcribe_with_fallback",
        # Circuit breaker
        "_is_disabled",
        "failure_count",
        "reset_failures",
        "_record_success",
        "_record_failure",
        "_persist_disabled",
        "_fire_last_resort_subscribers",
        # Busy flag
        "is_busy",
        "set_busy",
        "clear_busy",
        "busy_context",
        "force_clear_busy",
        # Subscriber management
        "on_backend_disabled",
        "add_backend_disabled_subscriber",
        "remove_backend_disabled_subscriber",
        "on_last_resort",
        "add_last_resort_subscriber",
        "remove_last_resort_subscriber",
    }

    EXPECTED_STATE_ATTRS = {
        "_disabled_backends",
        "_failure_counts",
        "_last_resort_notified",
        "_on_backend_disabled_subscribers",
        "_on_last_resort_subscribers",
        "_busy_backends",
        "_MAX_CONSECUTIVE_FAILURES",
    }

    def test_all_expected_methods_exist(self):
        """Every method that existed on the pre-split"""
        for method_name in self.EXPECTED_METHODS:
            assert hasattr(AsrBackendRegistry, method_name), (
                f"AsrBackendRegistry must preserve the public method "
                f"'{method_name}' (existed on the pre-split registry)."
            )

    def test_all_expected_state_attrs_exist(self):
        """Every state attribute that tests read/mutate must still be"""
        registry = _make_registry()
        for attr_name in self.EXPECTED_STATE_ATTRS:
            assert hasattr(registry, attr_name), (
                f"AsrBackendRegistry must expose the state attribute "
                f"'{attr_name}' so existing tests that read/mutate it "
                f"continue to work."
            )

    def test_typed_contracts_re_exported(self):
        """The typed contracts (``AsrBackend``, ``ConfigProtocol``,"""
        from voice_typer.server.asr_registry import (
            AsrBackend,
            ConfigProtocol,
            ProgressCallback,
        )

        assert AsrBackend is not None
        assert ConfigProtocol is not None
        assert ProgressCallback is not None

    def test_subscriber_types_re_exported(self):
        """The subscriber callback types must be re-exported from"""
        from voice_typer.server.asr_registry import (
            BackendDisabledCallback,
            LastResortCallback,
        )

        assert BackendDisabledCallback is not None
        assert LastResortCallback is not None


class TestStateDelegation:
    """the facade's properties delegate to the breaker/busy helpers."""

    def test_disabled_backends_property_returns_breaker_set(self):
        """``registry._disabled_backends`` must return the breaker's"""
        registry = _make_registry()
        registry._disabled_backends.add("parakeet")
        assert "parakeet" in registry._breaker._disabled_backends, (
            "registry._disabled_backends must be the SAME set object "
            "as registry._breaker._disabled_backends so .add() mutations "
            "land on the real state."
        )

    def test_failure_counts_property_returns_breaker_dict(self):
        """``registry._failure_counts`` must return the breaker's"""
        registry = _make_registry()
        registry._failure_counts["parakeet"] = 5
        assert registry._breaker._failure_counts["parakeet"] == 5, (
            "registry._failure_counts must be the SAME dict object as registry._breaker._failure_counts."
        )

    def test_busy_backends_property_returns_busy_flag_set(self):
        """``registry._busy_backends`` must return the busy flag's"""
        registry = _make_registry()
        registry._busy_backends.add("parakeet")
        assert "parakeet" in registry._busy._busy_backends, (
            "registry._busy_backends must be the SAME set object as registry._busy._busy_backends."
        )

    def test_last_resort_notified_property_returns_breaker_value(self):
        """``registry._last_resort_notified`` must reflect the breaker's"""
        registry = _make_registry()
        assert registry._last_resort_notified is False
        registry._breaker._last_resort_notified = True
        assert registry._last_resort_notified is True, (
            "registry._last_resort_notified must delegate to registry._breaker._last_resort_notified."
        )


class TestPatchObjectContract:
    """``patch.object(registry, \"_record_success\")`` must patch"""

    def test_patch_record_success_intercepts_load_active(self):
        """``patch.object(registry, \"_record_success\")`` must intercept"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        backend.load.return_value = None

        with patch.object(registry, "_record_success") as mock_success:
            registry.load_active(progress_callback=lambda msg: None)

        assert mock_success.called, (
            "patch.object(registry, '_record_success') must intercept "
            "the call from load_active. If _record_success were defined on "
            "RegistryCore (not the facade), the patch on the facade instance "
            "would be shadowed by the class method."
        )

    def test_patch_record_success_intercepts_load_with_fallback(self):
        """``patch.object(registry, \"_record_success\")`` must intercept"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        backend.load.return_value = None

        with patch.object(registry, "_record_success") as mock_success:
            registry.load_with_fallback(progress_callback=lambda msg: None)

        assert mock_success.called, (
            "patch.object(registry, '_record_success') must intercept "
            "the call from load_with_fallback (inherited from RegistryCore). "
            "The MRO must resolve self._record_success on the facade instance."
        )

    def test_patch_record_failure_intercepts_load_active(self):
        """``patch.object(registry, \"_record_failure\")`` must intercept"""
        registry = _make_registry()
        backend = registry.get("parakeet")
        backend.load.side_effect = RuntimeError("simulated load failure")

        with patch.object(registry, "_record_failure") as mock_failure:
            registry.load_active(progress_callback=lambda msg: None)

        assert mock_failure.called, (
            "patch.object(registry, '_record_failure') must intercept the call from load_active on load failure."
        )


class TestFileSizeConstraint:
    """the largest resulting file must be ≤ 400 lines."""

    MAX_LINES = 400

    def _count_lines(self, module_path: str) -> int:
        """Count the number of lines in a Python module file."""
        import voice_typer.server.asr.busy_flag as bf_mod
        import voice_typer.server.asr.circuit_breaker as cb_mod
        import voice_typer.server.asr.registry as reg_mod
        import voice_typer.server.asr_registry as facade_mod

        modules = {
            "asr/registry.py": reg_mod.__file__,
            "asr/circuit_breaker.py": cb_mod.__file__,
            "asr/busy_flag.py": bf_mod.__file__,
            "asr_registry.py": facade_mod.__file__,
        }
        path = modules[module_path]
        with open(path) as f:
            return sum(1 for _ in f)

    def test_registry_py_under_400_lines(self):
        """``asr/registry.py`` must be ≤ 400 lines."""
        count = self._count_lines("asr/registry.py")
        assert count <= self.MAX_LINES, (
            f"asr/registry.py must be ≤ {self.MAX_LINES} lines "
            f"(got {count}). Compress docstrings or move methods to the "
            f"facade."
        )

    def test_circuit_breaker_py_under_400_lines(self):
        """``asr/circuit_breaker.py`` must be ≤ 400 lines."""
        count = self._count_lines("asr/circuit_breaker.py")
        assert count <= self.MAX_LINES, f"asr/circuit_breaker.py must be ≤ {self.MAX_LINES} lines (got {count})."

    def test_busy_flag_py_under_400_lines(self):
        """``asr/busy_flag.py`` must be ≤ 400 lines."""
        count = self._count_lines("asr/busy_flag.py")
        assert count <= self.MAX_LINES, f"asr/busy_flag.py must be ≤ {self.MAX_LINES} lines (got {count})."

    def test_asr_registry_py_under_400_lines(self):
        """``asr_registry.py`` (the facade) must be ≤ 400 lines."""
        count = self._count_lines("asr_registry.py")
        assert count <= self.MAX_LINES, f"asr_registry.py must be ≤ {self.MAX_LINES} lines (got {count})."

    def test_former_monolith_shrunk(self):
        """The former 1072-line ``asr_registry.py`` must have shrunk"""
        count = self._count_lines("asr_registry.py")
        assert count < 500, (
            f"asr_registry.py must have shrunk significantly from "
            f"the original 1072 lines (got {count}). The facade should be "
            f"a thin wrapper, not a re-implementation."
        )
