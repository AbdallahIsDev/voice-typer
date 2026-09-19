"""whisper-family download path report SUCCESS."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def whisper_service(tmp_config_dir):
    """A VoiceTyperService wired to a whisper-family model, consent given."""
    from voice_typer.server.service import VoiceTyperService

    class FakeApp:
        config = type(
            "FakeConfig",
            (),
            {
                "asr_backend": "whisper",
                "model_size": "tiny",
                "huggingface_consent": True,
            },
        )()
        tray = MagicMock(name="tray")

    return VoiceTyperService(FakeApp())


@pytest.fixture()
def progress_spy(monkeypatch):
    """recorders so tests can assert on the exact progress/notification stream"""
    import voice_typer.server.service._download_helpers as helpers

    progress: list[tuple[int, str]] = []
    toasts: list[str] = []
    monkeypatch.setattr(
        helpers,
        "push_progress",
        lambda _bus, name, pct, message, **_kw: progress.append((pct, message)),
    )
    monkeypatch.setattr(
        helpers,
        "notify",
        lambda _tray, _name, _title, body: toasts.append(body),
    )
    return progress, toasts


class TestWhisperDownloadMissingHuggingfaceHub:
    def test_missing_hub_returns_structured_failure(self, whisper_service, progress_spy, monkeypatch):
        """With ``huggingface_hub`` unimportable, ``_download_whisper_family``"""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata("tiny")
        assert meta is not None, "tiny.en must be in MODEL_REGISTRY for this test"

        # ``None`` in sys.modules makes ``from huggingface_hub import ...``
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)

        result = whisper_service._download_whisper_family("tiny", meta)

        assert result["success"] is False, f"expected structured failure, got: {result}"
        assert result["reason"] == "huggingface_hub_missing"
        assert "huggingface_hub" in result["error"]
        assert result["model"] == "tiny"

    def test_missing_hub_never_pushes_100_percent_nor_success_toast(self, whisper_service, progress_spy, monkeypatch):
        """failure message and a FAILURE toast, no 100% push, no \"downloaded"""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata("tiny")
        assert meta is not None
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)

        progress, toasts = progress_spy
        whisper_service._download_whisper_family("tiny", meta)

        assert progress, "expected at least one progress push (the 0% failure reset)"
        assert not any(pct == 100 for pct, _ in progress), (
            f"regression: 100% progress pushed on the ImportError path, "
            f"the failure fell through to the success report. Pushes: {progress}"
        )
        assert all("successfully" not in body for body in toasts), (
            f"regression: success toast shown on the ImportError path. Toasts: {toasts}"
        )
        assert any("Failed to download" in body for body in toasts), f"expected a failure toast, got: {toasts}"

    def test_missing_hub_releases_single_flight_gate(self, whisper_service, progress_spy, monkeypatch):
        """The ImportError arm must clear the download pause/abort state —"""
        from voice_typer.server.asr_setup import (
            clear_download_pause_state,
            is_download_active,
        )
        from voice_typer.server.model_registry import get_model_metadata

        clear_download_pause_state()
        try:
            meta = get_model_metadata("tiny")
            assert meta is not None
            monkeypatch.setitem(sys.modules, "huggingface_hub", None)

            whisper_service._download_whisper_family("tiny", meta)

            assert is_download_active() is False, (
                "regression: ImportError path left the single-flight gate "
                "latched, subsequent downloads would be refused as "
                "'already active'"
            )
        finally:
            clear_download_pause_state()

    def test_missing_hub_via_download_model_dispatcher(self, whisper_service, progress_spy, monkeypatch):
        """End-to-end through the ``download_model`` dispatcher: the outer"""
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)

        result = whisper_service.download_model("tiny")

        assert result["success"] is False
        assert result.get("reason") == "huggingface_hub_missing"
        assert "reason" in result

    def test_failure_toast_body_reuses_shared_reason_message(self, whisper_service, progress_spy, monkeypatch):
        """The failure message must come from the shared reason table (E7 —"""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service.model._constants import _PARAKEET_REASON_MESSAGES

        meta = get_model_metadata("tiny")
        assert meta is not None
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)

        _progress, toasts = progress_spy
        whisper_service._download_whisper_family("tiny", meta)

        expected = _PARAKEET_REASON_MESSAGES["huggingface_hub_missing"]
        assert any(expected in body for body in toasts), (
            f"expected the shared reason-table message in the toast, got: {toasts}"
        )


class TestWhisperDownloadCacheHitSingleTerminalPush:
    """Cache hits must emit exactly ONE terminal 100% progress event."""

    @pytest.fixture()
    def fake_cached_hub(self, monkeypatch):
        """the local-only probe, i.e. the model is already fully cached."""
        import types

        calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            calls.append(kwargs)
            return "/fake/cache/path"

        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
        return calls

    def test_cache_hit_pushes_exactly_one_100_percent_event(self, whisper_service, progress_spy, fake_cached_hub):
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata("tiny")
        assert meta is not None, "tiny must be in MODEL_REGISTRY for this test"

        result = whisper_service._download_whisper_family("tiny", meta)

        assert result["success"] is True, f"a cache hit must succeed, got: {result}"
        progress, _toasts = progress_spy
        terminal = [(pct, msg) for pct, msg in progress if pct == 100]
        assert len(terminal) == 1, (
            f"regression: a cache hit must emit exactly ONE terminal 100% push; "
            f"got {len(terminal)}: {terminal} (full stream: {progress})"
        )
        assert terminal[0][1] == "Download of tiny complete"
        cached_events = [(pct, msg) for pct, msg in progress if "already cached" in msg]
        assert cached_events, f"the 'already cached' status message must be preserved on a cache hit; got: {progress}"
        assert all(pct < 100 for pct, _ in cached_events), (
            f"the 'already cached' event must not itself be a terminal 100% push; got: {cached_events}"
        )

    def test_cache_hit_skips_the_transfer_machinery(self, whisper_service, progress_spy, fake_cached_hub):
        """A successful local-only probe must go straight to the success"""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata("tiny")
        assert meta is not None

        result = whisper_service._download_whisper_family("tiny", meta)

        assert result["success"] is True
        progress, _toasts = progress_spy
        assert not any("Downloading" in msg for _, msg in progress), (
            f"cache hit must not report transfer progress; got: {progress}"
        )
        assert whisper_service._download_cancel_events == {}, "cache hit must not register a per-download cancel Event"
        # Only the local-only cache probe ran, no network download call.
        assert fake_cached_hub, "the cache probe must have run"
        assert all(kwargs.get("local_files_only") for kwargs in fake_cached_hub)
