"""Tests for the Qwen3-ASR ONNX Runtime backend (``qwen_onnx_model.py``).

PLAN_ONNX_INTEGRATION.md §4.3 Option C-2: the pre-exported
``andrewleech/qwen3-asr-*-onnx`` models run via onnxruntime with no
torch/transformers. These tests mock the ORT sessions + tokenizer (no
weights — the model dirs are multi-GB and user-downloaded) and verify:

- the mel → encoder → prompt → greedy-decode pipeline logic,
- the ONNX auto-detect branch in ``QwenEngine.load()``,
- the torch-only touchpoints are correctly guarded for ONNX models
  (``_warm_up_model`` skip, ``unload`` close, CUDA-fallback skip,
  device pinned to ``cpu``).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
from voice_typer.server import qwen_onnx_model as qom
from voice_typer.server.qwen_onnx_model import (
    QwenOnnxModel,
    _audio_pad_range,
    _build_prompt_ids,
    _get_feat_extract_output_lengths,
    _load_embed_tokens,
    _resolve_onnx_paths,
    is_onnx_model_dir,
)

# The Qwen3 special-token ids are pinned by the export tool
# (andrewleech/qwen3-asr-onnx src/prompt.py). Keep them as an explicit
# pin so an upstream contract drift is caught here, not on a user host.
_MODULE_EOS_IDS = {151643, 151645}  # <|endoftext|>, <|im_end|>
_MODULE_IM_START = 151644
_MODULE_AUDIO_START = 151669
_MODULE_AUDIO_END = 151670
_MODULE_AUDIO_PAD = 151676


# ─── helpers ─────────────────────────────────────────────────────────

def make_onnx_dir(
    tmp_path: Path,
    *,
    hidden: int = 4,
    vocab: int = 64,
    use_int4: bool = False,
    with_config: bool = True,
) -> Path:
    """Create the pre-exported ONNX model layout (files may be empty)."""
    d = tmp_path / "qwen3-asr-onnx"
    d.mkdir(exist_ok=True)
    suffix = ".int4" if use_int4 else ""
    for stem in ("encoder", "decoder_init", "decoder_step"):
        (d / f"{stem}{suffix}.onnx").write_bytes(b"\x00")
    (d / "decoder_weights.data").write_bytes(b"\x00")
    embed = np.zeros((vocab, hidden), dtype=np.float16)
    embed.tofile(d / "embed_tokens.bin")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    if with_config:
        (d / "config.json").write_text(
            f'{{"hidden_size": {hidden}, "text_config": {{"hidden_size": {hidden}}}}}',
            encoding="utf-8",
        )
    return d


@pytest.fixture(autouse=True)
def real_faster_whisper(monkeypatch):
    """Restore the REAL ``faster_whisper`` package for the mel path.

    The session-scoped ``mock_heavy_imports`` fixture stubs
    ``faster_whisper`` as a non-package MagicMock, which breaks the lazy
    ``from faster_whisper.feature_extractor import FeatureExtractor`` in
    ``qwen_onnx_model._log_mel_spectrogram``. Loads the real package
    fresh and swaps it in for the test's duration (monkeypatch restores
    the mock afterwards).
    """
    import importlib
    import sys

    mocked = sys.modules.get("faster_whisper")
    if mocked is not None and not hasattr(mocked, "__path__"):
        for name in [n for n in list(sys.modules) if n == "faster_whisper" or n.startswith("faster_whisper.")]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        real = importlib.import_module("faster_whisper")
        monkeypatch.setitem(sys.modules, "faster_whisper", real)


class FakeTokenizer:
    """Stub HF tokenizer: ``from_file`` + ``decode`` only."""

    @classmethod
    def from_file(cls, path: str) -> FakeTokenizer:
        return cls()

    def decode(self, ids, skip_special_tokens: bool = True) -> str:
        return " ".join(str(int(i)) for i in ids)


class FakeSession:
    """Scripted ORT session: ``run`` returns canned outputs per call."""

    def __init__(self, *, run_fn, input_names: tuple[str, ...] = ()):
        self._run_fn = run_fn
        self._input_names = input_names
        self.run_calls = 0

    def run(self, output_names, feed):
        self.run_calls += 1
        return self._run_fn(feed, self.run_calls)

    def get_inputs(self):
        return [SimpleNamespace(name=n) for n in self._input_names]


def scripted_sessions(
    *,
    hidden: int = 4,
    vocab: int = 64,
    first_token: int = 7,
    eos_ids=frozenset({2, 3}),
    decoder_format: str = "v3",
) -> dict[str, FakeSession]:
    """Return encoder/decoder fake sessions that produce a scripted decode.

    ``first_token`` is argmax'd from the prefill logits; the first
    ``decoder_step`` call then argmax's an EOS id so the loop stops after
    one autoregressive step.
    """

    def encoder_run(feed, call):
        # [1, 128, T] -> [1, 4, hidden]
        out = np.zeros((1, 4, hidden), dtype=np.float32)
        return (out,)

    def init_run(feed, call):
        length = (
            feed["input_ids"].shape[1]
            if decoder_format == "v3"
            else feed["input_embeds"].shape[1]
        )
        logits = np.full((1, length, vocab), -100.0, dtype=np.float32)
        logits[0, -1, first_token] = 0.0
        kv = np.zeros((1, 1, length, 8), dtype=np.float32)
        return (logits, kv, kv)

    def step_run(feed, call):
        logits = np.full((1, 1, vocab), -100.0, dtype=np.float32)
        eos_id = next(iter(eos_ids))
        logits[0, 0, eos_id] = 0.0
        kv = np.zeros((1, 1, 1, 8), dtype=np.float32)
        return (logits, kv, kv)

    inputs = ("input_ids", "position_ids", "audio_features", "audio_offset") if decoder_format == "v3" else (
        "input_embeds",
        "position_ids",
    )
    return {
        "encoder": FakeSession(run_fn=encoder_run, input_names=("mel",)),
        "decoder_init": FakeSession(
            run_fn=init_run, input_names=inputs
        ),
        "decoder_step": FakeSession(
            run_fn=step_run, input_names=("input_embeds", "position_ids", "past_keys", "past_values")
        ),
    }


@contextmanager
def patch_ort(sessions: dict[str, FakeSession]):
    """Patch onnxruntime so ``QwenOnnxModel.from_pretrained`` uses fakes."""

    def session_factory(path, providers=None):
        p = Path(path)
        if "decoder_init" in p.name:
            return sessions["decoder_init"]
        if "decoder_step" in p.name:
            return sessions["decoder_step"]
        return sessions["encoder"]

    with (
        mock.patch("onnxruntime.InferenceSession", side_effect=session_factory),
        mock.patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
    ):
        yield


def patch_tokenizer():
    return mock.patch("tokenizers.Tokenizer", FakeTokenizer)


def loaded_model(tmp_path, *, hidden: int = 4, vocab: int = 64, **kw) -> QwenOnnxModel:
    """Build a model with real files + fake ORT sessions."""
    d = make_onnx_dir(tmp_path, hidden=hidden, vocab=vocab, **kw)
    sessions = scripted_sessions(hidden=hidden, vocab=vocab)
    model = QwenOnnxModel(str(d))
    with patch_ort(sessions), patch_tokenizer():
        model.from_pretrained()
    model._sessions = sessions  # make the fakes directly reachable
    return model


# ─── pure helpers ────────────────────────────────────────────────────


class TestFeatExtractOutputLengths:
    def test_reference_values(self):
        # 30 s of 16 kHz audio -> 3000 mel frames -> 390 audio tokens
        # (13 tokens per 100-frame conv window; the value printed in the
        # model card / export-tool README for the full 30 s context).
        assert _get_feat_extract_output_lengths(3000) == 390
        assert _get_feat_extract_output_lengths(0) == 0
        assert _get_feat_extract_output_lengths(100) == 13
        assert _get_feat_extract_output_lengths(200) == 26

    def test_monotonic(self):
        lengths = [_get_feat_extract_output_lengths(f) for f in range(0, 321, 32)]
        assert lengths == sorted(lengths)


class TestBuildPromptIds:
    def test_structure(self):
        ids = _build_prompt_ids(4)
        assert ids[0] == _MODULE_IM_START
        assert _MODULE_AUDIO_START in ids
        assert _MODULE_AUDIO_END in ids
        # exactly 4 contiguous <|audio_pad|> tokens
        start, end = _audio_pad_range(ids)
        assert end - start == 4
        assert ids[start:end] == [_MODULE_AUDIO_PAD] * 4
        # ends with the assistant turn opening
        assert ids[-1] == _newline_id()
        assert _MODULE_IM_START in ids[-6:]

    def test_audio_pad_range_raises_without_pads(self):
        with pytest.raises(ValueError):
            _audio_pad_range([1, 2, 3])

    def test_audio_pad_count_matches_feature_length(self):
        # The prompt's pad count must equal the encoder output length
        # formula for the same audio (transcribe() clamps as a fallback).
        frames = 3000
        ids = _build_prompt_ids(_get_feat_extract_output_lengths(frames))
        start, end = _audio_pad_range(ids)
        assert end - start == 390


def _newline_id():
    return qom._NEWLINE_TOKEN_ID


class TestResolveOnnxPaths:
    def test_prefers_int4_when_requested(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        for stem in ("encoder", "decoder_init", "decoder_step"):
            (d / f"{stem}.int4.onnx").write_bytes(b"\x00")
            (d / f"{stem}.onnx").write_bytes(b"\x00")
        paths = _resolve_onnx_paths(d, prefer_quantized=True)
        assert all(p.name.endswith(".int4.onnx") for p in paths.values())

    def test_plain_fallback_when_no_int4(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        for stem in ("encoder", "decoder_init", "decoder_step"):
            (d / f"{stem}.onnx").write_bytes(b"\x00")
        paths = _resolve_onnx_paths(d, prefer_quantized=True)
        assert all(p.name.endswith(".onnx") for p in paths.values())

    def test_missing_session_raises(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        (d / "encoder.onnx").write_bytes(b"\x00")
        with pytest.raises(FileNotFoundError):
            _resolve_onnx_paths(d, prefer_quantized=True)


class TestLoadEmbedTokens:
    def test_reshapes_by_hidden(self, tmp_path):
        path = tmp_path / "embed_tokens.bin"
        np.zeros((8, 4), dtype=np.float16).tofile(path)
        assert _load_embed_tokens(path, 4).shape == (8, 4)

    def test_mismatched_hidden_raises(self, tmp_path):
        path = tmp_path / "embed_tokens.bin"
        np.zeros((8, 4), dtype=np.float16).tofile(path)
        with pytest.raises(ValueError):
            _load_embed_tokens(path, 5)


class TestIsOnnxModelDir:
    def test_true_plain(self, tmp_path):
        assert is_onnx_model_dir(make_onnx_dir(tmp_path))

    def test_true_int4(self, tmp_path):
        assert is_onnx_model_dir(make_onnx_dir(tmp_path, use_int4=True))

    def test_false_missing_tokenizer(self, tmp_path):
        d = make_onnx_dir(tmp_path)
        (d / "tokenizer.json").unlink()
        assert not is_onnx_model_dir(d)

    def test_false_not_a_dir(self, tmp_path):
        f = tmp_path / "file"
        f.write_text("x")
        assert not is_onnx_model_dir(f)

    def test_false_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert not is_onnx_model_dir(d)


# ─── QwenOnnxModel loading ───────────────────────────────────────────


class TestFromPretrained:
    def test_loads_sessions_and_metadata(self, tmp_path):
        d = make_onnx_dir(tmp_path, hidden=8, vocab=16)
        model = QwenOnnxModel(str(d))
        sessions = scripted_sessions(hidden=8, vocab=16)
        with patch_ort(sessions), patch_tokenizer():
            model.from_pretrained()
        assert set(model._sessions) == {"encoder", "decoder_init", "decoder_step"}
        assert model._hidden_size == 8
        assert model._embed_tokens is not None and model._embed_tokens.shape == (16, 8)
        assert model._tokenizer is not None
        assert model.model_path == Path(str(d))

    def test_missing_session_raises(self, tmp_path):
        d = make_onnx_dir(tmp_path)
        (d / "decoder_step.onnx").unlink()
        model = QwenOnnxModel(str(d))
        with patch_ort(scripted_sessions()), patch_tokenizer(), pytest.raises(FileNotFoundError):
            model.from_pretrained()

    def test_missing_embed_tokens_raises(self, tmp_path):
        d = make_onnx_dir(tmp_path)
        (d / "embed_tokens.bin").unlink()
        model = QwenOnnxModel(str(d))
        with patch_ort(scripted_sessions()), patch_tokenizer(), pytest.raises(FileNotFoundError):
            model.from_pretrained()

    def test_unreadable_config_defaults_hidden(self, tmp_path):
        # No config.json -> hidden defaults to the 1.7B arch value (2048);
        # the embed file must then be a multiple of 2048 to load.
        d = make_onnx_dir(tmp_path, with_config=False)
        (d / "embed_tokens.bin").unlink()
        np.zeros((1, 2048), dtype=np.float16).tofile(d / "embed_tokens.bin")
        model = QwenOnnxModel(str(d))
        with patch_ort(scripted_sessions()), patch_tokenizer():
            model.from_pretrained()
        assert model._hidden_size == 2048
        assert model._embed_tokens.shape == (1, 2048)


# ─── QwenOnnxModel inference ─────────────────────────────────────────


class TestTranscribe:
    def test_empty_audio_returns_empty(self, tmp_path):
        model = loaded_model(tmp_path)
        with patch_tokenizer():
            (result,) = model.transcribe((np.zeros(0, dtype=np.float32), 16000))
        assert result.text == ""
        assert model._sessions["encoder"].run_calls == 0

    def test_greedy_decode_pipeline(self, tmp_path):
        model = loaded_model(tmp_path, vocab=64)
        # shrink the EOS ids so the small vocab can hold them
        with mock.patch.object(qom, "_EOS_TOKEN_IDS", frozenset({2, 3})), patch_tokenizer():
            (result,) = model.transcribe((np.zeros(16000, dtype=np.float32), 16000))
        assert model._sessions["encoder"].run_calls == 1
        assert model._sessions["decoder_init"].run_calls == 1
        # prefill produced token 7; one step produced EOS -> loop stopped
        assert model._sessions["decoder_step"].run_calls == 1
        assert result.text  # tokenizer decoded a non-empty string

    def test_decoder_init_v1_input_embeds_format(self, tmp_path):
        """The v1 export format takes input_embeds (prompt embedded)."""
        d = make_onnx_dir(tmp_path, vocab=64)
        # v1 embeds the WHOLE prompt server-side, so the embed matrix
        # must cover the special-token ids (up to <|audio_pad|> = 151676).
        (d / "embed_tokens.bin").unlink()
        np.zeros((qom._AUDIO_PAD_TOKEN_ID + 2, 4), dtype=np.float16).tofile(
            d / "embed_tokens.bin"
        )
        sessions = scripted_sessions(vocab=64, decoder_format="v1")
        model = QwenOnnxModel(str(d))
        with patch_ort(sessions), patch_tokenizer():
            model.from_pretrained()
        model._sessions = sessions
        with mock.patch.object(qom, "_EOS_TOKEN_IDS", frozenset({2, 3})), patch_tokenizer():
            (result,) = model.transcribe((np.zeros(16000, dtype=np.float32), 16000))
        assert result.text
        # the v1 init path embedded the prompt + scattered the encoder
        # output; the run succeeded, which is the observable contract.
        assert sessions["decoder_init"].run_calls == 1

    def test_first_token_eos_stops_immediately(self, tmp_path):
        model = loaded_model(tmp_path, vocab=64)
        # make the prefill argmax an EOS id directly
        sessions = model._sessions

        def init_run(feed, call):
            length = feed["input_ids"].shape[1]
            logits = np.full((1, length, 64), -100.0, dtype=np.float32)
            logits[0, -1, 2] = 0.0  # EOS
            kv = np.zeros((1, 1, length, 8), dtype=np.float32)
            return (logits, kv, kv)

        sessions["decoder_init"] = FakeSession(run_fn=init_run, input_names=("input_ids",))
        with mock.patch.object(qom, "_EOS_TOKEN_IDS", frozenset({2, 3})), patch_tokenizer():
            (result,) = model.transcribe((np.zeros(16000, dtype=np.float32), 16000))
        assert sessions["decoder_step"].run_calls == 0  # EOS before any step
        assert result.text == "2"

    def test_non_16k_audio_is_resampled(self, tmp_path):
        model = loaded_model(tmp_path, vocab=64)
        with mock.patch.object(qom, "_EOS_TOKEN_IDS", frozenset({2, 3})), patch_tokenizer():
            (result,) = model.transcribe((np.zeros(8000, dtype=np.float32), 8000))
        assert result.text
        assert model._sessions["encoder"].run_calls == 1

    def test_close_releases_sessions(self, tmp_path):
        model = loaded_model(tmp_path)
        model.close()
        assert model._sessions == {}
        assert model._embed_tokens is None
        assert model._tokenizer is None

    def test_module_pins_special_token_ids(self):
        # Contract pin: the ids the export tool uses for Qwen3-ASR.
        assert set(qom._EOS_TOKEN_IDS) == _MODULE_EOS_IDS
        assert _MODULE_IM_START == qom._IM_START_TOKEN_ID
        assert _MODULE_AUDIO_START == qom._AUDIO_START_TOKEN_ID
        assert _MODULE_AUDIO_END == qom._AUDIO_END_TOKEN_ID
        assert _MODULE_AUDIO_PAD == qom._AUDIO_PAD_TOKEN_ID

    def test_module_pins_prompt_word_token_ids(self):
        """The prompt scaffolding word ids were VERIFIED 2026-08-15
        against the REAL tokenizer.json shipped in both
        andrewleech/qwen3-asr-1.7b-onnx and qwen3-asr-0.6b-onnx:
        "system" -> [8948], "user" -> [872], "assistant" -> [77091],
        "\n" -> [198]. The export tool's src/prompt.py hardcodes
        system=[9125]/user=[882], which decode to " Current"/" time" in
        the real vocab and are WRONG — these pins protect against a
        regression to the tool's values."""
        assert qom._SYSTEM_TOKEN_IDS == (8948,)
        assert qom._USER_TOKEN_IDS == (872,)
        assert qom._ASSISTANT_TOKEN_IDS == (77091,)
        assert qom._NEWLINE_TOKEN_ID == 198


