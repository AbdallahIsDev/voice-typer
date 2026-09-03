"""Tests for the duration-aware VAD policy (``vad_policy``) + wiring.

Policy under test:
- master switch OFF → raw audio, engine filter OFF (model-testing mode)
- SHORT audio (<2 s): never trim, engine filter ON (today's behavior —
  1-2 word dictations must never lose words to a trimmer)
- LONG audio (>30 s): never trim, engine filter ON (the engine needs
  its filter for segmentation/memory)
- MEDIUM clean audio: edge-trim (view, no copy) + engine filter OFF
  (skips the redundant full-audio Silero rescan)
- Uncertain input (high silence, near-silence level, no stats with
  junk): today's behavior (no trim, filter ON)
"""

import numpy as np
import pytest
from voice_typer.server import vad_policy
from voice_typer.server.vad_policy import decide_vad_filter, trim_edge_silence

SR = 16000


def _speech_like(
    seconds: float,
    *,
    lead_sil: float = 0.0,
    trail_sil: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Synthetic healthy-level speech with exact silence edges."""
    rng = np.random.default_rng(seed)
    speech = (0.1 * rng.standard_normal(int(seconds * SR))).astype(np.float32)
    lead = np.zeros(int(lead_sil * SR), dtype=np.float32)
    trail = np.zeros(int(trail_sil * SR), dtype=np.float32)
    return np.concatenate([lead, speech, trail])


def _stats(audio: np.ndarray) -> tuple[float, float, float]:
    abs_audio = np.abs(audio)
    return (
        float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))),
        float(np.max(abs_audio)),
        float(np.sum(abs_audio < 0.001) / audio.size * 100),
    )


class TestTrimEdgeSilence:
    def test_trims_edges_keeps_interior(self):
        audio = _speech_like(2.0, lead_sil=0.5, trail_sil=0.5)
        view, leading = trim_edge_silence(audio, SR)
        # Noise edges: allow a few sub-threshold samples of tolerance.
        assert abs(leading - int(0.5 * SR)) < 160
        assert abs(len(view) - int(2.0 * SR)) < 320
        # Zero-copy view into the original buffer.
        assert np.shares_memory(view, audio)
        # Interior untouched: identical samples.
        np.testing.assert_array_equal(view, audio[leading : leading + len(view)])

    def test_clean_audio_returns_unchanged(self):
        audio = _speech_like(2.0)
        view, leading = trim_edge_silence(audio, SR)
        assert leading == 0
        assert view.shape == audio.shape

    def test_all_silence_returns_unchanged(self):
        audio = np.zeros(SR, dtype=np.float32)
        view, leading = trim_edge_silence(audio, SR)
        assert leading == 0
        assert view.shape == audio.shape

    def test_empty_returns_unchanged(self):
        audio = np.zeros(0, dtype=np.float32)
        view, leading = trim_edge_silence(audio, SR)
        assert leading == 0
        assert view.shape == audio.shape

    def test_fraction_cap(self):
        # 40% leading silence: cap cuts the trim at 25% of duration.
        audio = _speech_like(2.0, lead_sil=2.0, trail_sil=0.0)
        assert len(audio) == int(4.0 * SR)
        view, leading = trim_edge_silence(audio, SR)
        assert leading == int(4.0 * SR * vad_policy.MAX_TRIM_FRACTION)

    def test_min_remaining_guard(self):
        # A 0.1 s blip that trimming would shrink below MIN_REMAINING_S:
        # refuse (the hallucination gate owns near-silence input).
        audio = _speech_like(0.1, lead_sil=0.4, trail_sil=0.45)
        view, leading = trim_edge_silence(audio, SR)
        assert leading == 0
        assert view.shape == audio.shape

    def test_nan_audio_returns_unchanged_without_crash(self):
        audio = _speech_like(1.0)
        audio[100:200] = np.nan
        view, leading = trim_edge_silence(audio, SR)
        assert view.shape == audio.shape


class TestDecideVadFilter:
    def test_switch_off_disables_everything(self):
        audio = _speech_like(5.0, lead_sil=0.5, trail_sil=0.5)
        out, use_filter, offset = decide_vad_filter(audio, SR, _stats(audio), False)
        assert use_filter is False
        assert offset == 0.0
        assert out.shape == audio.shape

    def test_short_audio_never_trimmed_filter_stays_on(self):
        # The 1-2 word case: today's behavior, byte-identical input.
        audio = _speech_like(1.0, lead_sil=0.2, trail_sil=0.2)
        out, use_filter, offset = decide_vad_filter(audio, SR, _stats(audio), True)
        assert use_filter is True
        assert offset == 0.0
        assert out.shape == audio.shape

    def test_long_audio_keeps_engine_filter(self):
        audio = _speech_like(35.0, lead_sil=1.0, trail_sil=1.0)
        out, use_filter, offset = decide_vad_filter(audio, SR, _stats(audio), True)
        assert use_filter is True
        assert offset == 0.0
        assert out.shape == audio.shape

    def test_medium_clean_audio_trims_and_skips_filter(self):
        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        out, use_filter, offset = decide_vad_filter(audio, SR, _stats(audio), True)
        assert use_filter is False
        assert offset == pytest.approx(0.3, abs=0.01)
        assert abs(len(out) - int(5.0 * SR)) < 320

    def test_medium_noisy_audio_keeps_filter(self):
        rng = np.random.default_rng(0)
        # 50% silence scattered through: uncertain input.
        speech = (0.1 * rng.standard_normal(8 * SR)).astype(np.float32)
        speech[::2] = 0.0
        out, use_filter, _ = decide_vad_filter(speech, SR, _stats(speech), True)
        assert use_filter is True
        assert out.shape == speech.shape

    def test_medium_near_silence_keeps_filter(self):
        audio = np.zeros(5 * SR, dtype=np.float32)
        out, use_filter, _ = decide_vad_filter(audio, SR, _stats(audio), True)
        assert use_filter is True
        assert out.shape == audio.shape

    def test_stats_none_computed_inline(self):
        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        out, use_filter, offset = decide_vad_filter(audio, SR, None, True)
        assert use_filter is False
        assert offset == pytest.approx(0.3, abs=0.01)


class TestCallSiteWiring:
    def _stub_engine(self, vad_enabled=True):
        import threading
        from types import SimpleNamespace

        model = type("M", (), {})()
        calls: dict = {}

        def fake_transcribe(audio, **kwargs):
            calls["audio"] = audio
            calls["kwargs"] = kwargs

            class _Seg:
                text = "hello"
                start = 0.0
                end = 5.0
                avg_logprob = -0.2
                no_speech_prob = 0.1

            from types import SimpleNamespace

            return ([_Seg()], SimpleNamespace(language="en", language_probability=0.99))

        model.transcribe = fake_transcribe
        engine = SimpleNamespace(
            _model=model,
            beam_size=1,
            best_of=1,
            language="en",
            condition_on_previous_text=False,
            config=SimpleNamespace(vad_filter_enabled=vad_enabled) if vad_enabled is not None else None,
            _abort_event=threading.Event(),
            last_quality_summary=None,
        )
        return engine, calls

    def test_batch_clean_medium_skips_engine_filter(self):
        from voice_typer.server.transcription_result import transcribe_unlocked

        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        engine, calls = self._stub_engine(vad_enabled=True)
        result = transcribe_unlocked(engine, audio, _stats(audio))
        assert result == "hello"
        assert calls["kwargs"]["vad_filter"] is False
        # Trimmed view: ~5 s of speech, not the 5.6 s input.
        assert abs(len(calls["audio"]) - int(5.0 * SR)) < 320

    def test_batch_short_keeps_engine_filter(self):
        from voice_typer.server.transcription_result import transcribe_unlocked

        audio = _speech_like(1.0, lead_sil=0.2, trail_sil=0.2)
        engine, calls = self._stub_engine(vad_enabled=True)
        transcribe_unlocked(engine, audio, _stats(audio))
        assert calls["kwargs"]["vad_filter"] is True
        assert len(calls["audio"]) == len(audio)

    def test_batch_switch_off_sends_raw_audio(self):
        from voice_typer.server.transcription_result import transcribe_unlocked

        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        engine, calls = self._stub_engine(vad_enabled=False)
        transcribe_unlocked(engine, audio, _stats(audio))
        assert calls["kwargs"]["vad_filter"] is False
        assert len(calls["audio"]) == len(audio)

    def test_batch_missing_config_behaves_as_enabled(self):
        from voice_typer.server.transcription_result import transcribe_unlocked

        audio = _speech_like(1.0)
        engine, calls = self._stub_engine(vad_enabled=None)
        transcribe_unlocked(engine, audio, _stats(audio))
        assert calls["kwargs"]["vad_filter"] is True

    def test_streaming_compensates_word_timestamps(self):
        from voice_typer.server.transcription_result import (
            transcribe_words_unlocked,
        )

        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        engine, calls = self._stub_engine(vad_enabled=True)

        from types import SimpleNamespace

        def fake_transcribe(audio, **kwargs):
            calls["audio"] = audio
            calls["kwargs"] = kwargs

            class _Seg:
                words = [SimpleNamespace(word="hello", start=1.0, end=1.5)]

            return ([_Seg()], None)

        engine._model.transcribe = fake_transcribe
        words = transcribe_words_unlocked(engine, audio, offset_seconds=10.0)
        assert calls["kwargs"]["vad_filter"] is False
        # 1.0 s into the TRIMMED audio + 10 s chunk offset + 0.3 s trim.
        assert words[0].start_seconds == pytest.approx(11.3, abs=0.01)
        assert words[0].end_seconds == pytest.approx(11.8, abs=0.01)

    def test_streaming_switch_off_keeps_timestamps_unshifted(self):
        from voice_typer.server.transcription_result import (
            transcribe_words_unlocked,
        )

        audio = _speech_like(5.0, lead_sil=0.3, trail_sil=0.3)
        engine, calls = self._stub_engine(vad_enabled=False)

        from types import SimpleNamespace

        def fake_transcribe(audio, **kwargs):
            calls["kwargs"] = kwargs

            class _Seg:
                words = [SimpleNamespace(word="hello", start=1.0, end=1.5)]

            return ([_Seg()], None)

        engine._model.transcribe = fake_transcribe
        words = transcribe_words_unlocked(engine, audio, offset_seconds=10.0)
        assert calls["kwargs"]["vad_filter"] is False
        assert words[0].start_seconds == pytest.approx(11.0, abs=0.01)


class TestAllowlist:
    def test_vad_filter_enabled_in_sec002_allowlist(self):
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "vad_filter_enabled" in IPC_CONFIG_ALLOWLIST
        typ, _validator = IPC_CONFIG_ALLOWLIST["vad_filter_enabled"]
        assert typ is bool

    def test_schema_default_is_true(self):
        from voice_typer.server.config import Config

        assert Config().vad_filter_enabled is True
