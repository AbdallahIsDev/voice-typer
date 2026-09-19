"""Tests for ``_audio_to_wav_bytes`` helper and the cleanup of lazy"""

from __future__ import annotations

import inspect
import io
import os
import wave

import numpy as np
import pytest
from voice_typer.server import cloud_engines
from voice_typer.server.cloud import _engine

CLOUD_ENGINES_PATH = os.path.abspath(cloud_engines.__file__)
ENGINE_PATH = os.path.abspath(_engine.__file__)


def test_audio_to_wav_bytes_empty_input_is_44_bytes() -> None:
    """An empty float32 array must produce exactly the 44-byte WAV header"""
    empty_wav = cloud_engines._audio_to_wav_bytes(np.zeros(0, dtype=np.float32))
    assert isinstance(empty_wav, bytes)
    assert len(empty_wav) == 44, f"expected 44-byte WAV header, got {len(empty_wav)} bytes"


def test_audio_to_wav_bytes_empty_input_is_valid_wav() -> None:
    """The 44-byte header must be openable by ``wave.open`` and report"""
    empty_wav = cloud_engines._audio_to_wav_bytes(np.zeros(0, dtype=np.float32))
    with wave.open(io.BytesIO(empty_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # 16-bit
        assert wf.getnframes() == 0


def test_audio_to_wav_bytes_empty_input_48k_byte_identical_to_hand_crafted() -> None:
    """magic bytes (RIFF...WAVEfmt ...data) at 48000 Hz. The helper, called"""
    helper_wav = cloud_engines._audio_to_wav_bytes(np.zeros(0, dtype=np.float32), sample_rate=48000)
    hand_crafted = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00"
        b"\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    assert helper_wav == hand_crafted
    # Sanity: the 48k variant is also a valid 44-byte WAV.
    assert len(helper_wav) == 44
    with wave.open(io.BytesIO(helper_wav), "rb") as wf:
        assert wf.getframerate() == 48000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 0


def _read_facade_source() -> str:
    with open(CLOUD_ENGINES_PATH, encoding="utf-8") as fh:
        return fh.read()


def _read_engine_source() -> str:
    with open(ENGINE_PATH, encoding="utf-8") as fh:
        return fh.read()


def test_test_connection_deepgram_branch_uses_helper() -> None:
    """The Deepgram branch of ``test_connection`` must obtain its empty"""
    src = _read_engine_source()
    # The method body must be present.
    assert "def test_connection" in src, "test_connection method not found"
    # Slice the source to the test_connection method body.
    method = inspect.getsource(cloud_engines.CloudEngine.test_connection)
    assert "_audio_to_wav_bytes" in method, "test_connection must call _audio_to_wav_bytes for the Deepgram branch"
    # The hand-crafted magic bytes literal must NOT appear in the method.
    assert b"RIFF" not in method.encode(), "test_connection still contains hand-crafted RIFF magic bytes"


def test_no_hand_crafted_wav_magic_bytes_in_module() -> None:
    """truth for WAV encoding."""
    src = _read_engine_source()
    hand_crafted = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00"
        b"\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    assert hand_crafted not in src.encode(), "hand-crafted WAV magic bytes are still present in cloud_engines.py"


def test_stdlib_imports_hoisted_to_module_top() -> None:
    """``import time``, ``import wave`` and ``from datetime import ...``"""
    with open(CLOUD_ENGINES_PATH, encoding="utf-8") as fh:
        head = fh.read().splitlines()[:30]
    head_text = "\n".join(head)

    assert "import time" in head_text, "module-top `import time` missing"
    assert "import wave" in head_text, "module-top `import wave` missing"
    assert "from datetime import" in head_text, "module-top `from datetime import` missing"

    # The lazy aliases must NOT survive anywhere in the file.
    full_src = _read_facade_source()
    assert "import time as _time" not in full_src, "lazy `import time as _time` still present"
    # `import wave` may only appear once, at module top. A second
    assert full_src.count("import wave") == 1, (
        f"expected exactly one `import wave` (module-top); found {full_src.count('import wave')}"
    )
    # `from datetime import datetime, timezone` may only appear once.
    assert full_src.count("from datetime import datetime, timezone") == 1, (
        "expected exactly one `from datetime import datetime, timezone` "
        "(module-top); found "
        f"{full_src.count('from datetime import datetime, timezone')}"
    )


def test_module_exports_time_wave_datetime() -> None:
    """Sanity: after hoisting, ``time``, ``wave``, ``datetime`` and"""
    import datetime as _dt  # noqa: F401  (for isinstance check)

    assert cloud_engines.time is not None
    assert cloud_engines.wave is not None
    assert cloud_engines.datetime is _dt.datetime
    assert cloud_engines.timezone is _dt.timezone


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
