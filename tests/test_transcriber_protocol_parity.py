"""Parity test for ``TranscriberProtocol`` across the two server modules.

Finding  (review.md): both ``voice_typer/server/transcription.py`` and
``voice_typer/server/transcription_load.py`` defined their own
``@runtime_checkable class TranscriberProtocol(Protocol)``. Two distinct
class objects existed at runtime, so ``isinstance(x, TranscriberProtocol)``
returned different results depending on which module's class was used.

The fix deletes the duplicate from ``transcription.py`` and makes
``transcription.py`` re-export ``TranscriberProtocol`` from the canonical
home in ``transcription_load.py``. These tests pin that contract so a
future regression (re-introducing a local class) is caught immediately.

The word-level capability is deliberately NOT part of
``TranscriberProtocol``: only the local Whisper engine implements
``transcribe_words`` (Parakeet / Qwen / Cloud deliberately do not), and
production gates it with an explicit ``hasattr`` check (streaming
session coordinator). The optional capability is typed separately as
``WordLevelTranscriber`` and pinned here too.
"""

from __future__ import annotations

from voice_typer.server import transcription, transcription_load


def test_transcriber_protocol_identity() -> None:
    """The two module-level names MUST refer to the SAME class object.

    Identity (``is``) — not equality — is the contract: ``runtime_checkable``
    protocols register structural members on the class, so two
    byte-identical-looking class objects still produce different
    ``isinstance`` results.
    """
    assert transcription.TranscriberProtocol is transcription_load.TranscriberProtocol


def test_transcriber_protocol_runtime_checkable() -> None:
    """The re-exported class must still be ``runtime_checkable``.

    A plain ``Protocol`` subclass without ``@runtime_checkable`` would
    silently disable the ``isinstance`` checks consumers may run
    against the engine surface.
    """
    cls = transcription.TranscriberProtocol
    # ``runtime_checkable`` sets ``_is_runtime_protocol = True`` on the
    # class object; this is the most stable cross-version signal.
    assert getattr(cls, "_is_runtime_protocol", False) is True


def test_transcriber_protocol_method_surface() -> None:
    """The canonical class exposes the required engine method surface.

    Drift guard: if a future edit drops one of the methods from the
    canonical ``transcription_load.py`` definition, ``isinstance`` checks
    would silently start returning ``False`` for compliant engines. We
    assert the seven documented members are present.

    ``transcribe_words`` is intentionally ABSENT — it is an optional
    capability (see ``WordLevelTranscriber``), not part of the required
    engine surface: Parakeet, Qwen and Cloud deliberately do not
    implement it.
    """
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
    """``WordLevelTranscriber`` types the optional ``transcribe_words`` capability.

    ``isinstance`` against the ``runtime_checkable`` capability protocol
    must accept only objects that actually implement ``transcribe_words``
    — mirroring the production ``hasattr(active, "transcribe_words")``
    gate in the streaming session coordinator.
    """
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
