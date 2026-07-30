"""Exception types raised by the recording pipeline.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  Both exception classes are re-exported from
``voice_typer.server.recording`` (the package ``__init__.py``) so
existing imports ``from voice_typer.server.recording import
ResampleError, ResampleUnavailable`` keep working unchanged.

XE-14-C: the two resample exceptions now share a common
:class:`RecordingError` base (itself a ``RuntimeError`` subclass) so
the IPC handler ``_respond_with_error`` isinstance ladder can map the
whole recording-pipeline family to dedicated IPC error codes (see
``ErrorCodes.RECORDING_RESAMPLE_FAILED`` /
``ErrorCodes.RECORDING_RESAMPLE_UNAVAILABLE`` in
``voice_typer/server/ipc/validation.py``) instead of collapsing them
into the generic ``server.internal_error`` toast. The base also
carries optional ``source_rate`` / ``target_rate`` structured fields
and a ``to_dict()`` helper so the IPC envelope can surface the
sample-rate pair that triggered the failure (useful for the renderer's
"audio pipeline misconfiguration" diagnostics UI).
"""


class RecordingError(RuntimeError):
    """Base for recording-pipeline exceptions (XE-14-C).

    Subclasses (:class:`ResampleError`,
    :class:`ResampleUnavailableError`) carry the semantic category
    (resample-failed vs scipy-unavailable) so the IPC layer can
    ``isinstance``-narrow to "something went wrong in the recording
    pipeline" without matching the message string.

    The optional ``source_rate`` / ``target_rate`` keyword arguments
    capture the sample-rate pair that triggered the failure (when
    applicable) so telemetry sinks and the renderer's diagnostics UI
    can drive their behavior off typed fields instead of regex-matching
    the message string. Both default to ``None`` for raise sites that
    don't have a meaningful rate pair (e.g. scipy-unavailable fires
    before any rate conversion is attempted).
    """

    def __init__(
        self,
        message: str = "",
        *,
        source_rate: int | None = None,
        target_rate: int | None = None,
    ) -> None:
        super().__init__(message)
        self.source_rate = source_rate
        self.target_rate = target_rate

    def to_dict(self) -> dict[str, int | str | None]:
        """Return the structured fields as a JSON-serializable dict.

        Mirrors the ``to_dict()`` shape on
        :class:`voice_typer.server.asr_errors.ConsentRequiredError` so
        the IPC layer can stamp the structured fields on the error
        envelope with a single ``data.update(exc.to_dict())`` call.
        """
        return {
            "message": str(self.args[0]) if self.args else "",
            "source_rate": self.source_rate,
            "target_rate": self.target_rate,
        }


class ResampleError(RecordingError):
    """Raised when audio cannot be resampled to the target sample rate.

    ERR-001: Previously the resample fallback returned the native-rate
    audio silently, which produced garbage transcriptions because the
    streaming path assumed the configured sample rate. Callers must
    catch this exception and decide how to handle the failure (skip
    the chunk, abort the dictation, or notify the user).

    XE-14-C: now inherits from :class:`RecordingError` (rather than
    directly from ``RuntimeError``) so the IPC handler
    ``_respond_with_error`` isinstance ladder can map the whole
    recording-pipeline family to a dedicated IPC error code.
    """


class ResampleUnavailableError(RecordingError):
    """Raised when scipy.signal.resample_poly is unavailable.

    ARCH-033: the 3-tier fallback (scipy → linear interp → native)
    previously failed silently at each tier. We now raise this typed
    exception at the scipy tier so the caller knows the high-quality
    path is unavailable and can decide whether to use linear interp.

    XE-14-C: now inherits from :class:`RecordingError` (rather than
    directly from ``RuntimeError``) so the IPC handler
    ``_respond_with_error`` isinstance ladder can map the whole
    recording-pipeline family to a dedicated IPC error code.
    """


# Backward-compatibility alias. The class was renamed from
# ``ResampleUnavailable`` to ``ResampleUnavailableError`` to match the
# project's exception naming convention, but several historical test
# modules still import the old name. Re-exporting the alias keeps those
# tests working without requiring a coordinated rename across the test
# suite. The alias is part of the module's public surface.
ResampleUnavailable = ResampleUnavailableError