# ─── QwenEngine integration (ONNX auto-detect + torch guards) ────────


class TestQwenEngineOnnxIntegration:
    def _make_engine(self, tmp_path, **kw):
        from voice_typer.server.qwen_engine import QwenEngine

        d = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        sessions = scripted_sessions(hidden=4, vocab=64)
        engine = QwenEngine(str(d), device=kw.pop("device", "cuda"))
        return engine, d, sessions

    def test_load_selects_onnx_backend_and_pins_cpu(self, tmp_path):
        engine, d, sessions = self._make_engine(tmp_path, device="cuda")
        with patch_ort(sessions), patch_tokenizer():
            assert engine.load() is True
        assert engine._onnx_model is not None
        assert engine.is_loaded
        # device pinned to cpu — the CUDA branch / warm-up must not fire
        assert engine.device == "cpu"
        assert engine.device_info == "qwen/cpu"

    def test_load_incomplete_onnx_dir_fails_closed(self, tmp_path):
        # ``is_onnx_model_dir`` only requires encoder + embed + tokenizer,
        # so a missing decoder session still routes to the ONNX branch and
        # fails closed there (fail-closed, no silent torch fallback).
        engine, d, sessions = self._make_engine(tmp_path)
        (d / "decoder_step.onnx").unlink()
        with patch_ort(sessions), patch_tokenizer(), pytest.raises(RuntimeError):
            engine.load()
        assert engine._onnx_model is None
        assert not engine.is_loaded

    def test_no_warm_up_model_attribute(self, tmp_path):
        """The torch warm-up pass was removed with the torch engine
        (2026-08-15) — the ONNX backend has nothing to prime (no CUDA
        kernels; the ORT sessions are already loaded)."""
        from voice_typer.server.qwen_engine import QwenEngine

        assert not hasattr(QwenEngine, "_warm_up_model"), (
            "QwenEngine must NOT have a _warm_up_model method — the torch "
            "engine was removed (2026-08-15); the ONNX backend has no "
            "warmup pass. Re-introducing it would be dead code."
        )

    def test_unload_closes_onnx_model(self, tmp_path):
        engine, d, sessions = self._make_engine(tmp_path)
        with patch_ort(sessions), patch_tokenizer():
            engine.load()
        with mock.patch.object(engine._onnx_model, "close") as close:
            engine.unload()
        close.assert_called_once()
        assert engine._onnx_model is None
        assert not engine.is_loaded

    def test_transcribe_with_fallback_reraises_for_onnx(self, tmp_path):
        engine, d, sessions = self._make_engine(tmp_path)
        with patch_ort(sessions), patch_tokenizer():
            engine.load()
        # ONNX model is pinned to cpu — even a "cuda"-looking error must
        # be re-raised, never routed into the torch .to() fallback path.
        with (
            mock.patch.object(engine, "transcribe", side_effect=RuntimeError("cuda error: launch failed")),
            pytest.raises(RuntimeError),
        ):
            engine.transcribe_with_fallback(np.zeros(16000, dtype=np.float32))

    def test_transcribe_routes_through_onnx_model(self, tmp_path):
        engine, d, sessions = self._make_engine(tmp_path)
        with patch_ort(sessions), patch_tokenizer():
            engine.load()
        assert engine._onnx_model is not None
        assert engine._lock is not None
        text = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert isinstance(text, str)
