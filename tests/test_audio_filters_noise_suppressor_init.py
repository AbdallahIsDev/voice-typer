"""RNNoise model-loading + lazy-resampler-init
coverage for ``voice_typer.server.audio_filters.noise_suppressor``.

The ``NoiseSuppressor`` filter has three init-time surfaces that were
previously under-tested:

  1. **Lazy resampler memoization** — ``_ensure_resamplers(sample_rate)``
     is called lazily from ``_process_rnnoise`` (NOT from ``__init__``).
     It creates the up/down ``_StreamingResampler`` pair ONCE per sample
     rate and memoizes them in ``self._upsampler`` /
     ``self._downsampler``. A second ``process()`` call at the same rate
     must NOT recreate the resamplers (the FIR filter design inside
     ``_StreamingResampler.__init__`` is expensive — ``scipy.signal.firwin``
     with a 30-tap kernel).

  2. **Sample-rate-driven resampler construction** — when the
     ``process()`` sample rate is NOT the RNNoise native rate
     (``RNNOISE_SAMPLE_RATE == 48000``), the up/down resampler pair IS
     constructed (to convert source↔48k). At the native rate (48k) both
     resamplers stay ``None`` (no resampling needed).

  3. **RNNoise init failure → clear degraded reason** — when the
     ``pyrnnoise`` import succeeds but ``RNNoise(sample_rate=48000)``
     raises (e.g. model file missing, native library load failure), the
     ``_init_rnnoise`` helper catches the exception and sets
     ``is_degraded=True`` with a ``degraded_reason`` that surfaces the
     original exception message. Pre-fix the catch was ``except
     Exception`` with a generic log; the test pins that the exception
     message reaches ``degraded_reason`` so the user can act on it
     (e.g. "model file not found" → reinstall the package).

All RNNoise / pyrnnoise interactions are mocked — no real model file
is loaded, no real inference runs. The tests inject a fake
``pyrnnoise`` module via ``sys.modules`` so ``from pyrnnoise import
RNNoise`` resolves to the test stub.

The ``scipy`` requirement (for ``_StreamingResampler``'s FIR filter
design) is handled via ``pytest.importorskip`` — matches the
convention in ``tests/test_noise_suppressor_resampler.py``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

scipy = pytest.importorskip("scipy.signal")  # skips the whole module if missing

from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.noise_suppressor import (  # noqa: E402
    NoiseSuppressor,
    _StreamingResampler,
)

# ── Fake pyrnnoise module helpers ─────────────────────────────────────


class _FakeRNNoise:
    """Minimal stand-in for ``pyrnnoise.RNNoise``.

    The real ``RNNoise`` class loads a model file and runs inference on
    480-sample int16 frames. The fake just records the construction args
    and returns the input frame unchanged from ``denoise_frame`` so the
    ``_process_rnnoise`` loop can exercise its full path (clip → scale →
    cast → denoise → cast back) without a real model.
    """

    def __init__(self, sample_rate: int = RNNOISE_SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.channels = 1  # set by _process_rnnoise before denoise_frame
        self.init_calls: list[int] = [sample_rate]

    def denoise_frame(self, frame_i16: np.ndarray) -> tuple[float, np.ndarray]:
        # pyrnnoise returns (speech_prob, cleaned_i16); return the input
        # unchanged so the test can predict the output shape.
        return (0.0, frame_i16)


def _install_fake_pyrnnoise(monkeypatch: pytest.MonkeyPatch) -> _FakeRNNoise:
    """Inject a fake ``pyrnnoise`` module into ``sys.modules`` so
    ``from pyrnnoise import RNNoise`` resolves to ``_FakeRNNoise``.

    Returns the ``_FakeRNNoise`` class (test can inspect ``init_calls``
    on the instance after construction via ``ns._backend.init_calls``).

    ``monkeypatch.setitem(sys.modules, ...)`` ensures the real
    (broken-on-this-host) ``pyrnnoise`` module is shadowed only for the
    duration of the test — the original ``sys.modules`` entry is
    restored on teardown.
    """
    fake_module = MagicMock()
    fake_module.RNNoise = _FakeRNNoise
    monkeypatch.setitem(sys.modules, "pyrnnoise", fake_module)
    return _FakeRNNoise


# ── Lazy resampler init memoization ───────────────────────────────────


class TestLazyResamplerInitMemoization:
    """``_ensure_resamplers`` runs ONCE per sample rate and
    memoizes the resampler pair. A second ``process()`` call at the
    same rate must NOT recreate the resamplers (the FIR filter design
    is expensive).
    """

    def test_lazy_init_runs_once_and_memoized(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Construct a NoiseSuppressor with a stubbed RNNoise backend,
        spy on ``_StreamingResampler.__init__`` to count constructions,
        call ``process(audio, 16000)`` twice, and assert:

          1. The resamplers were constructed (``_upsampler`` /
             ``_downsampler`` are not ``None``) — the lazy init ran on
             the first ``process()`` call.
          2. ``_StreamingResampler.__init__`` was called exactly TWICE
             (once for the upsampler, once for the downsampler) — NOT
             four times. A second ``process()`` call at the same rate
             must hit the memoization guard (``self._resampler_rate ==
             sample_rate and self._upsampler is not None``) and return
             early without recreating the resamplers.
          3. The resampler object identity is stable across the two
             ``process()`` calls (same object, not a fresh one).
        """
        _install_fake_pyrnnoise(monkeypatch)
        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)
        # The RNNoise backend was constructed (lazy import succeeded via
        # the fake module).
        assert ns._backend is not None, (
            "RNNoise backend must be constructed when pyrnnoise is available (fake module injected)"
        )
        assert ns.is_degraded is False, "NoiseSuppressor must NOT be degraded when RNNoise init succeeds"

        # Spy on _StreamingResampler.__init__ to count constructions.
        init_calls: list[tuple[int, int]] = []
        original_init = _StreamingResampler.__init__

        def _counting_init(self, up: int, down: int) -> None:
            init_calls.append((up, down))
            original_init(self, up, down)

        monkeypatch.setattr(_StreamingResampler, "__init__", _counting_init)

        # First process() call — lazy resampler init runs.
        audio = np.random.randn(480).astype(np.float32) * 0.1
        result1 = ns.process(audio, 16000)
        # Capture the resampler identities after the first call.
        upsampler_after_first = ns._upsampler
        downsampler_after_first = ns._downsampler
        assert upsampler_after_first is not None, "upsampler must be constructed after the first process() call"
        assert downsampler_after_first is not None, "downsampler must be constructed after the first process() call"

        # Second process() call — memoization guard must skip recreation.
        result2 = ns.process(audio, 16000)

        # 1. The resamplers were constructed (lazy init ran).
        assert ns._upsampler is not None
        assert ns._downsampler is not None

        # 2. _StreamingResampler.__init__ was called exactly TWICE (once
        # for up, once for down) — NOT four times (which would mean the
        # memoization guard failed and the resamplers were recreated on
        # the second process() call).
        assert len(init_calls) == 2, (
            f"_StreamingResampler.__init__ must be called exactly "
            f"twice (once for upsampler, once for downsampler) across two "
            f"process() calls at the same rate; got {len(init_calls)} calls "
            f"— memoization guard failed and the resamplers were recreated"
        )

        # 3. The resampler object identity is stable (same object, not a
        # fresh one).
        assert ns._upsampler is upsampler_after_first, (
            "upsampler object identity must be stable across process() calls (memoized, not recreated)"
        )
        assert ns._downsampler is downsampler_after_first, (
            "downsampler object identity must be stable across process() calls (memoized, not recreated)"
        )

        # Sanity: both process() calls produced output (not None).
        assert result1 is not None
        assert result2 is not None


