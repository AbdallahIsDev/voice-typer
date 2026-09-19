"""Per-domain regression tests for AudioWindow __eq__ layered comparison (PERF-EQ)."""

from __future__ import annotations

import numpy as np


class TestAudioWindowEqualityUsesLayeredFastPaths:
    """PERF-EQ (PARTIALLY FIXED, intentional)."""

    def _make_window(self, audio=None, start=0.0, end=1.0):
        from voice_typer.server.streaming import AudioWindow

        if audio is None:
            audio = np.full(16000, 0.1, dtype=np.float32)
        return AudioWindow(audio=audio, start_seconds=start, end_seconds=end)

    def test_eq_false_disables_dataclass_auto_eq(self):
        """custom ``__eq__`` below is the only equality path."""

        from voice_typer.server.streaming import AudioWindow

        fields = AudioWindow.__dataclass_params__
        assert fields.eq is False, (
            "AudioWindow must be declared with eq=False so the dataclass-"
            "generated __eq__ doesn't override the custom one"
        )

    def test_layer_1_scalar_mismatch_short_circuits(self):
        """Different ``start_seconds`` or ``end_seconds`` ⇒ not equal,"""
        from voice_typer.server.streaming import AudioWindow

        shared_audio = np.full(16000, 0.1, dtype=np.float32)
        a = AudioWindow(audio=shared_audio, start_seconds=0.0, end_seconds=1.0)
        b = AudioWindow(audio=shared_audio, start_seconds=0.5, end_seconds=1.0)
        c = AudioWindow(audio=shared_audio, start_seconds=0.0, end_seconds=2.0)

        assert a != b
        assert a != c
        assert b != c

    def test_layer_2_identity_short_circuits(self):
        """Same underlying buffer + same scalars ⇒ equal by reference,"""
        from voice_typer.server.streaming import AudioWindow

        shared_audio = np.full(16000, 0.1, dtype=np.float32)
        a = AudioWindow(audio=shared_audio, start_seconds=0.0, end_seconds=1.0)
        b = AudioWindow(audio=shared_audio, start_seconds=0.0, end_seconds=1.0)

        assert a is not b  # different dataclass instances
        assert a.audio is b.audio  # but same underlying buffer
        assert a == b

    def test_layer_3_shape_mismatch_short_circuits(self):
        """Same scalars, different array shapes ⇒ not equal, without"""
        from voice_typer.server.streaming import AudioWindow

        a = AudioWindow(
            audio=np.full(16000, 0.1, dtype=np.float32),
            start_seconds=0.0,
            end_seconds=1.0,
        )
        b = AudioWindow(
            audio=np.full(8000, 0.1, dtype=np.float32),
            start_seconds=0.0,
            end_seconds=1.0,
        )
        assert a != b

    def test_layer_4_array_equal_fallback_for_equal_content(self):
        """Same scalars, different buffers, identical content ⇒ equal"""
        from voice_typer.server.streaming import AudioWindow

        a = AudioWindow(
            audio=np.arange(16000, dtype=np.float32) * 0.001,
            start_seconds=0.0,
            end_seconds=1.0,
        )
        b = AudioWindow(
            audio=np.arange(16000, dtype=np.float32) * 0.001,
            start_seconds=0.0,
            end_seconds=1.0,
        )
        assert a is not b
        assert a.audio is not b.audio
        assert a == b

    def test_layer_4_array_equal_fallback_detects_content_mismatch(self):
        """Same scalars, same shape, different content ⇒ not equal"""
        from voice_typer.server.streaming import AudioWindow

        a = AudioWindow(
            audio=np.zeros(16000, dtype=np.float32),
            start_seconds=0.0,
            end_seconds=1.0,
        )
        b = AudioWindow(
            audio=np.ones(16000, dtype=np.float32),
            start_seconds=0.0,
            end_seconds=1.0,
        )
        assert a != b

    def test_eq_returns_notimplemented_for_non_audio_window(self):
        """``__eq__`` must return ``NotImplemented`` (which Python"""

        a = self._make_window()
        # Compare to an unrelated type, Python falls back to identity.
        result = a.__eq__("not a window")
        assert result is NotImplemented

        # Compare to None, Python uses identity (False).
        assert (a == None) is False  # noqa: E711

    def test_hash_is_on_scalar_fields_only(self):
        """``__hash__`` must be computed from scalar fields only —"""
        from voice_typer.server.streaming import AudioWindow

        a = AudioWindow(
            audio=np.zeros(16000, dtype=np.float32),
            start_seconds=1.0,
            end_seconds=2.0,
        )
        b = AudioWindow(
            audio=np.ones(16000, dtype=np.float32),
            start_seconds=1.0,
            end_seconds=2.0,
        )
        assert hash(a) == hash(b), "AudioWindow.__hash__ must depend only on scalar fields, not audio"

    def test_docstring_documents_layered_comparison(self):
        """explain why ``np.array_equal`` is kept (test reliance)."""
        from voice_typer.server.streaming import AudioWindow

        doc = AudioWindow.__doc__ or ""
        doc_lower = doc.lower()
        assert "layered" in doc_lower or "scalar" in doc_lower, (
            "AudioWindow docstring must mention the layered comparison"
        )
        assert "np.array_equal" in doc or "array_equal" in doc_lower, (
            "AudioWindow docstring must explicitly mention np.array_equal "
            "so future maintainers know it's intentional, not a regression"
        )
