# ruff: noqa: A001, A002, N802, N803, N816
# PYREFLY-001: stub for `webrtc_audio_processing` (Linux alt to RNNoise).
#
# NOTE: voice-typer does not currently import this module. The stub is
# provided as forward-compat scaffolding so a future Linux-only audio
# filter backend that does `import webrtc_audio_processing` will not
# regress the pyrefly `missing-import` count. If/when the module is
# actually imported, grep the codebase for the exact import surface
# and tighten these signatures.
from typing import Any

class AudioProcessingModule:
    def __init__(self, **kwargs: Any) -> None: ...
    def ApplyConfig(self, config: Any) -> None: ...
    def ProcessStream(self, stream: Any) -> Any: ...
    def ProcessReverseStream(self, stream: Any) -> Any: ...
    def set_stream_delay_ms(self, delay: int) -> None: ...
    def get_stream_delay_ms(self) -> int: ...
    def Initialize(self) -> None: ...
    def Reset(self) -> None: ...

class Config:
    def __init__(self) -> None: ...
    high_pass_filter: Any
    echo_cancellation: Any
    noise_suppression: Any
    gain_controller: Any
    voice_detection: Any

__all__: list[str]
