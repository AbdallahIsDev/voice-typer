"""Wiring pins for the ``transcription.py`` split into focused sibling modules.

``TranscriptionEngine``'s device / CUDA-probe / download-gate / fallback
methods are thin delegates over free functions in ``transcription_device``,
``transcription_cuda_probe``, ``transcription_download`` and
``transcription_fallback``. These tests pin that wiring so a future edit
that silently diverges the facade from the extracted bodies (or drops a
back-compat re-export) trips loudly:

* delegate-resolution identity — ``transcription.<name>_impl`` IS the
  canonical free function in the sibling module;
* the late-binding contract — module-global reads inside the extracted
  bodies resolve through ``voice_typer.server.transcription`` at call
  time, so monkeypatching the facade path changes behavior;
* the re-export surface — ``AUTO_CUDA_BEAM_SIZE`` / ``_auto_beam_size``
  stay importable from the facade (see ``test_transcription_beam_size``).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports(monkeypatch):
    """Mock faster_whisper + ctranslate2 so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 1
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


class TestDelegateResolutionIdentity:
    """The facade's ``*_impl`` bindings must BE the canonical functions."""

    def test_cuda_probe_delegates(self):
        import voice_typer.server.transcription as facade
        from voice_typer.server import transcription_cuda_probe as mod

        assert facade._probe_cuda_runtime_impl is mod.probe_cuda_runtime
        assert facade._warm_up_model_impl is mod.warm_up_model

    def test_device_delegates(self):
        import voice_typer.server.transcription as facade
        from voice_typer.server import transcription_device as mod

        assert facade._resolve_device_impl is mod.resolve_device
        assert facade._resolve_device_once_impl is mod.resolve_device_once
        assert facade._apply_auto_beam_size_impl is mod.apply_auto_beam_size

    def test_download_delegates(self):
        import voice_typer.server.transcription as facade
        from voice_typer.server import transcription_download as mod

        assert facade._probe_cache_impl is mod.probe_cache
        assert facade._require_model_downloaded_impl is mod.require_model_downloaded
        assert facade._whisper_size_cached_impl is mod.whisper_size_cached

    def test_fallback_delegates(self):
        import voice_typer.server.transcription as facade
        from voice_typer.server import transcription_fallback as mod

        assert facade._with_gpu_fallback_impl is mod.with_gpu_fallback
        assert facade._is_gpu_runtime_error_impl is mod.is_gpu_runtime_error
        assert facade._transcribe_with_fallback_impl is mod.transcribe_with_fallback

    def test_engine_methods_call_the_extracted_bodies(self, monkeypatch):
        """Calling the engine method dispatches into the extracted module.

        Patching the facade's ``_impl`` binding is visible through the
        method call — proving the delegate is live wiring, not a copy.
        """
        import voice_typer.server.transcription as facade
        from voice_typer.server.transcription import TranscriptionEngine

        seen = []
        monkeypatch.setattr(
            facade,
            "_is_gpu_runtime_error_impl",
            lambda engine, exc: seen.append(exc) or True,
        )
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cuda"
        assert engine._is_gpu_runtime_error(RuntimeError("anything")) is True
        assert len(seen) == 1
        assert isinstance(seen[0], RuntimeError)


class TestLateBindingThroughFacade:
    """Module-global reads in the extracted bodies resolve via the facade."""

    def test_resolve_device_reads_patched_cuda_runtime_gate(self, monkeypatch):
        """``_cuda_runtime_available`` patched on the facade path is honored
        by the extracted ``resolve_device`` body (Windows fast-path gate)."""
        from voice_typer.server.transcription import TranscriptionEngine

        monkeypatch.setattr(
            "voice_typer.server.transcription._cuda_runtime_available",
            lambda: False,
        )
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        device, compute = engine._resolve_device("cuda")
        assert (device, compute) == ("cpu", "int8")

    def test_apply_auto_beam_size_reads_facade_auto_beam(self, monkeypatch):
        """``_auto_beam_size`` stays canonical in the facade module — the
        extracted ``apply_auto_beam_size`` reads it via late binding."""
        import voice_typer.server.transcription as facade
        from voice_typer.server.transcription import TranscriptionEngine

        monkeypatch.setattr(facade, "_auto_beam_size", lambda model_size, device: 7)
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._beam_size_auto = True
        engine.model_size = "small.en"
        engine._device = "cuda"
        engine.beam_size = 1
        engine._apply_auto_beam_size()
        assert engine.beam_size == 7


class TestFacadeReExports:
    """Back-compat import surface pinned (no behavior assertions)."""

    def test_beam_size_symbols_importable_from_facade(self):
        from voice_typer.server.transcription import (  # noqa: F401
            AUTO_CUDA_BEAM_SIZE,
            TranscriptionEngine,
            _auto_beam_size,
        )

        assert AUTO_CUDA_BEAM_SIZE == 5
        assert _auto_beam_size("large-v3-turbo", "cuda") == AUTO_CUDA_BEAM_SIZE
        assert _auto_beam_size("tiny", "cuda") == 1
        assert _auto_beam_size("large-v3-turbo", "cpu") == 1

    def test_downloader_and_release_re_exports(self):
        from voice_typer.server import transcription as facade
        from voice_typer.server.asr_utils import (
            _check_disk_space_for_download,
            _download_with_retry,
            release_gpu_memory,
        )

        assert facade._download_with_retry is _download_with_retry
        assert facade._check_disk_space_for_download is _check_disk_space_for_download
        assert facade.release_gpu_memory is release_gpu_memory
