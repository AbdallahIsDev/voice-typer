"""``audio_stats`` plumbing across the ASR backends.

Split from the former catch-all module
``tests/test_dictation_pipeline_review_fixes.py``. Covers a-review
Finding 8:
``_transcribe`` had a broad ``try/except TypeError`` to handle
backends that lacked the ``audio_stats`` kwarg. The catch was too
broad: a TypeError inside the function body (``None.lower()``, bad
indexing) was also caught, masking real bugs. The fix adds
``audio_stats=None`` to ``CloudEngine.transcribe_with_fallback``
(the only backend that lacked it) and removes the broad catch.
These tests verify all four backends accept ``audio_stats`` and
that a real TypeError from the engine body propagates.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

# Shared non-magic app + per-cycle pipeline factories (single
# canonical definition in tests/fixtures/).
from tests.fixtures.dictation_pipeline_helpers import make_test_app as _make_app, new_pipeline as _new_pipeline


class TestAllBackendsAcceptAudioStatsKwarg:
    """a-review Finding 8: all four ASR backends must accept the
    ``audio_stats`` keyword argument on ``transcribe_with_fallback``.

    Pre-fix, only the three local engines (Whisper/Parakeet/Qwen)
    accepted it; ``CloudEngine.transcribe_with_fallback`` did not,
    which forced ``DictationPipeline._transcribe`` to wrap the call
    in a broad ``try/except TypeError`` fallback. The fix adds the
    parameter to CloudEngine (default None, ignored) so the broad
    catch can be removed.
    """

    def test_cloud_engine_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.cloud_engines import CloudEngine

        sig = inspect.signature(CloudEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters, (
            "CloudEngine.transcribe_with_fallback must accept audio_stats (a-review Finding 8)."
        )
        assert sig.parameters["audio_stats"].default is None, (
            "audio_stats on CloudEngine.transcribe_with_fallback must default to None for backwards compatibility."
        )

    def test_whisper_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        sig = inspect.signature(TranscriptionEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters

    def test_parakeet_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        sig = inspect.signature(ParakeetEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters

    def test_qwen_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.qwen_engine import QwenEngine

        sig = inspect.signature(QwenEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters


class TestTranscribeNoBroadTypeErrorCatch:
    """a-review Finding 8: ``DictationPipeline._transcribe`` must NOT
    wrap the ``transcribe_with_fallback`` call in a broad
    ``try/except TypeError``. A TypeError raised inside the engine
    body (e.g. ``None.lower()``, bad indexing) must propagate so the
    real bug surfaces in the log/traceback instead of being masked
    by a retry that fails the same way.

    The original source-text scan
    (``"except TypeError:" not in inspect.getsource(...)``) was
    brittle — a cosmetic refactor (e.g. catching ``TypeError`` as
    ``Exception`` subclass, or extracting the call into a helper)
    would break the test on false positives while functional
    regressions via different patterns (e.g. ``except Exception:``
    that still catches TypeError) would slip through. Removed in
    favor of the two behavioral tests below
    (``test_real_typeerror_propagates_from_engine`` and
    ``test_audio_stats_passed_through_to_engine``) which directly
    verify the runtime invariant: TypeError propagates and
    audio_stats is forwarded.
    """

    def test_real_typeerror_propagates_from_engine(self):
        """A TypeError raised inside the engine body must propagate
        out of ``_transcribe`` (not be swallowed by a broad catch).

        We mock the active transcriber so its
        ``transcribe_with_fallback`` raises TypeError — simulating
        a real bug like ``None.lower()`` inside the engine. The
        pre-fix broad catch would have retried and re-raised the
        same TypeError, producing a confusing trace. Post-fix, the
        original TypeError propagates directly.

        ``_transcribe`` now pops the streaming
        session via ``pop_streaming_session()`` (atomic) instead of
        the racy get+set pair, so we mock the pop (not the get)
        to force the batch path.
        """
        app = _make_app()
        # No streaming session — forces the ``else`` branch which
        # calls active.transcribe_with_fallback.
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        sentinel = TypeError("simulated None.lower() bug")
        active.transcribe_with_fallback.side_effect = sentinel
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        with pytest.raises(TypeError, match="simulated None.lower"):
            pipeline._transcribe()

        # The engine must have been called exactly once — no retry.
        assert active.transcribe_with_fallback.call_count == 1, (
            "DictationPipeline._transcribe must not retry on "
            "TypeError (a-review Finding 8). Got call_count="
            f"{active.transcribe_with_fallback.call_count}."
        )
        # And the retry must have passed audio_stats (the new code).
        _, kwargs = active.transcribe_with_fallback.call_args
        assert "audio_stats" in kwargs

    def test_audio_stats_passed_through_to_engine(self):
        """The audio_stats tuple captured from the recorder must be
        forwarded to the engine's transcribe_with_fallback.

        ``_transcribe`` now pops the streaming
        session via ``pop_streaming_session()`` (atomic) instead of
        the racy get+set pair, so we mock the pop (not the get)
        to force the batch path.
        """
        app = _make_app()
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._audio_stats = (0.123, 0.456, 25.0)
        result = pipeline._transcribe()

        assert result == "hello"
        _, kwargs = active.transcribe_with_fallback.call_args
        assert kwargs.get("audio_stats") == (0.123, 0.456, 25.0), (
            "_transcribe must forward the pre-computed audio_stats tuple to transcribe_with_fallback."
        )


class TestCloudEngineIgnoresAudioStats:
    """a-review Finding 8: when audio_stats is passed to
    ``CloudEngine.transcribe_with_fallback``, the value is ignored
    on the cloud path (cloud APIs don't use RMS/peak/silence) but
    forwarded to the local_engine fallback if one is provided.
    """

    def test_cloud_path_ignores_audio_stats(self):
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, "_send_request", return_value="cloud text"):
            result = engine.transcribe_with_fallback(audio, audio_stats=(0.1, 0.5, 50.0))
        assert result == "cloud text"

    def test_local_fallback_forwards_audio_stats(self):
        """When the cloud fails and a local_engine is provided,
        ``audio_stats`` must be forwarded to the local engine's
        ``transcribe`` call.
        """
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)

        # Force the cloud path to fail.
        with patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")):
            local_engine = MagicMock()
            local_engine.transcribe.return_value = "local text"
            result = engine.transcribe_with_fallback(
                audio,
                local_engine=local_engine,
                audio_stats=(0.7, 0.9, 10.0),
            )

        assert result == "local text"
        local_engine.transcribe.assert_called_once_with(audio, audio_stats=(0.7, 0.9, 10.0))

    def test_no_local_engine_still_works_without_audio_stats(self):
        """Backwards compat: calling without audio_stats must still
        work (existing callers like test_cloud_engines.py depend on it).
        """
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, "_send_request", return_value="text"):
            result = engine.transcribe_with_fallback(audio)
        assert result == "text"
