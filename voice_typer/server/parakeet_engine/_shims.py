"""Back-compat shim kept from the transformers era."""

from __future__ import annotations

import threading
from typing import Any


class TranscriptionBackendError(RuntimeError):
    """Raised when the ASR backend cannot produce a transcription."""


class _AbortStoppingCriteria:
    """Legacy ``transformers.StoppingCriteria`` shim, preserved for"""

    def __init__(self, abort_event: threading.Event) -> None:
        self._abort_event = abort_event

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:  # noqa: D401
        """Return True if generation should stop (abort signalled)."""
        return self._abort_event.is_set()
