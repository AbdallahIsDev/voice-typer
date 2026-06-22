"""Shared autouse fixture: mock heavy imports so all tests run headless.

Modules mocked: sounddevice, faster_whisper, pynput, pystray, PIL,
pyperclip.

TEST-003: the autouse mock is now conditional — tests that need real
pynput (e.g. to test the actual keyboard listener) can use the
``@pytest.mark.real_pynput`` marker to opt out of the pynput mock.
"""

import sys
import pytest
from unittest.mock import MagicMock


def pytest_configure(config):
    """TEST-003: register the real_pynput marker."""
    config.addinivalue_line(
        "markers",
        "real_pynput: opt out of the pynput mock (use real pynput.keyboard)",
    )


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch, request):
    """Mock all hardware/GUI dependencies so tests run headless.

    TEST-003: tests marked with @pytest.mark.real_pynput will NOT
    have pynput mocked, so they can test the real keyboard listener.
    """
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

    mock_whisper = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    # TEST-003: only mock pynput if the test doesn't request real pynput
    if not request.node.get_closest_marker("real_pynput"):
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
