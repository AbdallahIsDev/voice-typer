"""ASR backend registry — split into focused modules.

This package splits the former 1072-line ``asr_registry.py`` into three
focused modules so each concern has a single, narrow owner:

- :mod:`voice_typer.server.asr.registry` — the typed contracts
  (``AsrBackend`` / ``ConfigProtocol`` Protocols + ``ProgressCallback``
  type alias) and the core backend CRUD: ``register`` / ``unregister`` /
  ``get`` / ``active_name`` / ``available_backends`` /
  ``get_active`` / ``create`` / ``load_active`` / ``load_with_fallback`` /
  ``unload`` / ``transcribe_with_fallback``.
- :mod:`voice_typer.server.asr.circuit_breaker` — the per-backend
  failure-counter / disabled-set state + subscriber notification
  (``_record_success`` / ``_record_failure`` / ``_is_disabled`` /
  ``_persist_disabled`` / ``failure_count`` / ``reset_failures`` +
  ``on_backend_disabled`` / ``on_last_resort`` subscribers +
  last-resort one-shot latch).
- :mod:`voice_typer.server.asr.busy_flag` — the per-backend busy flag
  (``is_busy`` / ``set_busy`` / ``clear_busy`` / ``busy_context`` /
  ``force_clear_busy``) used by the dictation watchdog.

The public :class:`voice_typer.server.asr_registry.AsrBackendRegistry`
facade composes the three helpers and preserves the original public API
names + signatures, so every existing import of
``from voice_typer.server.asr_registry import AsrBackendRegistry``
continues to work unchanged.
"""

from voice_typer.server.asr.busy_flag import BusyFlag
from voice_typer.server.asr.circuit_breaker import (
    BackendDisabledCallback,
    CircuitBreaker,
    LastResortCallback,
)
from voice_typer.server.asr.registry import (
    AsrBackend,
    ConfigProtocol,
    ProgressCallback,
    RegistryCore,
)

__all__ = [
    "AsrBackend",
    "BackendDisabledCallback",
    "BusyFlag",
    "CircuitBreaker",
    "ConfigProtocol",
    "LastResortCallback",
    "ProgressCallback",
    "RegistryCore",
]
