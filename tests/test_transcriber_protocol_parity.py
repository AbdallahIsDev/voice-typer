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
    silently disable the ``isinstance`` checks in ``streaming.py`` and
    ``recording_controller.py`` that gate streaming support.
    """
    cls = transcription.TranscriberProtocol
    # ``runtime_checkable`` sets ``_is_runtime_protocol = True`` on the
    # class object; this is the most stable cross-version signal.
    assert getattr(cls, "_is_runtime_protocol", False) is True


def test_transcriber_protocol_method_surface() -> None:
    """The canonical class exposes the full protocol method surface.

    Drift guard: if a future edit drops one of the methods from the
    canonical ``transcription_load.py`` definition, ``isinstance`` checks
    would silently start returning ``False`` for compliant engines. We
    assert the eight documented members are present.
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
        "transcribe_words",
    }
    actual_members = set(dir(cls))
    missing = expected_members - actual_members
    assert not missing, f"TranscriberProtocol is missing members: {sorted(missing)}"
