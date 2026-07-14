# ruff: noqa: A001, A002, N802, N803, N816
# RW-6 (pyrefly): stub for the `qwen_asr` package (optional ASR engine).
#
# `qwen_asr` is an optional pip dependency used by
# `voice_typer/server/qwen_engine.py`. It is NOT installed on the CI
# runner (or in most dev environments — only users who want the Qwen3
# ASR engine install it). The runtime code wraps every `import qwen_asr`
# in `try/except ImportError`, so this stub only needs to declare the
# small import surface actually used:
#
#   - `Qwen3ASRModel.from_pretrained(path)` -> model instance
#   - `model.transcribe((audio, sample_rate), language=...)` -> list of
#     transcription objects with a `.text` attribute
#   - `model.to("cpu" | "cuda")` for device migration
#
# All symbols are typed `Any` because the surrounding code is wrapped
# in `try/except ImportError` and we do not need pyrefly to verify the
# qwen_asr call sites.
from typing import Any

class Qwen3ASRModel:
    @classmethod
    def from_pretrained(cls, path: Any, *args: Any, **kwargs: Any) -> Qwen3ASRModel: ...
    def transcribe(self, audio: Any, *args: Any, **kwargs: Any) -> list: ...
    def to(self, device: Any) -> None: ...
    def eval(self) -> None: ...
    def train(self, mode: bool = ...) -> None: ...

__all__: list[str]
