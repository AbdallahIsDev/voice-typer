"""Dictation pipeline package exports."""

from __future__ import annotations

from voice_typer.server.dictation_pipeline.enhancement_steps import (
    _EnhancementStepsMixin,
)
from voice_typer.server.dictation_pipeline.helpers import (  # noqa: F401
    _EMPTY_SEGMENTS,
    _NO_TRANSCRIPT_CONFIDENCE,
    BackendNotLoadedError,
    _AbortWatcher,
    _friendly_transcription_error,
    _lookup_local_whisper,
    _timed_stage,
)
from voice_typer.server.dictation_pipeline.orchestrator import (
    _OrchestratorMixin,
)
from voice_typer.server.dictation_pipeline.paste_step import (
    _PasteStepMixin,
)
from voice_typer.server.dictation_pipeline.storage_step import (
    _StorageStepMixin,
)
from voice_typer.server.dictation_pipeline.text_steps import (
    _TextStepsMixin,
)
from voice_typer.server.dictation_pipeline.transcribe_step import (
    _TranscribeStepMixin,
)

__all__ = [
    "BackendNotLoadedError",
    "DictationPipeline",
    "_AbortWatcher",
    "_EMPTY_SEGMENTS",
    "_NO_TRANSCRIPT_CONFIDENCE",
    "_friendly_transcription_error",
    "_lookup_local_whisper",
    "_timed_stage",
]


# Compose the orchestrator + 5 step mixins into the final public class.
class DictationPipeline(
    _OrchestratorMixin,
    _TranscribeStepMixin,
    _TextStepsMixin,
    _EnhancementStepsMixin,
    _StorageStepMixin,
    _PasteStepMixin,
):
    """The pipeline is run on a background thread by VoiceTyperApp."""

    pass
