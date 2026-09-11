"""Regression pins: the dead three-layer runtime filter-toggle API.

``set_filter_enabled`` was scaffolded at THREE layers (the
``audio_chain_builder`` module function, the ``AudioProcessor``
method, and the ``FilterChain`` method) as the server-side surface for
a renderer-facing runtime filter-toggle IPC command. That command was
never wired, the docstrings on all three layers stated the IPC
handler was "NOT wired in this change", and no production or test
caller ever invoked any layer (repo-wide search found only the three
definitions). The dead layers were deleted (E15 dead-code removal);
these tests pin the ABSENCE so the scaffold cannot silently creep back.

The SURVIVING mechanism is also pinned: the per-filter ``enabled`` flag
(:attr:`voice_typer.server.audio_filters.base.AudioFilter.enabled`)
consulted by :meth:`FilterChain.process` (a disabled filter is skipped
without calling its ``process`` method, so internal state survives the
bypass window). That flag remains the architectural extension point on
the ABC; only the unreachable toggle API on top of it was removed.
"""

from __future__ import annotations

import numpy as np
from voice_typer.server.audio_filters import FilterChain


class TestFilterToggleApiRemoved:
    """No layer of the runtime filter-toggle API may reappear."""

    def test_chain_builder_module_function_removed(self) -> None:
        """``audio_chain_builder`` must not define a module-level
        ``set_filter_enabled``, it had zero callers and duplicated the
        chain-level method."""
        import voice_typer.server.audio_chain_builder as mod

        assert not hasattr(mod, "set_filter_enabled"), (
            "audio_chain_builder re-introduced the dead module-level "
            "set_filter_enabled wrapper. It had zero callers, wire a real "
            "IPC command first if a runtime toggle is needed."
        )

    def test_audio_processor_method_removed(self) -> None:
        """``AudioProcessor`` must not define ``set_filter_enabled`` —
        the method had zero callers (IPC never wired)."""
        from voice_typer.server.audio_processor import AudioProcessor

        assert not hasattr(AudioProcessor, "set_filter_enabled"), (
            "AudioProcessor re-introduced the dead set_filter_enabled "
            "method. It had zero callers, wire a real IPC command first "
            "if a runtime toggle is needed."
        )

    def test_filter_chain_method_removed(self) -> None:
        """``FilterChain`` must not define ``set_filter_enabled``, the
        method had zero callers outside the two deleted wrapper layers."""
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
    """The per-filter ``enabled`` flag consulted by ``process`` stays.

    This is the load-bearing half of the removed API: a filter whose
    ``enabled`` flag is False must be skipped without calling its
    ``process`` method (state-preserving bypass).
    """

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
