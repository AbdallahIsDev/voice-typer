"""Microphone test recording — thin facade over level_monitor.

This module is kept for backward compatibility with existing IPC routes
in ipc_server.py.  All stream management is now handled by
:mod:`voice_typer.server.level_monitor` which opens a SINGLE
sounddevice InputStream for both continuous level monitoring AND
ad-hoc test recordings — eliminating the PortAudio device conflict
that occurred on Windows when two streams tried to open the same
device simultaneously.

All public functions delegate to level_monitor counterparts.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


def is_test_active() -> bool:
    """Return True if a microphone test is currently recording."""
    from voice_typer.server.level_monitor import is_test_active as _lm_is_active
    return _lm_is_active()


def get_level() -> dict:
    """Return the current audio level.

    Always returns the level monitor's reading (smoothed RMS/peak),
    since the test uses the same stream.
    """
    from voice_typer.server.level_monitor import get_level as _lm_get_level
    return _lm_get_level()


def start_test(
    mic_id: Optional[str] = None,
    duration: float = 10.0,
    filters: Optional[dict] = None,
) -> dict:
    """Start a microphone test recording.

    Delegates to level_monitor.start_test_recording() which uses the
    single existing InputStream — no new PortAudio stream is opened.

    Args:
        mic_id: Device index string or None for system default.
        duration: Recording duration in seconds (default 10).
        filters: Optional dict of audio enhancement filter overrides.

    Returns:
        dict with success, message, duration, sample_rate.
    """
    from voice_typer.server.level_monitor import start_test_recording
    return start_test_recording(mic_id=mic_id, duration=duration, filters=filters)


def stop_test() -> dict:
    """Stop the test recording and return captured audio as base64 WAV.

    Delegates to level_monitor.stop_test_recording().
    """
    from voice_typer.server.level_monitor import stop_test_recording
    return stop_test_recording()


def cancel_test() -> dict:
    """Cancel a running test without returning audio."""
    from voice_typer.server.level_monitor import cancel_test_recording
    return cancel_test_recording()
