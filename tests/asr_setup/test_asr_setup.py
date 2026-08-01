"""ASR setup / recording / streaming tests split out of the former ``tests/test_history_and_models.py``.

Domain: ASR backend setup + recording infrastructure — asr_setup
no longer caches _config_dir, ResampleUnavailable typed exception,
StreamingTextAssembler._prune_old_entries invariant, and the
CloudEngine urlopen 30 s timeout.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import numpy as np


class TestCloudEngineUlopenTimeout:
    """The cloud engine passes timeout=30 to urlopen."""

    def test_openai_compatible_uses_30s_timeout(self):
        from voice_typer.server import cloud_engines

        engine = cloud_engines.CloudEngine(
            provider="openai",
            api_key="test-key",
            consent_given=True,
        )

        captured: dict = {}

        class _FakeCtxManager:
            def __enter__(self):
                fake_resp = MagicMock()
                body = b'{"text": "hello"}'
                fake_resp.read.side_effect = [body, b""]
                return fake_resp

            def __exit__(self, *args):
                return False

        def _fake_open(req, timeout=None, **kwargs):
            captured["timeout"] = timeout
            return _FakeCtxManager()

        fake_opener = MagicMock()
        fake_opener.open.side_effect = _fake_open

        with patch.object(cloud_engines, "_opener", fake_opener):
            audio = np.zeros(16000, dtype=np.float32)
            result = engine.transcribe(audio)

        assert result == "hello"
        assert captured.get("timeout") == 30


class TestAsrSetupHasNoConfigDirCache:
    """asr_setup no longer has _CONFIG_DIR cache."""

    def test_no_config_dir_cache(self):
        from voice_typer.server import asr_setup

        assert not hasattr(asr_setup, "_CONFIG_DIR")
        assert not hasattr(asr_setup, "_config_dir")

    def test_parakeet_uses_config_directly(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine

        source = inspect.getsource(ParakeetEngine._is_cached)
        assert "from voice_typer.server.config import _config_dir" in source
        assert "from voice_typer.server.asr_setup import _config_dir" not in source


class TestResampleUnavailable:
    """ResampleUnavailable is a typed exception for missing scipy."""

    def test_resample_unavailable_is_runtime_error(self):
        from voice_typer.server.recording import ResampleUnavailable

        assert issubclass(ResampleUnavailable, RuntimeError)


class TestPruneOldEntries:
    """_prune_old_entries does not rebuild _word_key_index."""

    def test_word_key_index_preserved_after_prune(self):
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        index_before = dict(assembler._word_key_index)
        assembler._prune_old_entries(1.0)
        assert assembler._word_key_index == index_before
