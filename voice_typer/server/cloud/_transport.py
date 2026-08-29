"""Shared cloud HTTP transport primitives.

Extracted from the ``cloud_engines.py`` monolith so the transport layer
(pooled secure opener, response-body cap, WAV encoding, streaming
multipart body) is independently importable and testable.
``voice_typer/server/cloud_engines.py`` re-exports every name defined
here, so existing importers (including tests that poke module
attributes through that namespace) keep working unchanged.
"""

from __future__ import annotations

import io
import wave

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._http_safety import build_secure_opener

# PERF-: module-level OpenerDirector for connection pooling.
# Reuses TCP connections across requests (like requests.Session).
# SEC-2: ``build_secure_opener()`` installs ``_NoRedirectHandler()`` so
# the opener does NOT follow 3xx redirects (the default
# ``HTTPRedirectHandler`` would silently POST the request body — user
# audio + API key — to an attacker-controlled redirect target).
# the handler + builder live in ``_http_safety`` so they're
# shared with ``llm_polish._opener`` (single source of truth).
_opener = build_secure_opener()


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = WHISPER_SAMPLE_RATE) -> bytes:
    """Convert float32 numpy array to WAV bytes."""
    buf = io.BytesIO()
    # Convert float32 to int16
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def _read_capped(resp, *, max_bytes: int) -> bytes:
    """Read up to ``max_bytes`` from ``resp``.

    SEC-030: ``resp.read()`` with no size argument reads the entire body
    into memory. A malicious or buggy server returning a 5 GB
    Content-Length would exhaust RAM before the transcription thread
    caught up. We stream the response in 64 KB chunks and abort if the
    total exceeds the cap.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"Response body exceeded {max_bytes} bytes — aborting to prevent OOM")
        chunks.append(chunk)
    return b"".join(chunks)


class _StreamingMultipartBody:
    """File-like object that yields multipart body chunks on demand.

    PERF-: avoids building the entire multipart body in memory
        as a single ``bytes`` object. ``urllib.request.Request`` accepts a
        file-like object as ``data`` and reads it in chunks via ``read()``.
        This class yields the pre-computed parts list one chunk at a time,
        reducing peak memory from the full body (~5.2 MB for a 30s
        recording) to one chunk (~64 KB).

        The ``__contains__`` method supports the ``in`` operator so
        existing tests like ``assert b"fake_wav_data" in body`` continue
        to work without materializing the entire body.
    """

    _CHUNK_SIZE = 64 * 1024  # 64 KB per read() call

    def __init__(self, parts: list[bytes]):
        self._parts = parts
        self._total_length = sum(len(p) for p in parts)
        self._part_iter = iter(parts)
        self._current = b""
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes. If ``size == -1``, read all remaining."""
        if size == -1:
            # Read everything remaining
            remaining = b"".join(self._current_chunk_and_rest())
            self._current = b""
            return remaining
        result = bytearray()
        while len(result) < size:
            if not self._current:
                try:
                    self._current = next(self._part_iter)
                except StopIteration:
                    break
            needed = size - len(result)
            chunk = self._current[:needed]
            result.extend(chunk)
            self._current = self._current[len(chunk) :]
        self._pos += len(result)
        return bytes(result)

    def _current_chunk_and_rest(self):
        """Yield the current partial chunk, then all remaining parts."""
        if self._current:
            yield self._current
            self._current = b""
        yield from self._part_iter

    def __len__(self) -> int:
        """Total body length (for Content-Length header)."""
        return self._total_length - self._pos

    def __contains__(self, needle: bytes) -> bool:
        """Support ``in`` operator for test assertions.

        This materializes the full body, but tests only call it on
        small fake payloads (e.g. ``b"fake_wav_data"``), so the memory
        impact is negligible.
        """
        return needle in b"".join(self._parts)

    # urllib may call these on file-like data objects
    def readline(self, size: int = -1) -> bytes:
        return self.read(size)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass
