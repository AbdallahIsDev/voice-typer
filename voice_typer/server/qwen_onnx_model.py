"""Qwen3-ASR ONNX Runtime model wrapper.

PLAN_ONNX_INTEGRATION.md §4.3 Option C-2 — implemented 2026-08-14 using the
pre-exported ONNX model files (``andrewleech/qwen3-asr-1.7b-onnx`` /
``qwen3-asr-0.6b-onnx`` on HuggingFace; the export tool is
``andrewleech/qwen3-asr-onnx``). The export already exists upstream, so
no ``torch.onnx.export`` is needed in this project: this module loads the
ONNX sessions via ``onnxruntime`` and runs the Whisper-compatible
mel → encoder → prompt → greedy-decode pipeline described by the model
card. No torch, no transformers, no ``qwen_asr`` package.

Model dir layout (auto-detects the ``.int4.`` quantized variants):

    encoder.onnx            (or encoder.int4.onnx — encoder weights are
                             always FP32; the .int4 file is a copy)
    decoder_init.onnx       (or decoder_init.int4.onnx) — prefill
    decoder_step.onnx       (or decoder_step.int4.onnx) — autoregressive
    decoder_weights.data    (or decoder_weights.int4.data) — shared ext
    embed_tokens.bin        [vocab, hidden] float16 embedding matrix
    tokenizer.json          HF tokenizer (BPE, Qwen chat template)
    config.json             architecture config (hidden_size etc.)

Inference pipeline (verbatim from the model card + the export tool's
``src/inference.py`` / ``src/mel.py`` / ``src/prompt.py``):

1. Compute the Whisper-compatible log-mel (16 kHz, 128 bins, n_fft=400,
   hop=160, periodic Hann, Slaney mel, 0-8 kHz, drop last frame).
2. ``encoder.onnx``: mel → ``audio_features`` ``[1, enc_len, hidden]``.
3. Build the ASR prompt: ``<|im_start|>system\\n<|im_end|>\\n
   <|im_start|>user\\n<|audio_start|><|audio_pad|>…<|audio_end|>
   <|im_end|>\\n<|im_start|>assistant\\n`` — the ``<|audio_pad|>`` count
   equals ``encoder output length`` (via the same
   ``_get_feat_extract_output_lengths`` formula the export tool uses).
4. ``decoder_init.onnx`` (prefill): ``input_ids`` + ``position_ids`` +
   ``audio_features`` + ``audio_offset`` → logits + KV cache.
5. Greedy decode with ``decoder_step.onnx``: per-token embedding lookup
   from ``embed_tokens.bin`` → ``input_embeds`` + KV cache → logits,
   argmax, until EOS (``<|endoftext|>`` / ``<|im_end|>``).

The class exposes the same ``from_pretrained(path)`` +
``transcribe((audio, sample_rate), language=...) -> list[Transcription]``
surface that ``qwen_asr.Qwen3ASRModel`` exposes, so
``QwenEngine.load()`` can swap between the torch backend and this ONNX
backend without touching any caller (see the auto-detect logic in
``qwen_engine.py``).

Known validation scope (honest): the ONNX I/O names / special-token IDs /
mel parameters were verified 2026-08-15 against the REAL export —
decoder_init/decoder_step I/O names from the actual .onnx protobufs,
mel params + special-token ids from config.json, prompt word ids from the
real tokenizer.json (and the export tool's hardcoded system/user ids were
found to be WRONG and corrected here). The encoder I/O names (mel →
audio_features) were confirmed from the model file header. The actual
model weights are NOT bundled (multi-GB, user-downloaded per the existing
Qwen local-path workflow) — a real-inference smoke test must still run on
a host with the downloaded model (see PLAN_ONNX_INTEGRATION §4.3 C-2).
The unit tests in tests/test_qwen_onnx_model.py mock the ORT sessions and
verify the pipeline logic (prompt construction, mel shapes, decode loop,
EOS handling) without weights.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("voice_typer.server.qwen_onnx")

# ─── Whisper-compatible mel parameters (model card: "identical to Whisper") ──
_MEL_SAMPLE_RATE = 16000
_MEL_N_FFT = 400
_MEL_HOP = 160
_MEL_N_BINS = 128
_MEL_FMIN = 0.0
_MEL_FMAX = 8000.0

# ─── Special token IDs (shared across all Qwen3-ASR sizes; validated by
#     the export tool at export time against the real tokenizer) ──────
_ENDOFTEXT_TOKEN_ID = 151643  # <|endoftext|> — pad, also EOS
_IM_START_TOKEN_ID = 151644  # <|im_start|>
_IM_END_TOKEN_ID = 151645  # <|im_end|> — also EOS
_AUDIO_START_TOKEN_ID = 151669  # <|audio_start|>
_AUDIO_END_TOKEN_ID = 151670  # <|audio_end|>
_AUDIO_PAD_TOKEN_ID = 151676  # <|audio_pad|> — replaced by encoder output
_EOS_TOKEN_IDS = frozenset({_ENDOFTEXT_TOKEN_ID, _IM_END_TOKEN_ID})

# Hardcoded subword encodings of the prompt scaffolding ("system\\n",
# "user\\n", "assistant\\n"). VERIFIED 2026-08-15 against the REAL
# tokenizer.json shipped in both andrewleech/qwen3-asr-1.7b-onnx and
# qwen3-asr-0.6b-onnx ("system" -> [8948], "user" -> [872],
# "assistant" -> [77091], "\\n" -> [198]). NOTE: the export tool's
# ``src/prompt.py`` hardcodes system=[9125] / user=[882] — those ids
# decode to " Current" / " time" in the real vocab and are WRONG; the
# values below come from the actual tokenizer, not the tool.
_NEWLINE_TOKEN_ID = 198
_SYSTEM_TOKEN_IDS = (8948,)  # "system"
_USER_TOKEN_IDS = (872,)  # "user"
_ASSISTANT_TOKEN_IDS = (77091,)  # "assistant"

# Encoder windowing (from the export tool's encoder_wrapper.py):
# a stride-2 conv reduces length t to (t+1)//2, applied 3 times; a full
# 100-frame conv window yields 13 tokens.
_CONV_WINDOW = 100
_TOKENS_PER_WINDOW = 13

# Decoder greedily generates at most this many tokens.
_MAX_DECODE_TOKENS = 256


@dataclass
class Transcription:
    """Minimal ``ASRTranscription``-shaped result (``.text`` suffices)."""

    text: str


def _get_feat_extract_output_lengths(mel_frames: int) -> int:
    """Number of audio tokens the encoder produces for ``mel_frames``.

    Matches the export tool's ``_get_feat_extract_output_lengths``
    exactly (three stride-2 conv halvings on the window remainder, plus
    ``TOKENS_PER_WINDOW`` per full 100-frame window).
    """
    leave = mel_frames % _CONV_WINDOW
    t = (leave + 1) // 2
    t = (t + 1) // 2
    t = (t + 1) // 2
    return t + (mel_frames // _CONV_WINDOW) * _TOKENS_PER_WINDOW


def _build_prompt_ids(audio_token_count: int) -> list[int]:
    """Build the ASR prompt token-ID sequence (see module docstring)."""
    ids: list[int] = [
        _IM_START_TOKEN_ID,
        *_SYSTEM_TOKEN_IDS,
        _NEWLINE_TOKEN_ID,
        _IM_END_TOKEN_ID,
        _NEWLINE_TOKEN_ID,
        _IM_START_TOKEN_ID,
        *_USER_TOKEN_IDS,
        _NEWLINE_TOKEN_ID,
        _AUDIO_START_TOKEN_ID,
    ]
    ids.extend([_AUDIO_PAD_TOKEN_ID] * audio_token_count)
    ids.extend(
        [
            _AUDIO_END_TOKEN_ID,
            _IM_END_TOKEN_ID,
            _NEWLINE_TOKEN_ID,
            _IM_START_TOKEN_ID,
            *_ASSISTANT_TOKEN_IDS,
            _NEWLINE_TOKEN_ID,
        ]
    )
    return ids


def _audio_pad_range(prompt_ids: list[int]) -> tuple[int, int]:
    """Return the ``[start, end)`` index range of the audio_pad tokens."""
    start: int | None = None
    end: int | None = None
    for i, tid in enumerate(prompt_ids):
        if tid == _AUDIO_PAD_TOKEN_ID:
            if start is None:
                start = i
            end = i + 1
    if start is None or end is None:
        raise ValueError("No <|audio_pad|> tokens found in prompt")
    return start, end


def _log_mel_spectrogram(audio: np.ndarray) -> np.ndarray:
    """Whisper-compatible log-mel ``[1, 128, T]`` (numpy/scipy only).

    Mirrors the export tool's ``mel.py`` exactly: periodic Hann STFT
    (center=True, reflect), power magnitudes, Slaney mel filterbank
    (0-8 kHz, 128 bins), ``log10(clamp 1e-10)`` → ``max(x, max-8)`` →
    ``(x+4)/4``, drop the last frame. Uses ``faster_whisper``'s
    FeatureExtractor (already a project dependency via the Whisper
    backend) for the filterbank + torch-mirroring STFT — DRY, E7.
    """
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    if audio.ndim != 1:
        audio = audio.reshape(-1)

    from faster_whisper.feature_extractor import FeatureExtractor

    # feature_size=128 + Whisper defaults gives exactly the Qwen3-ASR
    # mel params (16 kHz, 128 bins, n_fft=400, hop=160, 0-8 kHz).
    extractor = FeatureExtractor(
        feature_size=_MEL_N_BINS,
        sampling_rate=_MEL_SAMPLE_RATE,
        hop_length=_MEL_HOP,
        n_fft=_MEL_N_FFT,
    )
    # Reuse the static helpers so we control the frame drop ourselves
    # (the reference drops the LAST log-mel frame; FeatureExtractor.__call__
    # drops it inside the stft — equivalent, but we match the reference
    # formula exactly here).
    window = np.hanning(_MEL_N_FFT + 1)[:-1].astype("float32")  # periodic Hann
    stft = FeatureExtractor.stft(
        audio,
        _MEL_N_FFT,
        hop_length=_MEL_HOP,
        window=window,
        return_complex=True,
    )
    magnitudes = (np.abs(stft) ** 2).astype(np.float32)  # match reference float32

    mel_spec = extractor.mel_filters @ magnitudes
    log_spec = np.log10(np.clip(mel_spec, a_min=1e-10, a_max=None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    log_spec = log_spec[:, :-1]  # drop last frame (WhisperFeatureExtractor)
    return log_spec[np.newaxis, :, :]  # [1, 128, T]


def _load_embed_tokens(path: Path, hidden_size: int) -> np.ndarray:
    """Load the float16 ``embed_tokens.bin`` matrix ``[vocab, hidden]``."""
    raw = np.fromfile(path, dtype=np.float16)
    if raw.size % hidden_size != 0:
        raise ValueError(
            f"embed_tokens.bin size {raw.size} is not a multiple of hidden_size {hidden_size}"
        )
    return raw.reshape(-1, hidden_size)


def _resolve_onnx_paths(model_dir: Path, prefer_quantized: bool) -> dict[str, Path]:
    """Resolve the encoder/decoder session paths, preferring int4 variants.

    ``prefer_quantized`` selects ``*.int4.onnx`` when present (RTN int4
    MatMulNBits, ~3x smaller, near-FP32 accuracy on CPU). The encoder
    int4 file carries FP32 weights (per the model card), so either file
    works — we still prefer the int4-named file for consistency.
    """
    suffixes = (".int4.onnx", ".onnx") if prefer_quantized else (".onnx", ".int4.onnx")
    out: dict[str, Path] = {}
    for key, stem in (
        ("encoder", "encoder"),
        ("decoder_init", "decoder_init"),
        ("decoder_step", "decoder_step"),
    ):
        found: Path | None = None
        for suffix in suffixes:
            candidate = model_dir / f"{stem}{suffix}"
            if candidate.is_file():
                found = candidate
                break
        if found is None:
            raise FileNotFoundError(
                f"Qwen3-ASR ONNX model missing {stem} session in {model_dir} "
                f"(looked for {stem}.onnx / {stem}.int4.onnx)"
            )
        out[key] = found
    return out


class QwenOnnxModel:
    """ONNX Runtime Qwen3-ASR model (pre-exported files, no torch).

    Drop-in replacement for ``qwen_asr.Qwen3ASRModel`` at the
    ``from_pretrained`` + ``transcribe((audio, sr), language=...)``
    surface so ``QwenEngine`` can use it unchanged.
    """

    def __init__(self, model_path: str, *, prefer_quantized: bool = True) -> None:
        self.model_path = Path(model_path)
        self.prefer_quantized = prefer_quantized
        self._sessions: dict[str, Any] = {}
        self._embed_tokens: np.ndarray | None = None
        self._tokenizer: Any = None
        self._hidden_size = 2048  # 1.7B default; refined from config.json

    # ── Loading ───────────────────────────────────────────────────────

    def _read_config(self) -> dict:
        config_path = self.model_path / "config.json"
        if config_path.is_file():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                log.warning("[QWEN-ONNX] config.json unreadable — using defaults", exc_info=True)
        return {}

    def from_pretrained(self, model_path: str | None = None) -> QwenOnnxModel:
        """Load all ONNX sessions + embed_tokens.bin + tokenizer.json.

        Mirrors ``qwen_asr.Qwen3ASRModel.from_pretrained`` so the swap
        point in ``QwenEngine.load()`` stays one line. Raises on any
        missing required file (fail-closed).
        """
        import onnxruntime as ort

        if model_path is not None:
            self.model_path = Path(model_path)
        base = self.model_path

        cfg = self._read_config()
        text_cfg = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
        hidden = text_cfg.get("hidden_size") if isinstance(text_cfg, dict) else None
        if isinstance(hidden, int) and hidden > 0:
            self._hidden_size = hidden

        paths = _resolve_onnx_paths(base, self.prefer_quantized)

        providers = [p for p in ort.get_available_providers() if p != "AzureExecutionProvider"]
        if not providers:
            providers = ["CPUExecutionProvider"]

        sessions: dict[str, Any] = {}
        for key, path in paths.items():
            try:
                sessions[key] = ort.InferenceSession(str(path), providers=providers)
            except Exception as exc:  # noqa: BLE001 — surface with file context
                raise RuntimeError(f"Qwen3-ASR ONNX failed to load {path.name}: {exc}") from exc
        self._sessions = sessions

        embed_path = base / "embed_tokens.bin"
        if not embed_path.is_file():
            raise FileNotFoundError(f"Qwen3-ASR ONNX model missing embed_tokens.bin in {base}")
        self._embed_tokens = _load_embed_tokens(embed_path, self._hidden_size)

        tok_path = base / "tokenizer.json"
        if not tok_path.is_file():
            raise FileNotFoundError(f"Qwen3-ASR ONNX model missing tokenizer.json in {base}")
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(tok_path))

        log.info(
            "[QWEN-ONNX] sessions loaded: encoder=%s decoder_init=%s decoder_step=%s "
            "(hidden=%d, providers=%s)",
            paths["encoder"].name,
            paths["decoder_init"].name,
            paths["decoder_step"].name,
            self._hidden_size,
            providers,
        )
        return self

    # ── Inference ─────────────────────────────────────────────────────

    def _run_encoder(self, mel: np.ndarray) -> np.ndarray:
        """``[1, 128, T]`` mel → ``[1, enc_len, hidden]`` audio features."""
        (audio_features,) = self._sessions["encoder"].run(
            ["audio_features"],
            {"mel": mel.astype(np.float32)},
        )
        return np.asarray(audio_features)

    def _run_decoder_init(
        self,
        input_ids: np.ndarray,
        position_ids: np.ndarray,
        audio_features: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Prefill: prompt ids + audio features → logits + KV cache."""
        # Detect the decoder format from the session's input names
        # (v3 = input_ids; v1 = input_embeds) — the reference inference.py
        # does the same so both export generations are supported.
        init_input_names = {i.name for i in self._sessions["decoder_init"].get_inputs()}
        if "input_ids" in init_input_names:
            audio_start, _ = _audio_pad_range(input_ids.reshape(-1).tolist())
            outputs = self._sessions["decoder_init"].run(
                ["logits", "present_keys", "present_values"],
                {
                    "input_ids": input_ids,
                    "position_ids": position_ids,
                    "audio_features": audio_features.astype(np.float32),
                    "audio_offset": np.array([audio_start], dtype=np.int64),
                },
            )
            return (np.asarray(outputs[0]), np.asarray(outputs[1]), np.asarray(outputs[2]))

        # v1 format: embed the prompt ourselves and scatter audio features.
        embed = self._embed_tokens
        if embed is None:
            raise RuntimeError("embed_tokens.bin not loaded")
        prompt_ids = input_ids.reshape(-1).tolist()
        input_embeds = embed[prompt_ids].copy()
        audio_start, audio_end = _audio_pad_range(prompt_ids)
        if audio_features.shape[1] != (audio_end - audio_start):
            raise ValueError(
                f"Audio feature length {audio_features.shape[1]} != "
                f"audio_pad count {audio_end - audio_start}"
            )
        input_embeds[audio_start:audio_end] = audio_features[0]
        input_embeds = input_embeds[np.newaxis, :, :]
        outputs = self._sessions["decoder_init"].run(
            ["logits", "present_keys", "present_values"],
            {
                "input_embeds": input_embeds,
                "position_ids": position_ids,
            },
        )
        return (np.asarray(outputs[0]), np.asarray(outputs[1]), np.asarray(outputs[2]))

    def _run_decoder_step(
        self,
        token_embed: np.ndarray,
        pos: int,
        past_keys: np.ndarray,
        past_values: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Autoregressive step: embedded token + KV cache → logits + KV."""
        outputs = self._sessions["decoder_step"].run(
            ["logits", "present_keys", "present_values"],
            {
                "input_embeds": token_embed,
                "position_ids": np.array([[pos]], dtype=np.int64),
                "past_keys": past_keys,
                "past_values": past_values,
            },
        )
        return (np.asarray(outputs[0]), np.asarray(outputs[1]), np.asarray(outputs[2]))

    def _greedy_decode(
        self,
        audio_features: np.ndarray,
        prompt_ids: list[int],
    ) -> list[int]:
        """Greedy decode from the prefill logits until EOS (max 256)."""
        input_ids = np.array(prompt_ids, dtype=np.int64)[np.newaxis, :]
        position_ids = np.arange(len(prompt_ids), dtype=np.int64)[np.newaxis, :]

        logits, present_keys, present_values = self._run_decoder_init(
            input_ids, position_ids, audio_features
        )
        next_token = int(np.argmax(logits[0, -1, :]))
        output_tokens = [next_token]
        if next_token in _EOS_TOKEN_IDS:
            return output_tokens

        embed = self._embed_tokens
        if embed is None:
            raise RuntimeError("embed_tokens.bin not loaded")
        pos = len(prompt_ids)
        for _ in range(_MAX_DECODE_TOKENS - 1):
            token_embed = embed[next_token][np.newaxis, np.newaxis, :]
            logits, present_keys, present_values = self._run_decoder_step(
                token_embed, pos, present_keys, present_values
            )
            next_token = int(np.argmax(logits[0, -1, :]))
            output_tokens.append(next_token)
            pos += 1
            if next_token in _EOS_TOKEN_IDS:
                break
        return output_tokens

    def transcribe(
        self,
        audio_tuple: tuple[np.ndarray, int],
        language: str | None = None,
    ) -> list[Transcription]:
        """Transcribe a single audio array (Whisper-style tuple API).

        Matches ``qwen_asr.Qwen3ASRModel.transcribe((audio, sr),
        language=...)`` so ``QwenEngine``'s chunking / hallucination /
        abort plumbing works unchanged. ``language`` is accepted for API
        compatibility but not used — the ONNX prompt is language-agnostic
        (the model auto-detects; language forcing would need tokenizer
        access on the prompt, deferred per the export tool).
        """
        audio, sample_rate = audio_tuple
        if audio.size == 0:
            return [Transcription("")]

        if sample_rate != _MEL_SAMPLE_RATE:
            # Whisper backends always feed 16 kHz; resample defensively
            # via the shared helper (DRY — same as QwenEngine's contract).
            from voice_typer.server.recording.resampling import resample_audio

            audio = resample_audio(audio, sample_rate, _MEL_SAMPLE_RATE)

        mel = _log_mel_spectrogram(audio)
        audio_features = self._run_encoder(mel)

        audio_token_count = _get_feat_extract_output_lengths(mel.shape[2])
        if audio_features.shape[1] != audio_token_count:
            log.warning(
                "[QWEN-ONNX] encoder returned %d tokens, prompt expects %d — "
                "clamping prompt to encoder output",
                audio_features.shape[1],
                audio_token_count,
            )
            audio_token_count = audio_features.shape[1]

        prompt_ids = _build_prompt_ids(audio_token_count)
        token_ids = self._greedy_decode(audio_features, prompt_ids)

        if self._tokenizer is None:
            raise RuntimeError("tokenizer.json not loaded")
        text = self._tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        return [Transcription(text)]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Release ONNX sessions (frees the model memory)."""
        self._sessions.clear()
        self._embed_tokens = None
        self._tokenizer = None


def is_onnx_model_dir(model_path: str | Path) -> bool:
    """True if ``model_path`` holds the pre-exported Qwen3-ASR ONNX layout.

    Used by ``QwenEngine.load()`` to auto-select the ONNX backend when a
    user points ``qwen_model_path`` at an ONNX export directory
    (``encoder.onnx`` / ``encoder.int4.onnx`` + embed_tokens.bin +
    tokenizer.json are the required files).
    """
    base = Path(model_path)
    if not base.is_dir():
        return False
    encoder = base / "encoder.onnx"
    if not encoder.is_file():
        encoder = base / "encoder.int4.onnx"
    return encoder.is_file() and (base / "embed_tokens.bin").is_file() and (
        base / "tokenizer.json"
    ).is_file()
