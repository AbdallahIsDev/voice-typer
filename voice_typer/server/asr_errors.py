"""Shared ASR error types.

EC-FIX-8: extracted from ``cloud_engines.py`` to break the layering
violation where local ASR engines (``parakeet_engine``,
``transcription``) imported ``ConsentRequiredError`` from the cloud-
engines module (EC-30 finding #12 / EC-B4).

``ConsentRequiredError`` was previously defined in ``cloud_engines.py``
because it was first raised by the cloud transcription path.  But the
HuggingFace consent gate (used by Whisper / Parakeet / Qwen local
engines) also needs to raise the same exception so the IPC layer can
``isinstance``-check it uniformly and surface a consent dialog instead
of a generic error toast.  Having local engines import the symbol from
``cloud_engines`` created an unwanted dependency: local-engine
``import`` had to pull in the entire cloud-engines module (and its
``numpy``, ``urllib``, threading imports) just to get a 5-line
exception class.

This module is intentionally dependency-free (no imports from
``voice_typer.server.*``) so any engine can import it without risk of
circular imports.
"""

from __future__ import annotations


class ConsentRequiredError(RuntimeError):
    """NEW-PRIV-006: raised when an ASR engine is asked to transcribe
    or download model files but the user hasn't granted consent for
    that provider.

    Subclass of ``RuntimeError`` so existing ``except RuntimeError``
    catch clauses still work — but the IPC layer can
    ``isinstance``-check for this type to surface a consent dialog
    instead of an error toast.

    Raised by:

    - ``cloud_engines.CloudEngine.transcribe`` — when cloud-provider
      consent (``openai_consent`` / ``groq_consent`` /
      ``deepgram_consent`` / ``cloud_consent``) is False.
    - ``transcription.TranscriptionEngine._pre_download_model`` — when
      ``huggingface_consent`` is False and the model needs to be
      downloaded from HuggingFace (EC-FIX-8: now raises instead of
      silently returning, matching Parakeet's behavior).
    - ``parakeet_engine.ParakeetEngine.load`` — when
      ``huggingface_consent`` is False and the Parakeet model needs to
      be downloaded from HuggingFace.
    - ``asr_setup.download_parakeet_weights`` — defense-in-depth
      consent gate (returns a ``tuple[bool, str]`` instead of raising,
      but the IPC layer maps the ``"huggingface_consent_false"`` reason
      code to a ``ConsentRequiredError``-equivalent UI flow).
    """
