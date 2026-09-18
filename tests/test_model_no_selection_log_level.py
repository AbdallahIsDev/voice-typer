"""No-model refusal logs at INFO, real errors stay WARN."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from voice_typer.server.asr_errors import ModelNotDownloadedError
from voice_typer.server.model_registry import NO_MODEL_SIZE


def _manager():
    from voice_typer.server.model_manager._notify import LastResortNotifyMixin

    class _M(LastResortNotifyMixin):
        def __init__(self):
            self._app = SimpleNamespace(
                config=SimpleNamespace(asr_backend="whisper"),
                tray=SimpleNamespace(
                    set_state=lambda *a, **k: None,
                    notify=lambda *a, **k: None,
                ),
            )

    return _M()


def test_no_model_selected_logs_info(caplog):
    mgr = _manager()
    exc = ModelNotDownloadedError(
        "No model selected. Open the Models page to pick a model.",
        model_size=NO_MODEL_SIZE,
        backend="whisper",
    )
    with caplog.at_level(logging.INFO, logger="voice_typer.server.model_manager"):
        mgr._notify_model_load_refused(exc, backend="whisper")
    infos = [r for r in caplog.records if "load refused" in r.getMessage()]
    assert len(infos) == 1
    assert infos[0].levelno == logging.INFO
    assert "No model selected" in infos[0].getMessage()


def test_missing_concrete_model_stays_warning(caplog):
    mgr = _manager()
    exc = ModelNotDownloadedError(
        "The configured whisper model 'tiny' is not downloaded. Open the Models page to download it.",
        model_size="tiny",
        backend="whisper",
    )
    with caplog.at_level(logging.INFO, logger="voice_typer.server.model_manager"):
        mgr._notify_model_load_refused(exc, backend="whisper")
    warns = [r for r in caplog.records if "load refused" in r.getMessage()]
    assert len(warns) == 1
    assert warns[0].levelno == logging.WARNING
