"""Whisper beam-width configuration: automatic device/model-aware default.

Covers the ``whisper_beam_size`` config surface end-to-end:

- the engine resolves a wide accuracy-biased beam automatically on CUDA
  for non-tiny models while keeping the snappy greedy default on tiny
  models and every CPU path (including GPU→CPU fallbacks);
- an explicitly configured width (legacy ``beam_size`` kwarg or the
  preferred ``whisper_beam_size`` field) always wins and is never
  downgraded by the automatic resolution;
- the SEC-002 IPC allowlist accepts the field so the renderer can write
  it, with the same range rules as the legacy field.
"""

import pytest
from voice_typer.server.transcription import (
    AUTO_CUDA_BEAM_SIZE,
    TranscriptionEngine,
    _auto_beam_size,
)


def _engine_with_device(model_size: str, device: str, **kwargs) -> TranscriptionEngine:
    engine = TranscriptionEngine(model_size=model_size, device=device, **kwargs)
    # Bypass the real (expensive) CUDA probe — tests pin the beam-width
    # policy against an already-resolved device, not the probe itself.
    engine._requested_device = None
    engine._device = "cuda" if device == "cuda" else "cpu"
    return engine


class TestAutoBeamSizePolicy:
    def test_cuda_non_tiny_model_gets_wide_beam(self):
        assert _auto_beam_size("large-v3-turbo", "cuda") == AUTO_CUDA_BEAM_SIZE

    def test_cuda_tiny_model_stays_greedy(self):
        assert _auto_beam_size("tiny", "cuda") == 1

    def test_tiny_english_variant_stays_greedy(self):
        assert _auto_beam_size("tiny.en", "cuda") == 1

    @pytest.mark.parametrize("device", ["cpu", "auto", ""])
    def test_non_cuda_devices_stay_greedy(self, device):
        assert _auto_beam_size("large-v3-turbo", device) == 1


class TestEngineBeamResolution:
    def test_auto_upgrade_applied_on_resolved_cuda(self):
        engine = _engine_with_device("large-v3-turbo", "cuda")
        assert engine.beam_size == 1  # construction-time default
        engine._apply_auto_beam_size()
        assert engine.beam_size == AUTO_CUDA_BEAM_SIZE

    def test_auto_noop_for_tiny_on_cuda(self):
        engine = _engine_with_device("tiny", "cuda")
        engine._apply_auto_beam_size()
        assert engine.beam_size == 1

    def test_legacy_kwarg_beats_auto_and_survives_fallback(self):
        engine = _engine_with_device("large-v3-turbo", "cpu", beam_size=2)
        engine._apply_auto_beam_size()
        assert engine.beam_size == 2

    def test_whisper_beam_size_config_beats_auto(self):
        class _Cfg:
            whisper_beam_size = 3

        engine = _engine_with_device("large-v3-turbo", "cuda", config=_Cfg())
        assert engine._beam_size_auto is False
        engine._apply_auto_beam_size()
        assert engine.beam_size == 3

    def test_cpu_fallback_downgrades_auto_beam(self):
        engine = _engine_with_device("large-v3-turbo", "cuda")
        engine._apply_auto_beam_size()
        assert engine.beam_size == AUTO_CUDA_BEAM_SIZE
        # GPU→CPU fallback path re-resolves after switching devices.
        engine._device = "cpu"
        engine._apply_auto_beam_size()
        assert engine.beam_size == 1

    def test_explicit_beam_not_downgraded_on_fallback(self):
        engine = _engine_with_device("large-v3-turbo", "cuda", beam_size=4)
        engine._apply_auto_beam_size()
        assert engine.beam_size == 4
        engine._device = "cpu"
        engine._apply_auto_beam_size()
        assert engine.beam_size == 4


class TestWhisperBeamSizeAllowlist:
    def test_field_is_renderer_writable(self):
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"whisper_beam_size": 5})
        assert errors == []
        assert validated["whisper_beam_size"] == 5

    @pytest.mark.parametrize("bad_value", [0, 11, "3", 2.5])
    def test_invalid_values_rejected(self, bad_value):
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"whisper_beam_size": bad_value})
        assert errors, f"expected rejection for {bad_value!r}"
        assert any("whisper_beam_size" in e for e in errors)

    def test_default_one_means_automatic(self):
        """``1`` is the sentinel for the automatic default, so it must
        validate cleanly (the engine resolves it per device/model)."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"whisper_beam_size": 1})
        assert errors == []
        assert validated["whisper_beam_size"] == 1
