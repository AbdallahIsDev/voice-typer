"""Backward-compat shim — code moved to ``voice_typer.server.security`` (EO-23).

The model-download file-pattern allowlists moved to
:mod:`voice_typer.server.security.model_integrity` (merged with the
SHA-256 model-verification code). This module re-exports the two
backend-specific lists so existing import sites (``asr_setup.py``,
``transcription.py``, tests) keep working unchanged.

NOTE: the bare ``ALLOW_PATTERNS`` alias stays DELETED (GT-E1-3 — zero
production callers; enforced by
``tests/test_model_integrity.py::test_allow_patterns_backward_compat_alias_removed``).

New code should import from ``voice_typer.server.security.model_integrity``
directly.
"""

from voice_typer.server.security.model_integrity import (  # noqa: F401
    ALLOW_PATTERNS_PARAKEET,
    ALLOW_PATTERNS_WHISPER,
)

__all__ = ["ALLOW_PATTERNS_PARAKEET", "ALLOW_PATTERNS_WHISPER"]
