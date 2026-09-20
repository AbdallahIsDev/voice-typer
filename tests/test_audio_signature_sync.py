"""Sync guard: the rebuild signature must cover every config field the chain reads."""

from __future__ import annotations

from unittest.mock import patch

import voice_typer.server.audio_chain_builder as builder
from voice_typer.server.audio_chain_builder import AUDIO_CHAIN_CONFIG_FIELDS, build_chain
from voice_typer.server.audio_processor import _CONFIG_SIGNATURE_FIELDS, _config_signature


class _RecordingConfig:
    """Stand-in config that records every attribute read."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        self.reads.append(name)
        if name == "noise_suppression_method":
            return "rnnoise"
        if name.endswith(("_hz", "_db", "_ms", "_ratio")):
            return 1.0
        return True


def _stub(*args, **kwargs):
    return object()


_FILTER_ATTRS = (
    "NotchFilter",
    "HighPassFilter",
    "NoiseSuppressor",
    "NoiseGate",
    "Equalizer",
    "Compressor",
    "Limiter",
)


class TestSignatureIsBuilderCanonical:
    def test_alias_is_the_same_tuple_object(self) -> None:
        assert _CONFIG_SIGNATURE_FIELDS is AUDIO_CHAIN_CONFIG_FIELDS

    def test_signature_covers_a_known_field(self) -> None:
        assert "noise_filter_gate_adaptive" in _CONFIG_SIGNATURE_FIELDS


class TestBuildChainReadsSubsetOfSignature:
    def test_every_config_read_is_in_the_signature(self) -> None:
        """A new ``config.<field>`` in build_chain without a signature entry fails loudly."""
        cfg = _RecordingConfig()
        with (
            patch.multiple(builder, **{name: _stub for name in _FILTER_ATTRS}),
        ):
            build_chain(cfg, 16000, quiet=True)
        assert cfg.reads, "expected build_chain to read at least one config field"
        unknown = set(cfg.reads) - set(_CONFIG_SIGNATURE_FIELDS)
        assert not unknown, (
            "build_chain reads config fields missing from the rebuild signature, "
            f"live changes to {sorted(unknown)} would not rebuild the chain: "
            "add them to AUDIO_CHAIN_CONFIG_FIELDS"
        )

    def test_each_signature_field_flips_the_signature(self) -> None:
        """Every listed field (except the preset selector) actually affects the signature."""
        from voice_typer.server.config._schema import _ConfigSchema

        for field in _CONFIG_SIGNATURE_FIELDS:
            if field == "audio_preset":
                continue
            first = _ConfigSchema()
            second = _ConfigSchema()
            current = getattr(first, field)
            setattr(second, field, not current if isinstance(current, bool) else "changed")
            assert _config_signature(first, 16000) != _config_signature(second, 16000), (
                f"{field} is listed in the signature but does not change it"
            )
