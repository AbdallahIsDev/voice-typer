"""Config-directory patching and fake-config helpers shared across test files.

Single authoritative place that knows WHICH module references must be
patched to redirect the app's config directory in tests, plus the
canonical minimal ``Config`` stand-in for audio-filter-chain tests.
Every test that needs a fake config dir should go through
:func:`patch_config_dir_refs` (directly or via the ``tmp_config_dir``
fixture in ``tests/conftest.py``) instead of re-listing the patch
targets inline, and every audio-filter-chain test that needs a config
object should go through :class:`FakeConfig` instead of redefining a
local copy.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["patch_config_dir_refs", "FakeConfig"]


def patch_config_dir_refs(monkeypatch, path: Path) -> None:
    """Redirect every ``_config_dir`` binding to *path* for one test.

    Patches all three known bindings:

    - ``voice_typer.server.config._config_dir`` — the canonical accessor;
      app.py routes its internal calls through ``_resolve_config_dir()``
      (call-time indirection), so this patch intercepts every app path;
    - ``voice_typer.server.app._config_dir`` — belt-and-suspenders for
      consumers that deliberately resolve via the app module at call
      time (``single_instance`` reads ``_app_module._config_dir()``);
    - ``voice_typer.server._paths._config_dir`` — the lazy resolver's
      memoized callable (once a previous test has triggered resolution,
      this attribute pins the REAL function and silently ignores the
      canonical-name patch).

    Works with both ``monkeypatch`` fixtures and manual
    ``pytest.MonkeyPatch`` instances.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: path)
    import voice_typer.server._paths as _paths_mod

    monkeypatch.setattr(_paths_mod, "_config_dir", lambda: path)


class FakeConfig:
    """Minimal config object for audio-filter-chain tests.

    Carries the ADR-0007 defaults for every ``noise_filter_*`` /
    ``noise_suppression_method`` / ``audio_preset`` field the chain
    builder consults; ``sample_rate`` mirrors Whisper's native rate.
    Extra/overriding fields are set from ``**kwargs`` so individual
    tests can flip single knobs (e.g. ``noise_filter_notch=True``)
    without a subclass.

    This is the SINGLE copy — it was previously duplicated as local
    ``FakeConfig`` classes in ``tests/test_audio_processor.py`` and
    ``tests/test_audio_processor_set_sample_rate.py``, which drifted
    (the latter lacked three fields the chain builder never reads).
    """

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
