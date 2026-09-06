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
    """Protocol that every transcription engine must implement.

    This is the REQUIRED surface shared by all engines — Whisper,
    Parakeet, Qwen and Cloud. Word-level transcription is deliberately
    NOT part of it: only the local Whisper engine implements
    ``transcribe_words`` (see :class:`WordLevelTranscriber`), and the
    streaming coordinator gates that optional capability with an
    explicit ``hasattr(active, "transcribe_words")`` check before any
    streaming session starts.
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


@runtime_checkable
class WordLevelTranscriber(Protocol):
    """Optional capability protocol: word-level (streaming) transcription.

    Only the local Whisper ``TranscriptionEngine`` implements
    ``transcribe_words`` — Parakeet, Qwen and the cloud engines
    deliberately do not. Production never assumes the capability: the
    streaming session coordinator gates with
    ``hasattr(active, "transcribe_words")`` (mirroring
    :class:`WordLevelTranscriber` structurally) before starting a
    streaming session, so a Protocol that REQUIRED the method would
    misdocument the actual contract.

    Consumers MUST either isinstance-check against this
    ``runtime_checkable`` protocol or repeat the explicit ``hasattr``
    gate — never call ``transcribe_words`` unconditionally.
    """

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0) -> object: ...
