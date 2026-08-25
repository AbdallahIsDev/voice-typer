"""Canonical home of ``TranscriberProtocol`` for the Whisper ASR backend.

This module previously also hosted free-function copies of the model-load
bodies (``load_transcriber_impl``, ``probe_cuda_runtime``, ``warm_up_model``,
``resolve_device``, ``build_fallback_chain``). Those copies were a
pre-split extraction that was superseded by the focused sibling modules
(``transcription_device`` / ``transcription_cuda_probe`` /
``transcription_download`` / ``transcription_fallback``) and the engine's
inline load orchestrators; they had already drifted from the live bodies
(differing CUDA-runtime gate, immediate vs deferred GPU release, missing
beam re-resolve, stale log formats) while keeping zero importers, so they
were removed rather than kept as dead duplicates.

The protocol STAYS here as the canonical definition:
``voice_typer.server.transcription`` re-exports it so
``from voice_typer.server.transcription import TranscriberProtocol``
resolves to the SAME class object (identity parity — pinned by
``tests/test_transcriber_protocol_parity.py``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")


@runtime_checkable
class TranscriberProtocol(Protocol):
    """Protocol that any transcription engine must implement.

    ``isinstance(backend, TranscriberProtocol)`` correctly identifies
    backends that support streaming (including the ``transcribe_words``
    method used by ``streaming.py`` and ``recording_controller.py``).
    """

    @property
    def is_loaded(self) -> bool: ...

    def load(self, progress_callback=None) -> None: ...

    def transcribe(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str: ...

    def transcribe_with_fallback(
        self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None
    ) -> str: ...

    def unload(self) -> None: ...

    @property
    def device_info(self) -> str: ...

    @property
    def loaded_via(self) -> str: ...

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0) -> object: ...
