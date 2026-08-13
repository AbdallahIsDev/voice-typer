# ruff: noqa: A001, A002, N802, N803, N816
# RW-6 (pyrefly): stub for the `onnx_asr` package (ONNX ASR engine).
#
# `onnx_asr` is an optional pip dependency used by
# `voice_typer/server/parakeet_engine.py`. It is NOT installed on the
# CI runner (or in most dev environments — only users who want the
# ONNX Parakeet backend install it). The runtime code wraps every
# `import onnx_asr` in a lazy `_ensure_imports()` call guarded by
# `try/except ImportError`, so this stub only needs to declare the
# small import surface actually used:
#
#   - `onnx_asr.Model(name, *, quantization=None, providers=None, onnx_dir=None)`
#     -> model instance
#   - `model.recognize(audio, sample_rate=16000)` -> str (or list[str]
#     if a list of audios is passed)
#
# Constructor signature follows PLAN_ONNX_INTEGRATION.md §3.3 (Option
# B-1) — class-based API (NOT `load_model(...)`). The `providers` arg
# is forwarded to `onnxruntime.InferenceSession`'s `providers` kwarg.
# `quantization` selects the ONNX export variant: `"fp16"` for the
# FP16 export (`grikdotnet/parakeet-tdt-0.6b-fp16`), `"int8"` for the
# INT8 quantised variant, `None` for the FP32 default.
#
# All symbols are typed `Any` because the surrounding code is wrapped
# in `try/except ImportError` and we do not need pyrefly to verify the
# onnx_asr call sites.
from typing import Any

class Model:
    """ONNX ASR model wrapper (see `onnx-asr` library docs)."""

    def __init__(
        self,
        model_name: str,
        *,
        quantization: str | None = ...,
        providers: list[str] | None = ...,
        onnx_dir: str | None = ...,
        **kwargs: Any,
    ) -> None: ...

    def recognize(
        self,
        audio: Any,
        *,
        sample_rate: int = ...,
        language: str | None = ...,
        **kwargs: Any,
    ) -> str: ...

    def recognize_batch(
        self,
        audio_batch: list[Any],
        *,
        sample_rate: int = ...,
        language: str | None = ...,
        **kwargs: Any,
    ) -> list[str]: ...

    def release(self) -> None: ...


__all__: list[str] = ["Model"]
