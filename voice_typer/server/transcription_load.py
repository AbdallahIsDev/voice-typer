"""Model load for transcription."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")


@runtime_checkable
class TranscriberProtocol(Protocol):
    """Protocol that every transcription engine must implement."""

    @property
    def is_loaded(self) -> bool: ...

    def load(self, progress_callback=None) -> None: ...

    def transcribe(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str: ...

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
        local_engine=None,
    ) -> str: ...

    def unload(self) -> None: ...

    @property
    def device_info(self) -> str: ...

    @property
    def loaded_via(self) -> str: ...


@runtime_checkable
class WordLevelTranscriber(Protocol):
    """Optional capability protocol: word-level (streaming) transcription."""

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0) -> object: ...