# ── Sample-rate-driven resampler construction ─────────────────────────


class TestSampleRateResamplerConstruction:
    """when the ``process()`` sample rate is NOT the RNNoise
    native rate (48000), the up/down resampler pair IS constructed.
    At the native rate (48k) both resamplers stay ``None``.
    """

    def test_sample_rate_resampler_constructed_when_input_not_16k(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Construct a NoiseSuppressor with a stubbed RNNoise backend,
        call ``process(audio, 44100)`` (a common mic rate that's neither
        the default 16k nor the RNNoise native 48k), and assert:

          1. The upsampler was constructed (``_upsampler is not None``)
             — the source rate (44100) != RNNOISE_SAMPLE_RATE (48000),
             so resampling is needed.
          2. The downsampler was constructed (``_downsampler is not
             None``) — the round-trip back to 44100 needs a downsample
             from 48k.
          3. ``_resampler_rate`` is set to 44100 (the rate the
             resamplers are configured for).

        The test uses 44100 (not 48000 as the task description
        suggested) because 48000 IS the RNNoise native rate — at 48k
        no resampler is needed (``_ensure_resamplers`` returns early).
        44100 is a real-world non-native rate (CD-quality audio) that
        exercises the resampler-construction path.
        """
        _install_fake_pyrnnoise(monkeypatch)
        ns = NoiseSuppressor(method="rnnoise", sample_rate=44100)

        # Before process() — resamplers are NOT yet constructed (lazy).
        assert ns._upsampler is None, "upsampler must NOT be constructed before the first process() call (lazy init)"
        assert ns._downsampler is None, (
            "downsampler must NOT be constructed before the first process() call (lazy init)"
        )

        # 44100 is not the RNNoise native rate (48000) — resamplers
        # must be constructed. Use a 480-sample input so the RNNoise
        # frame loop produces at least one output frame (after the
        # 44100→48000 upsample produces >= 480 samples).
        audio = np.random.randn(480).astype(np.float32) * 0.1
        ns.process(audio, 44100)

        # 1. Upsampler constructed (44100 != 48000 → resampling needed).
        assert ns._upsampler is not None, (
            "upsampler must be constructed when process() sample rate (44100) != RNNOISE_SAMPLE_RATE (48000)"
        )
        # 2. Downsampler constructed (round-trip back to 44100).
        assert ns._downsampler is not None, (
            "downsampler must be constructed for the 48k→44100 round-trip back to the source rate"
        )
        # 3. _resampler_rate is set to the process sample rate.
        assert ns._resampler_rate == 44100, (
            f"_resampler_rate must be set to the process sample rate (44100); got {ns._resampler_rate}"
        )

    def test_no_resampler_at_native_rate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At the RNNoise native rate (48000), NO resampler is
        constructed — the source rate matches the model's required rate,
        so no up/downsampling is needed. ``_resampler_rate`` is set to
        48000 (so a subsequent rate change is detected), but both
        resamplers stay ``None``.
        """
        _install_fake_pyrnnoise(monkeypatch)
        ns = NoiseSuppressor(method="rnnoise", sample_rate=48000)

        audio = np.random.randn(480).astype(np.float32) * 0.1
        ns.process(audio, 48000)

        # Both resamplers stay None (native rate — no resampling needed).
        assert ns._upsampler is None, (
            "upsampler must stay None at the RNNoise native rate (48000) — no resampling needed"
        )
        assert ns._downsampler is None, (
            "downsampler must stay None at the RNNoise native rate (48000) — no resampling needed"
        )
        # _resampler_rate is set so a subsequent rate change is detected.
        assert ns._resampler_rate == 48000


# ── RNNoise init failure → clear degraded reason ──────────────────────


class TestInitFailureClearError:
    """when ``RNNoise(sample_rate=48000)`` raises (model file
    missing, native library load failure, etc.), ``_init_rnnoise``
    catches the exception and sets ``is_degraded=True`` with a
    ``degraded_reason`` that surfaces the original exception message.

    The helper NEVER raises — it always falls back to ``method="none"``
    (passthrough) so the audio pipeline doesn't crash. The user sees
    the degraded state in the UI and can act on the message (e.g.
    "model file not found" → reinstall the package).
    """

    def test_init_failure_raises_clear_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Inject a fake ``pyrnnoise`` module whose ``RNNoise``
        constructor raises ``RuntimeError("model file not found")``,
        construct a NoiseSuppressor, and assert:

          1. ``is_degraded`` is ``True`` (the init failure was caught
             and the filter fell back to degraded mode).
          2. ``degraded_reason`` contains "rnnoise" (so the user knows
             WHICH backend failed).
          3. ``degraded_reason`` contains the original exception
             message ("model file not found") so the user can act on
             the specific failure (e.g. reinstall, check disk space).
          4. ``_method`` is ``"none"`` (fell back to passthrough — the
             audio pipeline doesn't crash).
          5. ``_backend`` is ``None`` (no RNNoise instance was kept).

        The test name says "raises_clear_error" but ``_init_rnnoise``
        never raises — it surfaces the error via ``degraded_reason``.
        The "clear error" is the degraded_reason string, which must
        contain the original exception message so it's actionable.
        """
        # Inject a fake pyrnnoise whose RNNoise constructor raises.
        fake_module = MagicMock()

        class _FailingRNNoise:
            def __init__(self, sample_rate: int = RNNOISE_SAMPLE_RATE) -> None:
                raise RuntimeError("model file not found")

        fake_module.RNNoise = _FailingRNNoise
        monkeypatch.setitem(sys.modules, "pyrnnoise", fake_module)

        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)

        # 1. is_degraded is True (init failure caught).
        assert ns.is_degraded is True, (
            "NoiseSuppressor must be degraded when RNNoise init "
            "raises (the exception is caught and the filter falls back "
            "to passthrough)"
        )

        # 2. degraded_reason contains "rnnoise" (which backend failed).
        assert "rnnoise" in ns.degraded_reason.lower(), (
            f"degraded_reason must mention 'rnnoise' so the user knows WHICH backend failed; got {ns.degraded_reason!r}"
        )

        # 3. degraded_reason contains the original exception message.
        assert "model file not found" in ns.degraded_reason, (
            f"degraded_reason must surface the original exception "
            f"message ('model file not found') so the user can act on the "
            f"specific failure; got {ns.degraded_reason!r}"
        )

        # 4. _method fell back to "none" (passthrough).
        assert ns._method == "none", (
            f"_method must fall back to 'none' (passthrough) when RNNoise init fails; got {ns._method!r}"
        )

        # 5. _backend is None (no RNNoise instance kept).
        assert ns._backend is None, (
            "_backend must be None when RNNoise init fails (no instance to keep — the constructor raised)"
        )

    def test_import_error_degraded_reason_mentions_rnnoise(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``from pyrnnoise import RNNoise`` raises ImportError
        (package not installed), ``_init_rnnoise`` catches it and sets
        ``is_degraded=True`` with a ``degraded_reason`` mentioning
        "rnnoise" + "not installed".

        This is the most common failure mode (user didn't install the
        optional dependency). The test removes the fake pyrnnoise module
        so the real (broken-on-this-host) import path runs — OR, if the
        real pyrnnoise IS installed and working on a future host, the
        test forces the ImportError by replacing the module with one
        whose ``RNNoise`` attribute access raises ImportError.
        """

        # Force ImportError on ``from pyrnnoise import RNNoise`` by
        # injecting a module that raises on attribute access. Using a
        # MagicMock with a side_effect on __getattr__ won't work for
        # ``from X import Y`` (Python uses __getattr__ on the module).
        # Instead, inject a module that explicitly lacks RNNoise AND
        # raises ImportError on access. Simplest: a real module-like
        # object whose ``RNNoise`` attribute raises on access.
        class _ImportFailingModule:
            def __getattr__(self, name):
                raise ImportError(f"cannot import name {name!r} from 'pyrnnoise'")

        monkeypatch.setitem(sys.modules, "pyrnnoise", _ImportFailingModule())

        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)

        assert ns.is_degraded is True, (
            "NoiseSuppressor must be degraded when pyrnnoise import fails (ImportError caught)"
        )
        assert "rnnoise" in ns.degraded_reason.lower(), (
            f"degraded_reason must mention 'rnnoise' on import failure; got {ns.degraded_reason!r}"
        )
        assert ns._method == "none", "_method must fall back to 'none' on import failure"
