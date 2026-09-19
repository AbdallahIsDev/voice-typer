"""Config-directory patching and fake-config helpers shared across test files."""

from __future__ import annotations

from pathlib import Path

__all__ = ["patch_config_dir_refs", "FakeConfig"]


def patch_config_dir_refs(monkeypatch, path: Path) -> None:
    """Redirect every ``_config_dir`` binding to *path* for one test."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: path)
    import voice_typer.server._paths as _paths_mod

    monkeypatch.setattr(_paths_mod, "_config_dir", lambda: path)
    import voice_typer.server.config_internals.paths as _paths_impl_mod

    monkeypatch.setattr(_paths_impl_mod, "_config_dir", lambda: path)
    import voice_typer.server.logging_setup as _logging_setup_mod

    monkeypatch.setattr(_logging_setup_mod, "_config_dir", lambda: path)
    import voice_typer.server.startup_sequence._phases_early as _phases_early_mod

    monkeypatch.setattr(_phases_early_mod, "_config_dir", lambda: path)


class FakeConfig:
    """Minimal config object for audio-filter-chain tests."""

    def __init__(self, **kwargs):
        # ADR 0007 defaults
        self.audio_preset = "custom"
        self.noise_filter_enabled = True
        self.noise_filter_highpass = True
        self.noise_filter_highpass_cutoff_hz = 80.0
        self.noise_filter_gate = True
        self.noise_filter_gate_threshold = 0.003
        self.noise_filter_gate_hold_ms = 200.0
        self.noise_filter_gate_open_threshold_db = -26.0
        self.noise_filter_gate_close_threshold_db = -32.0
        self.noise_filter_gate_attack_ms = 25.0
        self.noise_filter_gate_release_ms = 150.0
        self.noise_filter_rnnoise = True
        self.noise_filter_post_capture = False
        self.noise_suppression_method = "none"  # skip RNNoise in tests
        self.noise_filter_eq = True
        self.noise_filter_eq_low_db = -3.0
        self.noise_filter_eq_mid_db = 3.0
        self.noise_filter_eq_high_db = 2.0
        self.noise_filter_compressor = True
        self.noise_filter_compressor_threshold_db = -18.0
        self.noise_filter_compressor_ratio = 3.0
        self.noise_filter_compressor_attack_ms = 6.0
        self.noise_filter_compressor_release_ms = 60.0
        self.noise_filter_compressor_output_gain_db = 0.0
        self.noise_filter_limiter = True
        self.noise_filter_limiter_ceiling_db = -6.0
        self.noise_filter_limiter_release_ms = 60.0
        self.noise_filter_notch = False
        self.noise_filter_notch_frequency_hz = 0.0
        self.sample_rate = 16000
        for k, v in kwargs.items():
            setattr(self, k, v)
