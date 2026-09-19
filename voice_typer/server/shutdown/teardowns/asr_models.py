"""Teardown helper for the active ASR backend + GPU memory release."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_asr_models(controller) -> None:
    """Unload the active ASR backend + release GPU memory before exit."""
    # Resolve helpers from :mod:`voice_typer.server.shutdown_controller` at
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
