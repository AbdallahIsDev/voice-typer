"""Parity test for ``TranscriberProtocol`` across the two server modules."""

from __future__ import annotations

from voice_typer.server import transcription, transcription_load


def test_transcriber_protocol_identity() -> None:
    """
    The two module-level names MUST refer to the SAME class object.
    Identity (``is``) (not equality) is the contract: ``runtime_checkable``
    """
    assert transcription.TranscriberProtocol is transcription_load.TranscriberProtocol


def test_transcriber_protocol_runtime_checkable() -> None:
    """The re-exported class must still be ``runtime_checkable``."""
    cls = transcription.TranscriberProtocol
    assert getattr(cls, "_is_runtime_protocol", False) is True


def test_transcriber_protocol_method_surface() -> None:
    """The canonical class exposes the required engine method surface."""
    cls = transcription_load.TranscriberProtocol
    expected_members = {
        "is_loaded",
        "load",
        "transcribe",
        "transcribe_with_fallback",
        "unload",
        "device_info",
        "loaded_via",
    }
    actual_members = set(dir(cls))
    missing = expected_members - actual_members
    assert not missing, f"TranscriberProtocol is missing members: {sorted(missing)}"
    assert "transcribe_words" not in actual_members


def test_word_level_capability_is_separate_protocol() -> None:
    """``WordLevelTranscriber`` types the optional ``transcribe_words`` capability."""
    cls = transcription_load.WordLevelTranscriber
    assert getattr(cls, "_is_runtime_protocol", False) is True

    class _FullEngine:
        def transcribe_words(self, audio, offset_seconds: float = 0.0):
            return []

    class _CloudEngine:  # deliberately lacks transcribe_words
        pass

    assert isinstance(_FullEngine(), cls)
    assert not isinstance(_CloudEngine(), cls)


def test_real_whisper_engine_satisfies_capability() -> None:
    """The local Whisper engine implements the optional capability."""
    from voice_typer.server import transcription as transcription_module
    from voice_typer.server.transcription_load import WordLevelTranscriber

    engine = transcription_module.TranscriptionEngine.__new__(transcription_module.TranscriptionEngine)
    assert isinstance(engine, WordLevelTranscriber)
