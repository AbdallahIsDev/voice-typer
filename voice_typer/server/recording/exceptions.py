"""Exception types for the recording pipeline (re-exported from the package)."""


class RecordingError(RuntimeError):
    """Base for recording-pipeline exceptions."""

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
        """JSON-serializable structured fields for the IPC error envelope."""
        return {
            "message": str(self.args[0]) if self.args else "",
            "source_rate": self.source_rate,
            "target_rate": self.target_rate,
        }


class ResampleError(RecordingError):
    """Raised when audio cannot be resampled to the target sample rate."""


class ResampleUnavailableError(RecordingError):
    """Raised when scipy.signal.resample_poly is unavailable.

    Callers may fall back to linear interp after catching this type.
    """


ResampleUnavailable = ResampleUnavailableError
