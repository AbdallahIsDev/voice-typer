"""Exception types raised by the recording pipeline.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  Both exception classes are re-exported from
``voice_typer.server.recording`` (the package ``__init__.py``) so
existing imports ``from voice_typer.server.recording import
ResampleError, ResampleUnavailable`` keep working unchanged.
"""


class ResampleError(RuntimeError):
    """Raised when audio cannot be resampled to the target sample rate.

    ERR-001: Previously the resample fallback returned the native-rate
    audio silently, which produced garbage transcriptions because the
    streaming path assumed the configured sample rate. Callers must
    catch this exception and decide how to handle the failure (skip
    the chunk, abort the dictation, or notify the user).
    """


class ResampleUnavailableError(RuntimeError):
    """Raised when scipy.signal.resample_poly is unavailable.

    ARCH-033: the 3-tier fallback (scipy → linear interp → native)
    previously failed silently at each tier. We now raise this typed
    exception at the scipy tier so the caller knows the high-quality
    path is unavailable and can decide whether to use linear interp.
    """


# Backward-compatibility alias. The class was renamed from
# ``ResampleUnavailable`` to ``ResampleUnavailableError`` to match the
# project's exception naming convention, but several historical test
# modules still import the old name. Re-exporting the alias keeps those
# tests working without requiring a coordinated rename across the test
# suite. The alias is part of the module's public surface.
ResampleUnavailable = ResampleUnavailableError
