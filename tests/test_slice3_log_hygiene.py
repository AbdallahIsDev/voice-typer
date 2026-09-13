"""Log hygiene: single choke-point per event, fail-loud registry.

Covers the dedupe fixes (audio chain pair, suppressor once-per-process,
resource single-drive collapse) with mocks, no real audio or network.
"""

from __future__ import annotations

import logging
import types


def _config_with_filters(**overrides: object) -> types.SimpleNamespace:
    base = {
        "audio_preset": "studio",
        "noise_filter_enabled": True,
        "noise_filter_highpass": True,
        "noise_filter_highpass_cutoff_hz": 80,
        "noise_suppression_method": "none",
        "noise_filter_gate": False,
        "noise_filter_gate_open_threshold_db": -30.0,
        "noise_filter_gate_close_threshold_db": -40.0,
        "noise_filter_gate_attack_ms": 5.0,
        "noise_filter_gate_hold_ms": 200.0,
        "noise_filter_gate_release_ms": 100.0,
        "noise_filter_gate_adaptive": False,
        "noise_filter_eq": False,
        "noise_filter_eq_low_db": 0.0,
        "noise_filter_eq_mid_db": 0.0,
        "noise_filter_eq_high_db": 0.0,
        "noise_filter_compressor": False,
        "noise_filter_compressor_threshold_db": -18.0,
        "noise_filter_compressor_ratio": 3.0,
        "noise_filter_compressor_attack_ms": 5.0,
        "noise_filter_compressor_release_ms": 100.0,
        "noise_filter_compressor_output_gain_db": 0.0,
        "noise_filter_limiter": False,
        "noise_filter_limiter_ceiling_db": -1.0,
        "noise_filter_limiter_release_ms": 50.0,
        "noise_filter_notch": False,
        "noise_filter_notch_frequency_hz": 50.0,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


class TestAudioProcessorSingleInfoPerBuild:
    def test_fresh_build_emits_one_chain_info(self, caplog):
        from voice_typer.server.audio_processor import AudioProcessor

        cfg = _config_with_filters()
        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_processor"),
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_chain_builder"),
        ):
            caplog.clear()
            AudioProcessor(cfg, sample_rate=16000)
        chain_infos = [
            r for r in caplog.records if r.levelno == logging.INFO and "[AUDIO-CHAIN] Built chain" in r.getMessage()
        ]
        proc_infos = [
            r for r in caplog.records if r.levelno == logging.INFO and "[AUDIO-PROC] chain built" in r.getMessage()
        ]
        assert len(chain_infos) == 1, "builder must emit exactly one INFO per build"
        assert proc_infos == [], "wrapper must not duplicate the builder INFO"

    def test_rebuild_emits_one_chain_info(self, caplog):
        from voice_typer.server.audio_processor import AudioProcessor

        cfg = _config_with_filters()
        proc = AudioProcessor(cfg, sample_rate=16000, quiet=True)
        caplog.clear()
        cfg2 = _config_with_filters(noise_filter_gate=True)
        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_processor"),
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_chain_builder"),
        ):
            proc.rebuild_from_config(cfg2)
        proc_infos = [
            r for r in caplog.records if r.levelno == logging.INFO and "[AUDIO-PROC] chain rebuilt" in r.getMessage()
        ]
        assert proc_infos == [], "rebuild wrapper must stay at DEBUG (builder owns the INFO)"


class TestNoiseSuppressorOncePerProcess:
    def test_second_init_drops_to_debug(self, caplog, monkeypatch):
        import voice_typer.server.audio_filters.noise_suppressor as ns

        monkeypatch.setattr(ns, "_backend_ready_logged", set())

        class _FakeRNNoise:
            def __init__(self, sample_rate: int = 48000) -> None:
                pass

        import sys

        fake_mod = types.SimpleNamespace(RNNoise=_FakeRNNoise)
        monkeypatch.setitem(sys.modules, "pyrnnoise", fake_mod)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_filters.noise_suppressor"):
            caplog.clear()
            ns.NoiseSuppressor(method="rnnoise", sample_rate=16000)
            ns.NoiseSuppressor(method="rnnoise", sample_rate=16000)
        infos = [r for r in caplog.records if r.levelno == logging.INFO and "RNNoise backend ready" in r.getMessage()]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG and "RNNoise backend ready" in r.getMessage()]
        assert len(infos) == 1, "first init logs INFO once per process"
        assert len(debugs) == 1, "repeat init drops to DEBUG"


class TestResourceProbeSingleDrive:
    def test_three_paths_same_drive_emit_one_disk_info(self, caplog, monkeypatch, tmp_path):
        import pathlib

        from voice_typer.server import resource_probe

        # Three distinct dirs that resolve to the same drive anchor.
        cfg_dir = tmp_path / "cfg"
        home_dir = tmp_path / "home"
        hf_dir = tmp_path / "hf"
        for d in (cfg_dir, home_dir, hf_dir):
            d.mkdir()
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: cfg_dir)
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home_dir))
        monkeypatch.setenv("HF_HOME", str(hf_dir))
        monkeypatch.setattr("psutil.virtual_memory", lambda: types.SimpleNamespace(available=4 * 1024**3))
        monkeypatch.delattr("os.statvfs", raising=False)

        import shutil

        class _Usage:
            total = 100 * 1024**3
            used = 90 * 1024**3
            free = 10 * 1024**3

        monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage())

        with caplog.at_level(logging.INFO, logger="voice_typer.server.resource_probe"):
            caplog.clear()
            resource_probe.check_resources()
        disk_infos = [r for r in caplog.records if "[RESOURCE] Disk free" in r.getMessage()]
        assert len(disk_infos) == 1, f"same-drive paths must collapse to one line, got {len(disk_infos)}"


class TestRegistryFailLoud:
    def test_get_active_returns_none_when_only_unloaded_remains(self):
        from unittest.mock import MagicMock

        from voice_typer.server.asr_registry import AsrBackendRegistry

        class _Config:
            asr_backend = "parakeet"

        registry = AsrBackendRegistry(_Config())
        backend = MagicMock()
        backend.is_loaded = False
        registry.register("parakeet", backend)
        assert registry.get_active() is None, "must never serve an unloaded backend"
