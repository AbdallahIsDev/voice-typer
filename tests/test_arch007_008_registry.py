"""ARCH-007/008: tests for AsrBackendRegistry.create() and the unified
construction path.

Verifies that:
- AsrBackendRegistry.create() constructs and registers each backend type
- The three previously-triplicated construction sites in app.py now all
  delegate to the registry (one chokepoint)
- The registry is initialized in __init__ (not lazily) so _start_dictation
  can rely on it existing
"""
import sys
import inspect

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')

import pytest
from unittest.mock import MagicMock, patch
from voice_typer.server.asr_registry import AsrBackendRegistry


class TestArch007RegistryCreate:
    """ARCH-007: AsrBackendRegistry.create() owns engine construction."""

    def test_create_whisper_constructs_and_registers(self, monkeypatch):
        """create('whisper', ...) constructs TranscriptionEngine and registers it."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(TranscriptionEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.transcription" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "whisper",
            whisper_kwargs=dict(
                model_size="tiny.en", device="cpu", language="en",
                beam_size=1, best_of=1, condition_on_previous_text=False,
            ),
        )
        assert result is fake_engine
        fake_cls.assert_called_once()
        _, kwargs = fake_cls.call_args
        assert kwargs["model_size"] == "tiny.en"
        assert kwargs["device"] == "cpu"
        # Registry should now have the whisper backend
        assert registry.get("whisper") is fake_engine

    def test_create_qwen_constructs_with_kwargs(self, monkeypatch):
        """create('qwen', ...) constructs QwenEngine with the right kwargs."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(QwenEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.qwen_engine" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "qwen",
            qwen_kwargs=dict(model_path="/fake/path", device="cpu", language="en"),
        )
        assert result is fake_engine
        fake_cls.assert_called_once_with(model_path="/fake/path", device="cpu", language="en")
        assert registry.get("qwen") is fake_engine

    def test_create_parakeet_constructs_with_kwargs(self, monkeypatch):
        """create('parakeet', ...) constructs ParakeetEngine with the right kwargs."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(ParakeetEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.parakeet_engine" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "parakeet",
            parakeet_kwargs=dict(device="cuda", language="en"),
        )
        assert result is fake_engine
        fake_cls.assert_called_once_with(device="cuda", language="en")
        assert registry.get("parakeet") is fake_engine

    def test_create_unknown_backend_returns_none(self):
        """create('nonexistent', ...) returns None and logs an error."""
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("nonexistent")
        assert result is None

    def test_create_handles_import_error_gracefully(self, monkeypatch):
        """If the backend module can't be imported, return None (not raise)."""
        def raise_import(name, *a, **kw):
            raise ImportError(f"no module {name}")
        monkeypatch.setattr("importlib.import_module", raise_import)
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("whisper", whisper_kwargs={})
        assert result is None

    def test_create_handles_construction_error_gracefully(self, monkeypatch):
        """If the engine constructor raises, return None (not propagate)."""
        fake_cls = MagicMock(side_effect=RuntimeError("bad config"))
        fake_mod = MagicMock(TranscriptionEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.transcription" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("whisper", whisper_kwargs={})
        assert result is None


class TestArch007AppConstructionDelegatesToRegistry:
    """ARCH-007: All 3 construction sites in app.py delegate to registry.create()."""

    def test_no_direct_transcription_engine_construction_in_app(self):
        """app.py source must NOT contain 'X = TranscriptionEngine(...)' assignments.

        Previously the code had three sites like:
            self.transcriber = TranscriptionEngine(model_size=..., device=..., ...)
        All three now go through AsrBackendRegistry.create() instead.

        We look for the assignment pattern ('= TranscriptionEngine(')
        which only matches real constructor calls, not text mentions
        in comments/docstrings.
        """
        import re
        from voice_typer.server import app
        src = inspect.getsource(app)
        # Match lines like "self.transcriber = TranscriptionEngine(...)"
        # but NOT comments (# ...) or docstring text mentioning the pattern.
        construction_pattern = re.compile(r'^[^#]*=\s*TranscriptionEngine\(', re.MULTILINE)
        matches = construction_pattern.findall(src)
        assert not matches, (
            "ARCH-007 regression: app.py still has direct "
            "'X = TranscriptionEngine(...)' construction. All construction "
            "must go through AsrBackendRegistry.create(). "
            f"Found {len(matches)} match(es)."
        )


class TestArch008RegistryInitializedEarly:
    """ARCH-008: registry is initialized in __init__ (not lazily)."""

    def test_asr_registry_exists_after_init(self, tmp_path, monkeypatch):
        """VoiceTyperApp.__init__ must set self._asr_registry before any
        engine field is accessed."""
        # Point config to a temp directory
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path
        )
        # Mock heavy deps so __init__ doesn't fail
        monkeypatch.setattr(
            "voice_typer.server.app.is_autostart_enabled", lambda: False
        )
        monkeypatch.setattr(
            "voice_typer.server.app.list_microphones", lambda: []
        )
        from voice_typer.server.app import VoiceTyperApp
        app = VoiceTyperApp()
        assert hasattr(app, '_asr_registry'), (
            "_asr_registry must be set in __init__ so _start_dictation "
            "and other code paths can rely on it existing"
        )
        assert app._asr_registry is not None
