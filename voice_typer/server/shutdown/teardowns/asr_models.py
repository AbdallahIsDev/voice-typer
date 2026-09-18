"""Teardown helper for the active ASR backend + GPU memory release.

Phase 4.5 (OI-36), extracted verbatim from
:meth:`ShutdownController._teardown_asr_models`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_asr_models(controller) -> None:
    """Unload the active ASR backend + release GPU memory before exit.

    Pre-fix, ``shutdown_controller._do_cleanup`` ran 14 parallel
    teardown helpers. NONE of them touched ``app.models`` /
    ``app.models.registry``. ``asr_registry.unload()`` was only
    invoked on (a) backend load failure and (b) ``app._change_model()``.
    On a normal quit / restart_app / atexit, the active Parakeet /
    Whisper backend's ``unload()`` was never called. Combined with
    (host force-kills after 2-6s), the Python process was
    SIGKILLed before Python's GC could drop the model references —
    meaning GPU release / context teardown never ran. On GPU systems
    this leaked GPU memory across rapid restart cycles; on CPU-only
    Whisper ~1-3GB RSS stayed resident longer than necessary.

    This helper is placed FIRST in the parallel batch so the
    (potentially slow) GPU context teardown starts as early as
    possible. ``registry.unload()`` is idempotent and already wraps
    every per-backend ``backend.unload()`` in try/except, so a
    single failing backend doesn't abort the others.
    ``release_gpu_memory()`` is best-effort and no-op-safe, so it is a
    no-op on CPU-only hosts.
    """
    # Resolve helpers from :mod:`voice_typer.server.shutdown_controller` at
    # call time so tests that monkeypatch ``shutdown_controller._run_with_timeout``
    # (and the module's ``TIMEOUT`` sentinel / logger) are observed, same lazy
    # lookup convention as the other teardown modules. The unload is
    # wrapped in ``_run_with_timeout("asr_registry.unload", ..., timeout=8.0)``
    # so a hung backend unload can't stall the whole shutdown; on TIMEOUT we
    # still release GPU memory (the cache clear is independent of the unload).
    import voice_typer.server.shutdown_controller as _sc

    try:
        registry = getattr(controller._app.models, "registry", None)
        if registry is not None and hasattr(registry, "unload"):
            result = _sc._run_with_timeout(
                "asr_registry.unload",
                registry.unload,
                timeout=8.0,
            )
            if result is _sc.TIMEOUT:
                # Log at WARNING (the GPU cache may not be fully
                # released), we still proceed to release_gpu_memory() below.
                _sc.log.warning(
                    "[CLEANUP] asr_registry.unload() did not finish within 8s, "
                    "proceeding to release_gpu_memory (GPU cache may not be fully released)"
                )
    except Exception:
        log.debug("[CLEANUP] asr_registry.unload() failed", exc_info=True)
    try:
        from voice_typer.server.asr_utils import release_gpu_memory

        release_gpu_memory()
    except Exception:
        log.debug(
            "[CLEANUP] release_gpu_memory() failed (non-fatal)",
            exc_info=True,
        )


__all__ = ["teardown_asr_models"]
