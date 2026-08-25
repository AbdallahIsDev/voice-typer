"""Back-compat shim kept from the transformers era."""

from __future__ import annotations

import threading
from typing import Any


class TranscriptionBackendError(RuntimeError):
    """Raised when the ASR backend cannot produce a transcription.

    ``transcribe_with_fallback`` raises this on CPU fallback failure so
    callers can distinguish a real backend failure from a legitimate
    "no speech detected" result (``""``).
    """


class _AbortStoppingCriteria:
    """Legacy ``transformers.StoppingCriteria`` shim — preserved for
    backward-compat with tests/importers that reference the name.

    The torch/transformers backend used this to wire
    ``model.generate()``'s ``stopping_criteria`` argument so the
    dictation pipeline's cancel path (ESC / watchdog) could stop
    generation between tokens. The ONNX Runtime backend has no
    per-token stopping hook — ``onnx-asr`` 0.12.0 does not forward
    ``RunOptions`` to ``session.run`` (see the note on
    ``ParakeetEngine._abort_event``), so the working abort path is
    the inter-chunk ``_abort_event`` check only (see
    :meth:`ParakeetEngine.request_abort`). This class is no longer
    used internally; it is kept as a no-op shim so existing
    ``from voice_typer.server.parakeet_engine import _AbortStoppingCriteria``
    imports in ``tests/test_dictation_pipeline_abort.py`` keep resolving.
    """

    def __init__(self, abort_event: threading.Event) -> None:
        self._abort_event = abort_event

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:  # noqa: D401
        """Return True if generation should stop (abort signalled)."""
        return self._abort_event.is_set()
