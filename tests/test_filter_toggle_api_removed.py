"""Regression pins: the dead three-layer runtime filter-toggle API."""

from __future__ import annotations

import numpy as np
from voice_typer.server.audio_filters import FilterChain


class TestFilterToggleApiRemoved:
    """No layer of the runtime filter-toggle API may reappear."""

    def test_chain_builder_module_function_removed(self) -> None:
        """``audio_chain_builder`` must not define a module-level"""
        import voice_typer.server.audio_chain_builder as mod

        assert not hasattr(mod, "set_filter_enabled"), (
            "audio_chain_builder re-introduced the dead module-level "
            "set_filter_enabled wrapper. It had zero callers, wire a real "
            "IPC command first if a runtime toggle is needed."
        )

    def test_audio_processor_method_removed(self) -> None:
        """``AudioProcessor`` must not define ``set_filter_enabled`` —"""
        from voice_typer.server.audio_processor import AudioProcessor

        assert not hasattr(AudioProcessor, "set_filter_enabled"), (
            "AudioProcessor re-introduced the dead set_filter_enabled "
            "method. It had zero callers, wire a real IPC command first "
            "if a runtime toggle is needed."
        )

    def test_filter_chain_method_removed(self) -> None:
        """``FilterChain`` must not define ``set_filter_enabled``, the"""
        assert not hasattr(FilterChain, "set_filter_enabled"), (
            "FilterChain re-introduced the dead set_filter_enabled "
            "method. It had zero callers, wire a real IPC command first "
            "if a runtime toggle is needed."
        )


class _StubFilter:
    """Minimal AudioFilter stand-in for the process-skip pin below."""

    name = "Stub"

    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    def process(self, audio, sample_rate):  # noqa: ANN001, ANN202
        self.calls += 1
        return audio * 0.5

    def reset(self) -> None:
        pass


class TestEnabledFlagMechanismSurvives:
    """The per-filter ``enabled`` flag consulted by ``process`` stays."""

    def test_process_skips_disabled_filter(self) -> None:
        stub = _StubFilter()
        stub.enabled = False
        chain = FilterChain([stub])

        chunk = np.ones(4, dtype=np.float32)
        out = chain.process(chunk, 16000)

        assert out is chunk  # passthrough, untouched
        assert stub.calls == 0, "disabled filter must not be called"

    def test_process_runs_enabled_filter(self) -> None:
        stub = _StubFilter()
        chain = FilterChain([stub])

        chunk = np.ones(4, dtype=np.float32)
        out = chain.process(chunk, 16000)

        np.testing.assert_array_equal(out, np.full(4, 0.5, dtype=np.float32))
        assert stub.calls == 1
