"""CR-18 regression guard — verify ``model_downloader`` module extraction.

Finding CR-18 (Critical): ``voice_typer/server/service.py`` is a
2,365-LOC mixed-concern god facade. Fix-D extracts the
``download_model`` method (and its surrounding download/poll/cancel
machinery) into a dedicated ``voice_typer/server/model_downloader.py``
module.

The extracted module should expose a callable (typically
``download_model(service, model_name)`` or
``ModelDownloader.download(model_name)``) that:
1. Enforces the HuggingFace consent gate (CR-8).
2. Pushes ``download_progress`` events to the renderer (UX-005).
3. Returns ``{"success": bool, ...}`` matching the legacy shape.
4. Delegates to ``snapshot_download`` for Whisper-family models.
5. Honors per-download cancellation (HIGH-8 / SERVICE-1).
6. Honors pause / resume (NEW-PAUSE-001).

After Fix-D, ``VoiceTyperService.download_model`` should be a thin
delegator to the extracted module so behavior is preserved.

This is a Fix-T test (coordinates with Fix-D). It is expected to
FAIL until Fix-D lands the extraction.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest


def _has_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


@pytest.fixture
def fake_service() -> MagicMock:
    """Build a fake VoiceTyperService-like object with the minimal
    attributes ``download_model`` reads (app, config, tray, _app,
    per-download registry helpers)."""
    svc = MagicMock()
    svc._app = MagicMock()
    svc._app.config.qwen_model_path = None
    svc._app.config.huggingface_consent = True
    svc._app.tray.notify = MagicMock()
    svc._register_download = MagicMock(return_value="dl-1")
    svc._is_download_cancelled = MagicMock(return_value=False)
    svc._unregister_download = MagicMock()
    return svc


def test_model_downloader_module_exists() -> None:
    """The extracted module must exist after Fix-D."""
    assert _has_module("voice_typer.server.model_downloader"), (
        "voice_typer.server.model_downloader module not found — "
        "Fix-D should extract download_model into this module (CR-18)."
    )


def test_model_downloader_exposes_download_callable() -> None:
    """The module must expose a callable entry point."""
    if not _has_module("voice_typer.server.model_downloader"):
        pytest.skip("Fix-D not yet landed")
    mod = importlib.import_module("voice_typer.server.model_downloader")

    # Accept either a function ``download_model`` or a class
    # ``ModelDownloader`` with a ``download`` method.
    has_func = callable(getattr(mod, "download_model", None))
    has_class = hasattr(mod, "ModelDownloader")
    assert has_func or has_class, (
        "model_downloader must expose either download_model() or ModelDownloader class with .download() method"
    )


def test_service_download_model_delegates_to_model_downloader(fake_service) -> None:
    """After extraction, ``VoiceTyperService.download_model`` should be
    a thin delegator — calling it should trigger the extracted
    module's ``download_model`` (or ``ModelDownloader.download``).
    """
    if not _has_module("voice_typer.server.model_downloader"):
        pytest.skip("Fix-D not yet landed")

    from voice_typer.server.service import VoiceTyperService

    svc = VoiceTyperService(fake_service._app)
    mod = importlib.import_module("voice_typer.server.model_downloader")

    captured: list = []

    # Replace the extracted callable with a spy.
    if hasattr(mod, "download_model"):

        def _spy(*args, **kwargs):
            captured.append((args, kwargs))
            return {"success": True, "model": "qwen"}

        mod.download_model = _spy  # type: ignore[assignment]
    elif hasattr(mod, "ModelDownloader"):
        orig_init = mod.ModelDownloader.__init__
        mod.ModelDownloader.download = lambda self, *a, **kw: captured.append((a, kw)) or {"success": True}

    # qwen already cached → consent gate passes (huggingface_consent=True).
    result = svc.download_model("qwen")
    assert result["success"] is True
    assert captured, (
        "Expected VoiceTyperService.download_model to delegate to "
        "the extracted model_downloader module (CR-18 / Fix-D)."
    )


def test_consent_gate_still_enforced_after_extraction(fake_service) -> None:
    """CR-8: HuggingFace consent gate must survive the extraction."""
    if not _has_module("voice_typer.server.model_downloader"):
        pytest.skip("Fix-D not yet landed")

    from voice_typer.server.service import VoiceTyperService

    fake_service._app.config.huggingface_consent = False
    svc = VoiceTyperService(fake_service._app)

    # download_model should refuse without consent — no matter what
    # model is requested.
    result = svc.download_model("small.en")
    assert result["success"] is False, (
        "HuggingFace consent gate (CR-8) must survive model_downloader "
        "extraction — refusing download_model('small.en') when "
        "huggingface_consent is False."
    )
    assert "consent" in (result.get("message") or "").lower() or "consent" in str(result).lower(), (
        f"Expected consent-related message in refusal: {result}"
    )


def test_unknown_model_returns_error_after_extraction(fake_service) -> None:
    """An unknown model name returns success=False — behavior preserved."""
    if not _has_module("voice_typer.server.model_downloader"):
        pytest.skip("Fix-D not yet landed")

    from voice_typer.server.service import VoiceTyperService

    svc = VoiceTyperService(fake_service._app)
    result = svc.download_model("nonexistent-model")
    assert result["success"] is False
