"""Chunked window transcription over decoder PCM (ADR-0023 engine loop)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

WINDOW_SECONDS = 30.0
TARGET_SAMPLE_RATE = 16_000


class WindowBackend(Protocol):
    """Structural contract: active ASR backend chunk transcription."""

    def transcribe_with_fallback(self, audio: Any, *args: object, **kwargs: object) -> str: ...

    def request_abort(self) -> None: ...


@dataclass
class WindowsState:
    """Accumulated transcript + decoded offset, survives stream retries.

    Shared across re-opened streams so a stall/expiry resume (E9/E10)
    continues appending instead of losing the partial transcript.
    """

    texts: list[str] = field(default_factory=list)
    decoded_seconds: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(self.texts).strip()


# (fraction, eta_seconds); eta is None until a stable rate is known.
ProgressCallback = Callable[[float, float | None], None]


def transcribe_windows(
    chunks: Iterable[Any],
    backend: WindowBackend,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    total_seconds: float | None = None,
    state: WindowsState | None = None,
) -> WindowsState:
    """Accumulate 5 s chunks into 30 s windows; transcribe each.

    Cancellation stops the loop cooperatively and returns the partial
    state instead of raising, so the caller can persist it (E14).
    """
    import numpy as np

    acc = state if state is not None else WindowsState()
    window: list[Any] = []
    window_samples = 0
    window_cap = int(WINDOW_SECONDS * TARGET_SAMPLE_RATE)
    started = time.perf_counter()
    start_offset = acc.decoded_seconds

    def _flush() -> None:
        nonlocal window, window_samples
        if not window:
            return
        audio = np.concatenate(window).astype(np.float32, copy=False)
        window = []
        window_samples = 0
        text = str(backend.transcribe_with_fallback(audio) or "").strip()
        if text:
            acc.texts.append(text)

    def _cancelled() -> bool:
        if cancel_event is None or not cancel_event.is_set():
            return False
        try:
            backend.request_abort()
        except Exception:  # noqa: BLE001, abort is best-effort
            log.debug("[MEDIA] backend abort failed", exc_info=True)
        return True

    for chunk in chunks:
        if _cancelled():
            break
        window.append(chunk)
        window_samples += int(chunk.shape[0])
        acc.decoded_seconds += float(chunk.shape[0]) / TARGET_SAMPLE_RATE
        if window_samples >= window_cap:
            _flush()
            if _cancelled():
                break
        if on_progress is not None and total_seconds:
            frac = min(1.0, acc.decoded_seconds / total_seconds)
            on_progress(frac, _eta(acc.decoded_seconds - start_offset, started, total_seconds, acc.decoded_seconds))
    if cancel_event is None or not cancel_event.is_set():
        _flush()
        if on_progress is not None:
            on_progress(1.0, 0.0)
    return acc


def _eta(processed: float, started: float, total_seconds: float | None, decoded: float) -> float | None:
    """ETA from the measured decode+transcribe rate of this call."""
    if not total_seconds or processed <= 2.0 or decoded >= total_seconds:
        return None
    elapsed = time.perf_counter() - started
    if elapsed <= 0:
        return None
    rate = processed / elapsed
    if rate <= 0:
        return None
    return round(max(0.0, (total_seconds - decoded) / rate), 1)
