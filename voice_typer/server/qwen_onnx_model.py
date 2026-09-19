"""Qwen3-ASR ONNX Runtime model wrapper."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("voice_typer.server.qwen_onnx")

_MEL_SAMPLE_RATE = 16000
_MEL_N_FFT = 400
_MEL_HOP = 160
_MEL_N_BINS = 128
_MEL_FMIN = 0.0
_MEL_FMAX = 8000.0

# ─── Special token IDs (shared across all Qwen3-ASR sizes; validated by
_ENDOFTEXT_TOKEN_ID = 151643  # <|endoftext|>, pad, also EOS
_IM_START_TOKEN_ID = 151644  # <|im_start|>
_IM_END_TOKEN_ID = 151645  # <|im_end|>, also EOS
_AUDIO_START_TOKEN_ID = 151669  # <|audio_start|>
_AUDIO_END_TOKEN_ID = 151670  # <|audio_end|>
_AUDIO_PAD_TOKEN_ID = 151676  # <|audio_pad|>, replaced by encoder output
_EOS_TOKEN_IDS = frozenset({_ENDOFTEXT_TOKEN_ID, _IM_END_TOKEN_ID})

# Hardcoded subword encodings of the prompt scaffolding ("system\\n",
_NEWLINE_TOKEN_ID = 198
_SYSTEM_TOKEN_IDS = (8948,)  # "system"
_USER_TOKEN_IDS = (872,)  # "user"
_ASSISTANT_TOKEN_IDS = (77091,)  # "assistant"

# Encoder windowing (from the export tool's encoder_wrapper.py):
_CONV_WINDOW = 100
_TOKENS_PER_WINDOW = 13

# Decoder greedily generates at most this many tokens.
_MAX_DECODE_TOKENS = 256


@dataclass
class Transcription:
    """Minimal ``ASRTranscription``-shaped result (``.text`` suffices)."""

    text: str


def _get_feat_extract_output_lengths(mel_frames: int) -> int:
    """Number of audio tokens the encoder produces for ``mel_frames``."""
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
    """Whisper-compatible log-mel ``[1, 128, T]`` (numpy/scipy only)."""
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    if audio.ndim != 1:
        audio = audio.reshape(-1)

    from faster_whisper.feature_extractor import FeatureExtractor

    # feature_size=128 + Whisper defaults gives exactly the Qwen3-ASR
    extractor = FeatureExtractor(
        feature_size=_MEL_N_BINS,
        sampling_rate=_MEL_SAMPLE_RATE,
        hop_length=_MEL_HOP,
        n_fft=_MEL_N_FFT,
    )
    # Reuse the static helpers so we control the frame drop ourselves
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
        raise ValueError(f"embed_tokens.bin size {raw.size} is not a multiple of hidden_size {hidden_size}")
    return raw.reshape(-1, hidden_size)


def _resolve_onnx_paths(model_dir: Path, prefer_quantized: bool) -> dict[str, Path]:
    """Resolve the encoder/decoder session paths, preferring int4 variants."""
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
    """ONNX Runtime Qwen3-ASR model (pre-exported files, ONNX-only)."""

    def __init__(self, model_path: str, *, prefer_quantized: bool = True) -> None:
        self.model_path = Path(model_path)
        self.prefer_quantized = prefer_quantized
        self._sessions: dict[str, Any] = {}
        self._embed_tokens: np.ndarray | None = None
        self._tokenizer: Any = None
        self._hidden_size = 2048  # 1.7B default; refined from config.json

    def _read_config(self) -> dict:
        config_path = self.model_path / "config.json"
        if config_path.is_file():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                log.warning("[QWEN-ONNX] config.json unreadable, using defaults", exc_info=True)
        return {}

    def from_pretrained(self, model_path: str | None = None) -> QwenOnnxModel:
        """Load all ONNX sessions + embed_tokens.bin + tokenizer.json."""
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
            except Exception as exc:  # noqa: BLE001, surface with file context
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
            "[QWEN-ONNX] sessions loaded: encoder=%s decoder_init=%s decoder_step=%s (hidden=%d, providers=%s)",
            paths["encoder"].name,
            paths["decoder_init"].name,
            paths["decoder_step"].name,
            self._hidden_size,
            providers,
        )
        return self

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
                f"Audio feature length {audio_features.shape[1]} != audio_pad count {audio_end - audio_start}"
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

        logits, present_keys, present_values = self._run_decoder_init(input_ids, position_ids, audio_features)
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
        """Transcribe a single audio array (Whisper-style tuple API)."""
        audio, sample_rate = audio_tuple
        if audio.size == 0:
            return [Transcription("")]

        if sample_rate != _MEL_SAMPLE_RATE:
            # Whisper backends always feed 16 kHz; resample defensively
            from voice_typer.server.recording.resampling import resample_audio

            audio = resample_audio(audio, sample_rate, _MEL_SAMPLE_RATE)

        mel = _log_mel_spectrogram(audio)
        audio_features = self._run_encoder(mel)

        audio_token_count = _get_feat_extract_output_lengths(mel.shape[2])
        if audio_features.shape[1] != audio_token_count:
            log.warning(
                "[QWEN-ONNX] encoder returned %d tokens, prompt expects %d, clamping prompt to encoder output",
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

    def close(self) -> None:
        """Release ONNX sessions (frees the model memory)."""
        self._sessions.clear()
        self._embed_tokens = None
        self._tokenizer = None


def is_onnx_model_dir(model_path: str | Path) -> bool:
    """True if ``model_path`` holds the pre-exported Qwen3-ASR ONNX layout."""
    base = Path(model_path)
    if not base.is_dir():
        return False
    encoder = base / "encoder.onnx"
    if not encoder.is_file():
        encoder = base / "encoder.int4.onnx"
    return encoder.is_file() and (base / "embed_tokens.bin").is_file() and (base / "tokenizer.json").is_file()
