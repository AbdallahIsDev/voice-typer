"""NEW-PRIV-005 regression: TranscriptionEngine consent check must NOT
crash on the real construction path.

The bug: ``TranscriptionEngine.__init__`` did not assign ``self.config``,
but ``_pre_download_model`` called ``getattr(self.config, ...)``.  The
``getattr`` default of ``False`` only protects against a missing
*huggingface_consent* attribute; it does NOT protect against ``self.config``
itself being missing.  Result: ``AttributeError`` on every uncached
Whisper download attempt — the most common production path.

Round 1 tests bypassed the bug by using ``TranscriptionEngine.__new__()``
(which skips ``__init__``) and manually setting ``engine.config =
FakeConfig()``.  These tests use the REAL ``__init__`` path so the bug
would be caught if it regressed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path


class TestNewPriv005RealConstructionPath:
    """Verify the consent check works when the engine is constructed
    the way production constructs it (via __init__, with config=...)."""

    def test_engine_accepts_config_kwarg(self, tmp_config_dir):
        """TranscriptionEngine.__init__ must accept a ``config`` kwarg
        and store it as ``self.config``."""
        from voice_typer.server.config import Config
        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)
        # The engine MUST have a non-None config attribute after __init__.
        assert engine.config is cfg, (
            "TranscriptionEngine.__init__ must assign self.config when "
            "the config kwarg is passed"
        )
        assert engine.config.huggingface_consent is True

    def test_engine_defaults_config_to_none(self, tmp_config_dir):
        """When no config is passed, self.config must be None (not missing).
        The consent check must handle None gracefully — no AttributeError."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en")
        # Must not raise AttributeError.  None is the safe default.
        assert engine.config is None

    def test_pre_download_does_not_crash_without_config(self, tmp_path, monkeypatch):
        """The bug: ``_pre_download_model`` crashed with AttributeError
        when self.config was missing.  Verify the fix handles None
        gracefully (treats as 'no consent', returns without downloading)."""
        # Stub huggingface_hub.snapshot_download so cache-check fails
        # (forces the consent-check path).
        import sys
        fake_module = type(sys)("huggingface_hub")

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            # If we reach here, consent was given AND we'd hit network.
            # The test asserts we NEVER reach here when consent is missing.
            raise AssertionError(
                "snapshot_download was called with local_files_only=False "
                "even though consent was not given — the consent check is broken."
            )

        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        # Construct via the REAL __init__ path (no __new__ bypass).
        # No config kwarg → self.config is None → consent defaults to
        # False → _pre_download_model returns early without crashing.
        engine = TranscriptionEngine(model_size="small.en")
        progress_messages: list[str] = []
        # Must NOT raise AttributeError.
        engine._pre_download_model(
            "small.en",
            progress_callback=progress_messages.append,
        )
        # The consent-required message should have been pushed.
        assert any("consent" in m.lower() for m in progress_messages), (
            f"Expected a consent-required progress message, got: {progress_messages}"
        )

    def test_pre_download_downloads_when_consent_given(self, tmp_path, monkeypatch):
        """When consent IS given (config passed with huggingface_consent=True),
        _pre_download_model must actually call snapshot_download with
        local_files_only=False (i.e. proceed with the download)."""
        import sys
        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            # Simulate a successful download.
            return str(tmp_path / "fake_model")

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        # Verify a real (non-local-only) download was attempted.
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) >= 1, (
            f"Expected at least one non-local download call, got: {download_calls}"
        )

    def test_pre_download_refuses_download_without_consent(self, tmp_path, monkeypatch):
        """When consent is NOT given (config passed but
        huggingface_consent=False), _pre_download_model must NOT call
        snapshot_download with local_files_only=False."""
        import sys
        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            raise AssertionError(
                "snapshot_download called with local_files_only=False "
                "even though huggingface_consent is False"
            )

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = False
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        # Only the local_files_only call (cache check) should have happened.
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) == 0, (
            f"Expected zero non-local download calls when consent is False, "
            f"got: {non_local_calls}"
        )


class TestNewPriv005ModelManagerWiring:
    """Verify the production code path (model_manager._ensure_engine)
    passes the live Config to TranscriptionEngine."""

    def test_ensure_engine_passes_config_to_whisper(self, tmp_config_dir, monkeypatch):
        """model_manager._ensure_engine('whisper') must pass
        config=self._app.config to the TranscriptionEngine constructor."""
        from voice_typer.server.config import Config
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.asr_registry import AsrBackendRegistry
        from voice_typer.server.tray import AppState

        # Minimal app stub with the fields ModelManager reads.
        class FakeTray:
            state = AppState.IDLE
            def set_state(self, *args, **kwargs): pass
            def notify(self, *args, **kwargs): pass

        class FakeApp:
            def __init__(self):
                self.config = Config()
                self.config.huggingface_consent = True
                self.tray = FakeTray()
                self._ipc_server = None
                self.models = None  # set by ModelManager.__init__
                # Required by some legacy code paths
                self._cloud_engine = None
                self._llm_polisher = None
                self._template_manager = None

        app = FakeApp()
        app.models = ModelManager(app)
        # ModelManager creates its own internal AsrBackendRegistry
        # (self._registry) — use it to verify the engine was registered.
        registry = app.models._registry
        # _ensure_engine('whisper') constructs a TranscriptionEngine via
        # registry.create("whisper", whisper_kwargs=dict(..., config=...)).
        app.models._ensure_engine("whisper")
        engine = registry.get("whisper")
        assert engine is not None, "whisper engine was not registered"
        # The engine MUST have the live config reference.
        assert engine.config is app.config, (
            "model_manager._ensure_engine('whisper') did not pass "
            "config=self._app.config to TranscriptionEngine"
        )
