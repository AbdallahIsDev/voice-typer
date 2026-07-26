"""AC-6 regression tests: ``AsrBackendRegistry.load_active`` exception
path mirrors :meth:`load_with_fallback` (circuit breaker + unload-on-failure).

Pre-fix: ``load_active``'s ``except Exception`` branch caught the
exception, logged it, and returned None. It did NOT call
``self._record_failure(self.active_name)`` (circuit breaker) NOR
``backend.unload()`` (resource cleanup). A user repeatedly retrying
a failed ``change_model`` (e.g. F2 hotkey on a broken Parakeet
install) called ``load_active`` each time — failure counter never
incremented (backend never auto-disabled) and partially-allocated
torch tensors / CUDA contexts from each failed ``from_pretrained``
were never released (GPU memory accumulation across retries).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.asr_registry import AsrBackendRegistry


def _make_registry_with_failing_backend(*, backend_name: str = "parakeet") -> tuple[AsrBackendRegistry, MagicMock]:
    """Construct a registry whose active backend's ``load`` raises."""
    failing_engine = MagicMock()
    failing_engine.is_loaded = False
    failing_engine.load.side_effect = RuntimeError("parakeet CUDA OOM")

    class _Config:
        asr_backend = backend_name
        model_size = backend_name
        device = "cpu"
        language = "en"
        beam_size = 1
        best_of = 1
        condition_on_previous_text = False

    registry = AsrBackendRegistry(_Config())
    registry.register(backend_name, failing_engine)
    return registry, failing_engine


class TestAC6LoadActiveCircuitBreaker:
    """AC-6: ``load_active`` exception path must increment the failure
    counter (circuit breaker), mirroring ``load_with_fallback``."""

    def test_load_active_increments_failure_counter_on_exception(self):
        """When ``backend.load`` raises, ``load_active`` must call
        ``_record_failure(active_name)`` so the failure counter
        increments (mirrors ``load_with_fallback``'s primary-backend
        failure path)."""
        registry, _ = _make_registry_with_failing_backend()

        assert registry.failure_count("parakeet") == 0
        registry.load_active(progress_callback=lambda msg: None)
        assert registry.failure_count("parakeet") == 1, (
            "AC-6: load_active must increment the failure counter on "
            "exception (mirror load_with_fallback). Pre-fix, the counter "
            "never incremented so the circuit breaker never tripped on "
            "repeated load_active failures."
        )

    def test_load_active_disables_backend_after_max_failures(self):
        """After ``_MAX_CONSECUTIVE_FAILURES`` load_active failures,
        the backend must be added to ``_disabled_backends`` (circuit
        breaker tripped). Pre-fix, this never happened for load_active."""
        registry, _ = _make_registry_with_failing_backend()
        assert not registry._is_disabled("parakeet")

        for _ in range(registry._MAX_CONSECUTIVE_FAILURES):
            registry.load_active(progress_callback=lambda msg: None)

        assert registry._is_disabled("parakeet"), (
            "AC-6: load_active must trip the circuit breaker after "
            "_MAX_CONSECUTIVE_FAILURES consecutive failures (mirror "
            "load_with_fallback). Pre-fix, load_active never disabled "
            "a persistently-failing backend."
        )

    def test_load_active_resets_failure_counter_on_success(self):
        """A successful ``load_active`` call must reset the failure
        counter to 0 (mirrors ``load_with_fallback``'s success path)."""
        # First call fails, second succeeds.
        call_count = {"n": 0}
        engine = MagicMock()
        engine.is_loaded = False

        def fake_load(progress_callback=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call fails")

        engine.load.side_effect = fake_load

        class _Config:
            asr_backend = "parakeet"
            model_size = "parakeet"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        registry.register("parakeet", engine)

        registry.load_active(progress_callback=lambda msg: None)
        assert registry.failure_count("parakeet") == 1

        registry.load_active(progress_callback=lambda msg: None)
        assert registry.failure_count("parakeet") == 0, (
            "AC-6: load_active must reset the failure counter to 0 on "
            "successful load (mirror load_with_fallback's _record_success "
            "call)."
        )


class TestAC6LoadActiveUnloadOnFailure:
    """AC-6 / MEM-01: ``load_active`` exception path must call
    ``backend.unload()`` (resource cleanup), mirroring
    ``load_with_fallback``."""

    def test_load_active_calls_unload_on_failure(self):
        """When ``backend.load`` raises, ``load_active`` must call
        ``backend.unload()`` to release partially-allocated resources
        (torch tensors, CUDA contexts, model weights)."""
        registry, engine = _make_registry_with_failing_backend()

        registry.load_active(progress_callback=lambda msg: None)

        (
            engine.unload.assert_called_once(),
            (
                "AC-6 / MEM-01: load_active must call backend.unload() on "
                "exception to release partially-allocated resources (mirror "
                "load_with_fallback). Pre-fix, partially-allocated torch "
                "tensors from each failed from_pretrained were never released "
                "across retries."
            ),
        )

    def test_load_active_swallows_unload_failure(self):
        """If ``backend.unload()`` itself raises, ``load_active`` must
        swallow the unload exception (log a warning) and still return
        None — NOT propagate the unload exception to the caller."""
        registry, engine = _make_registry_with_failing_backend()
        # Make unload also fail.
        engine.unload.side_effect = RuntimeError("unload failed")

        # Must NOT raise — load_active should swallow the unload exception.
        result = registry.load_active(progress_callback=lambda msg: None)
        assert result is None, (
            "AC-6: load_active must return None (not raise) when both "
            "load() and unload() fail. The unload failure must be logged "
            "but not propagated."
        )
        # unload was called even though it raised.
        engine.unload.assert_called_once()

    def test_load_active_returns_none_on_failure(self):
        """``load_active`` must return None on backend.load() failure."""
        registry, _ = _make_registry_with_failing_backend()
        result = registry.load_active(progress_callback=lambda msg: None)
        assert result is None

    def test_load_active_returns_backend_on_success(self):
        """Sanity: ``load_active`` returns the backend on success."""
        engine = MagicMock()
        engine.is_loaded = False

        class _Config:
            asr_backend = "parakeet"
            model_size = "parakeet"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        registry.register("parakeet", engine)

        result = registry.load_active(progress_callback=lambda msg: None)
        assert result is engine
