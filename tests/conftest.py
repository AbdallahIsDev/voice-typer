"""Shared autouse fixture: mock heavy imports so all tests run headless.

Modules mocked: sounddevice, faster_whisper, pynput, pystray, PIL,
pyperclip, flet.
"""

import sys
import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock all hardware/GUI dependencies so tests run headless."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

    mock_whisper = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    mock_pynput = MagicMock()
    mock_pynput_kb = MagicMock()
    monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

    mock_pystray = MagicMock()
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())

    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Prevent atexit handler from polluting test output
    try:
        monkeypatch.setattr("voice_typer.server.app.atexit.register", lambda *a, **kw: None)
    except Exception:
        pass
